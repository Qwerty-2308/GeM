"""Eligibility rules. Each rule checks one thing (Single Responsibility).

Rules are plain deterministic code. They never call an LLM. To add a check,
write a new class with `name` and `evaluate()` and add it to DEFAULT_RULES.
"""

from __future__ import annotations

from datetime import datetime

from ..domain.models import (
    BidAnalysis, CorrigendumStatus, FieldStatus, RuleResult, SellerProfile, Verdict,
)
from ..parsing.clause_patterns import DEFAULT_CLAUSE_PATTERNS

_BLOCKING_CATEGORIES = {p.category for p in DEFAULT_CLAUSE_PATTERNS if p.blocking_hint}


class ProfileRequiredRule:
    name = "profile_required"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        if profile is None:
            return [RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW,
                               reason="No seller profile saved yet, so eligibility can't be checked.")]
        return []


class DeadlineRule:
    """The bid document holds the original deadline, and a corrigendum can extend it — seen
    live on 11-Oct-2026, when three bids whose PDFs said they closed on 5-7 Oct were open
    until the 12th. So a passed deadline only blocks once GeM has confirmed there is no
    corrigendum; otherwise the bid needs a look, because skipping a live bid costs a bid."""

    name = "deadline"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        end = bid.found_value("bid_end")
        if end is None:
            return []  # missing critical fields are handled by CriticalFieldsRule
        field = bid.field("bid_end")
        if datetime.fromisoformat(end) > now:
            return []
        if bid.corrigendum.status is CorrigendumStatus.NONE:
            return [RuleResult(rule=self.name, outcome=Verdict.BLOCKED,
                               reason="Bid has already closed.", evidence=field.evidence)]
        return [RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW,
                           reason="The deadline in the bid document has passed, but it may have been extended "
                                  "by a corrigendum. Check the current deadline on GeM.",
                           evidence=field.evidence)]


class CriticalFieldsRule:
    name = "critical_fields"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        results = []
        for f in bid.fields:
            if not f.critical or f.status is FieldStatus.FOUND:
                continue
            reason = {
                FieldStatus.NOT_FOUND: f"Couldn't find “{f.label}” in the document. Check it by hand.",
                FieldStatus.UNCLEAR: f"“{f.label}” was found but couldn't be read reliably.",
                FieldStatus.CONFLICTING: f"“{f.label}” conflicts with a buyer clause. {f.note or ''}",
            }[f.status]
            results.append(RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW,
                                      reason=reason.strip(), evidence=f.evidence))
        return results


class ConflictingFieldsRule:
    """Non-critical fields can still conflict; surface those too."""

    name = "conflicts"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        return [
            RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW,
                       reason=f"“{f.label}” conflicts with a buyer clause. {f.note or ''}".strip(),
                       evidence=f.evidence)
            for f in bid.fields
            if not f.critical and f.status is FieldStatus.CONFLICTING
        ]


class RestrictiveClauseRule:
    name = "restrictive_clauses"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        return [
            RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW,
                       reason=f"{flag.label}: read this clause and confirm you qualify.",
                       evidence=flag.evidence)
            for flag in bid.flags
            if flag.category in _BLOCKING_CATEGORIES
        ]


class UnreadAttachmentRule:
    name = "unread_attachment"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        return [
            RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW,
                       reason="The buyer attached a separate terms document. Open it on GeM and read it; "
                              "this tool has not checked it.",
                       evidence=flag.evidence)
            for flag in bid.flags
            if flag.category == "unread_attachment"
        ]


class CorrigendumRule:
    """A corrigendum can move the deadline or change the specification, so every state
    except "GeM says there is none" is something the person must look at.

    NOT_CHECKED is deliberately not silent: a bid we could not check is a bid whose
    facts may already be out of date.
    """

    name = "corrigendum"

    _REASONS = {
        CorrigendumStatus.PRESENT:
            "This bid has a corrigendum. It can change the deadline, quantity, "
            "specification or terms — read it before relying on anything below.",
        CorrigendumStatus.NOT_CHECKED:
            "Not checked for a corrigendum. Open the bid on GeM and use "
            "“View Corrigendum/Representation” before you bid.",
        CorrigendumStatus.FAILED:
            "The corrigendum check did not complete, so this bid may have been amended "
            "without us seeing it. Check it on GeM.",
    }

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        reason = self._REASONS.get(bid.corrigendum.status)
        if reason is None:  # NONE: GeM told us there is no corrigendum
            return []
        note = f" {bid.corrigendum.note}" if bid.corrigendum.note else ""
        return [RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW, reason=f"{reason}{note}")]


class EmdRule:
    name = "emd"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        if bid.found_value("emd_required") != "Yes":
            return []
        field = bid.field("emd_required")
        note = " You're marked as an MSE; check whether the exemption applies." if profile and profile.is_mse else ""
        return [RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW,
                           reason=f"EMD is required for this bid.{note}", evidence=field.evidence)]


class ExperienceTurnoverExemptionRule:
    name = "experience_turnover_exemption"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        if profile is None:
            return []
        mse_exempt = bid.found_value("mse_exemption") == "Yes" and profile.is_mse
        startup_exempt = bid.found_value("startup_exemption") == "Yes" and profile.is_startup
        if mse_exempt or startup_exempt:
            return []
        if profile.annual_turnover_rupees is None or profile.years_experience is None:
            field = bid.field("mse_exemption")
            return [RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW,
                               reason="No experience/turnover exemption applies to you, and your profile is "
                                      "missing turnover or years of experience.",
                               evidence=field.evidence if field else None)]
        return []


class CategoryMatchRule:
    name = "category_match"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        if profile is None or not profile.categories:
            return []
        haystack = " ".join(filter(None, [
            bid.found_value("item_category"), bid.found_value("relevant_categories"),
        ])).lower()
        if any(c.lower() in haystack for c in profile.categories):
            return []
        field = bid.field("item_category")
        return [RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW,
                           reason="Item doesn't match any category in your profile.",
                           evidence=field.evidence if field else None)]


DEFAULT_RULES = (
    ProfileRequiredRule(),
    DeadlineRule(),
    CriticalFieldsRule(),
    ConflictingFieldsRule(),
    UnreadAttachmentRule(),
    CorrigendumRule(),
    RestrictiveClauseRule(),
    EmdRule(),
    ExperienceTurnoverExemptionRule(),
    CategoryMatchRule(),
)
