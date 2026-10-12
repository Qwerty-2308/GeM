"""Eligibility rules. Each rule checks one thing (Single Responsibility).

Rules are plain deterministic code. They never call an LLM. To add a check,
write a new class with `name` and `evaluate()` and add it to DEFAULT_RULES.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..domain.models import (
    BidAnalysis, CorrigendumStatus, FieldStatus, RuleResult, SellerProfile, Verdict, mentions,
)
from ..parsing.clause_patterns import DEFAULT_CLAUSE_PATTERNS

_BLOCKING_CATEGORIES = {p.category for p in DEFAULT_CLAUSE_PATTERNS if p.blocking_hint}


def inr(rupees: int) -> str:
    """25500000 -> '₹2,55,00,000' (Indian digit grouping)."""
    digits = str(rupees)
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return "₹" + ",".join(part for part in (head, *groups, tail) if part)


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
    corrigendum, and confirmed it after that deadline (an earlier answer cannot know about a
    later extension). Otherwise the bid needs a look: skipping a live bid costs a bid."""

    name = "deadline"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        end = bid.found_value("bid_end")
        if end is None:
            return []  # missing critical fields are handled by CriticalFieldsRule
        field = bid.field("bid_end")
        deadline = datetime.fromisoformat(end)
        if deadline > now:
            return []
        checked_at = bid.corrigendum.checked_at
        if bid.corrigendum.status is CorrigendumStatus.NONE and checked_at is not None and checked_at >= deadline:
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
        amount = bid.found_value("emd_amount")
        what = f"An EMD of {inr(int(amount))}" if amount else "EMD"
        note = " You're marked as an MSE; check whether the exemption applies." if profile and profile.is_mse else ""
        return [RuleResult(rule=self.name, outcome=Verdict.NEEDS_REVIEW,
                           reason=f"{what} is required for this bid.{note}", evidence=field.evidence)]


@dataclass(frozen=True)
class _Requirement:
    field: str              # the bid's stated minimum
    profile_attr: str       # the seller's figure
    mse_relaxation: str
    startup_relaxation: str
    what: str
    show: Callable[[int], str]


class TurnoverExperienceRule:
    """Compares the bid's stated minimum turnover and experience with the seller profile.

    Blocks only on a clear shortfall that no relaxation covers. Whatever it cannot decide — a
    partial relaxation, a relaxation it could not read, a profile without the figure — is a
    review, never a block: skipping a bid the seller could win costs a bid."""

    name = "turnover_experience"

    _REQUIREMENTS = (
        _Requirement("min_avg_turnover", "annual_turnover_rupees", "mse_turnover_relaxation",
                     "startup_turnover_relaxation", "average annual turnover (last 3 years)", inr),
        _Requirement("min_experience_years", "years_experience", "mse_experience_relaxation",
                     "startup_experience_relaxation", "years of experience", lambda n: f"{n} years"),
    )

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        if profile is None:
            return []
        return [r for req in self._REQUIREMENTS if (r := self._check(req, bid, profile)) is not None]

    def _check(self, req: _Requirement, bid: BidAnalysis, profile: SellerProfile) -> RuleResult | None:
        field = bid.field(req.field)
        if field is None or field.status is FieldStatus.NOT_FOUND:
            return None
        if field.status is not FieldStatus.FOUND:
            return self._result(Verdict.NEEDS_REVIEW, f"The bid states a minimum {req.what} that couldn't be "
                                                      "read reliably. Check it in the document.", field)
        required = int(field.value)
        applicable = [name for name, applies in ((req.mse_relaxation, profile.is_mse),
                                                 (req.startup_relaxation, profile.is_startup)) if applies]
        relaxations = {bid.found_value(name) for name in applicable}  # None = not found or unreadable
        if "Yes" in relaxations:
            return None
        have = getattr(profile, req.profile_attr)
        if have is None:
            return self._result(Verdict.NEEDS_REVIEW, f"The bid requires a minimum {req.what} of "
                                f"{req.show(required)}. Add yours to the seller profile to check it.", field)
        if have >= required:
            return None
        shortfall = f"The bid requires a minimum {req.what} of {req.show(required)}; your profile says {req.show(have)}."
        if relaxations - {"No"}:  # partial, or a relaxation for your category we couldn't read
            return self._result(Verdict.NEEDS_REVIEW, f"{shortfall} A relaxation may apply to you (partial, or "
                                                      "not readable here). Check the bid's terms.", field)
        return self._result(Verdict.BLOCKED, shortfall, field)

    def _result(self, outcome: Verdict, reason: str, field) -> RuleResult:
        return RuleResult(rule=self.name, outcome=outcome, reason=reason, evidence=field.evidence)


class CategoryMatchRule:
    name = "category_match"

    def evaluate(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> list[RuleResult]:
        if profile is None or not profile.categories:
            return []
        haystack = " ".join(filter(None, [
            bid.found_value("item_category"), bid.found_value("relevant_categories"),
        ]))
        if any(mentions(haystack, c) for c in profile.categories):
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
    TurnoverExperienceRule(),
    CategoryMatchRule(),
)
