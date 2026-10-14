"""What the chatbot may do, as tools over the existing pipeline.

Each call returns `data` for the model and, where there is something to show, a `card` the page
renders from the same data, so every figure the seller sees comes from the pipeline with its page
and quote, not from the model's wording.

Bid text is untrusted and reaches the model through these results. So the tools are limited to
what is harmless if a bid's text talks the model into calling them: profile changes are only
proposed (the page asks him to confirm), searches and downloads go through SafeFetcher's caps, and
there is no tool to reset the safety stop or change the contact email.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..domain.models import FieldStatus, SellerProfile, Verdict
from ..infrastructure.pypdf_extractor import InvalidPdf
from ..safety.errors import SafetyError, UrlNotAllowed
from .bid_fetch_service import BadBidLink
from .bid_import_service import EvidenceMismatch
from .discovery_service import DiscoveryNotAllowed

if TYPE_CHECKING:
    from ..container import Container

_QUOTE_FOR_MODEL = 160  # the card shows the whole quote


@dataclass(frozen=True)
class ToolResult:
    data: dict[str, Any]
    card: dict[str, Any] | None = None


def _tool(name: str, description: str, properties: dict | None = None, required: tuple[str, ...] = ()) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": list(required)}}}


_LIST = {"type": "array", "items": {"type": "string"}}
DEFINITIONS = [
    _tool("get_profile", "The seller's saved profile: products, words to ignore, turnover, experience, MSE status."),
    _tool("propose_profile_update",
          "Propose changes to the seller's profile. Nothing is saved: he sees the change and confirms it "
          "himself. Pass only the fields that change.",
          {"products": {**_LIST, "description": "Products he sells, as GeM names them, e.g. 'office chair'"},
           "ignore_words": {**_LIST, "description": "Items naming these never count, e.g. 'repair', 'AMC'"},
           "annual_turnover_rupees": {"type": "integer", "description": "Average annual turnover in rupees"},
           "years_experience": {"type": "integer"},
           "is_mse": {"type": "boolean"}, "is_startup": {"type": "boolean"}, "is_oem": {"type": "boolean"},
           "business_name": {"type": "string"},
           "bid_order": {"type": "string", "enum": ["match", "closing"],
                         "description": "Download first: bids made mostly of his products, or closing soonest"}}),
    _tool("search_gem", "Start one search of GeM's public bid list for his products. It takes a few minutes; "
                        "the page shows the results by itself. Searches are at least 15 minutes apart."),
    _tool("search_status", "Whether a search is running, how the last one ended, and the bids of his products "
                           "it found."),
    _tool("list_bids", "Bids already downloaded and checked, with the verdict for each."),
    _tool("get_bid", "One checked bid in full: verdict, every reason with its page and quote, and the "
                     "values read from the document.", {"bid_id": {"type": "integer"}}, ("bid_id",)),
    _tool("add_bid_from_link", "Download and check one bid from its GeM link "
                               "(https://bidplus.gem.gov.in/showbidDocument/<number>).",
          {"link": {"type": "string"}}, ("link",)),
    _tool("safety_status", "Whether fetching from GeM is stopped for safety, and today's request count."),
]

_PROFILE_FIELDS = {"products": "categories", "ignore_words": "ignore_words",
                   "annual_turnover_rupees": "annual_turnover_rupees", "years_experience": "years_experience",
                   "is_mse": "is_mse", "is_startup": "is_startup", "is_oem": "is_oem",
                   "business_name": "business_name", "bid_order": "bid_order"}


class ChatTools:
    definitions = DEFINITIONS

    def __init__(self, container: "Container"):
        self._c = container

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"_{name}", None)
        if name not in {d["function"]["name"] for d in DEFINITIONS} or handler is None:
            return ToolResult({"error": f"There is no tool called {name}."})
        try:
            return handler(**args)
        except TypeError as error:  # arguments the tool doesn't take
            return ToolResult({"error": f"Wrong arguments for {name}: {error}"})

    # ---- profile ----
    def _get_profile(self) -> ToolResult:
        profile = self._c.profiles.get()
        if profile is None:
            return ToolResult({"profile": None, "note": "No profile saved yet."})
        return ToolResult({"profile": profile.model_dump(mode="json", exclude={"updated_at"})})

    def _propose_profile_update(self, **changes: Any) -> ToolResult:
        unknown = set(changes) - set(_PROFILE_FIELDS)
        if unknown:
            return ToolResult({"error": f"Not profile fields: {', '.join(sorted(unknown))}"})
        current = (self._c.profiles.get() or SellerProfile()).model_dump(exclude={"updated_at"})
        try:
            proposed = SellerProfile(**{**current, **{_PROFILE_FIELDS[k]: v for k, v in changes.items()}})
        except ValidationError as error:
            return ToolResult({"error": f"That change isn't possible: {error.errors()[0]['msg']}"})
        return ToolResult(
            {"status": "Shown to him with a Confirm button; not saved until he clicks it.", "changes": changes},
            {"type": "profile_proposal", "changes": changes,
             "profile": proposed.model_dump(mode="json", exclude={"updated_at"})})

    # ---- search ----
    def _search_gem(self) -> ToolResult:
        try:
            self._c.discovery.start_in_background(trigger="chat")
        except DiscoveryNotAllowed as error:
            return ToolResult({"started": False, "reason": str(error)})
        return ToolResult({"started": True, "note": "Takes a few minutes; the page shows the results when done."},
                          {"type": "search"})

    def _search_status(self) -> ToolResult:
        status = self._c.discovery.status()
        last = status.last_run
        mine = [{"bid_number": e.bid_number, "items": (e.category or "")[:200],
                 "your_items": f"{e.matched_items} of {e.total_items}",
                 "closes_at": e.ends_at.isoformat() if e.ends_at else None,
                 "checked": e.doc_status.value, "note": e.doc_note, "bid_id": e.bid_id}
                for e in status.listings if e.matched_items][:20]
        return ToolResult({"running": status.running, "searches_for": status.keywords,
                           "last_search": {"outcome": last.outcome, "message": last.message,
                                           "started_at": last.started_at.isoformat()} if last else None,
                           "his_products_found": mine},
                          {"type": "listings", "listings": mine} if mine else None)

    # ---- bids ----
    def _list_bids(self) -> ToolResult:
        bids = [{"id": b.bid.id, "bid_number": b.bid.bid_number,
                 "items": (b.bid.found_value("item_category") or "")[:200],
                 "buyer": b.bid.found_value("organisation") or b.bid.found_value("ministry"),
                 "closes_at": b.bid.deadline().isoformat() if b.bid.deadline() else None,
                 "verdict": b.assessment.verdict.value, "things_to_check": b.assessment.review_count}
                for b in self._c.bid_query.list()][:30]
        return ToolResult({"bids": bids}, {"type": "bid_list", "bids": bids} if bids else None)

    def _get_bid(self, bid_id: int) -> ToolResult:
        item = self._c.bid_query.get(bid_id)
        if item is None:
            return ToolResult({"error": f"No checked bid with id {bid_id}."})
        bid = item.bid

        def ev(evidence, full: bool) -> dict:
            if evidence is None:
                return {}
            quote = evidence.quote if full else evidence.quote[:_QUOTE_FOR_MODEL]
            return {"page": evidence.page, "quote_from_bid_document": quote}

        card = {
            "type": "bid", "id": bid.id, "bid_number": bid.bid_number, "verdict": item.assessment.verdict.value,
            "reasons": [{"check": r.rule, "outcome": r.outcome.value, "reason": r.reason, **ev(r.evidence, True)}
                        for r in item.assessment.results],
            "fields": [{"name": f.name, "label": f.label, "status": f.status.value, "value": f.value,
                        **ev(f.evidence, True)} for f in bid.fields],
            "corrigendum": bid.corrigendum.status.value,
        }
        # For the model, only what it needs to answer: Groq counts every token (8,000 a minute on a
        # free key) and this goes back with each round. The card above carries the full quotes.
        for_model = {
            "id": bid.id, "bid_number": bid.bid_number, "verdict": item.assessment.verdict.value,
            "corrigendum": bid.corrigendum.status.value,
            "to_check": [{"outcome": r.outcome.value, "reason": r.reason, **ev(r.evidence, False)}
                         for r in item.assessment.results if r.outcome is not Verdict.ELIGIBLE],
            "passed_checks": [r.rule for r in item.assessment.results if r.outcome is Verdict.ELIGIBLE],
            "values": {f.label: [f.value, f.evidence.page] for f in bid.fields
                       if f.status is FieldStatus.FOUND and f.evidence},
            "not_found": [f.label for f in bid.fields if f.status is FieldStatus.NOT_FOUND],
            "conflicting": [f.label for f in bid.fields if f.status is FieldStatus.CONFLICTING],
        }
        return ToolResult(for_model, card)

    def _add_bid_from_link(self, link: str) -> ToolResult:
        try:
            result = self._c.bid_fetch.fetch_from_link(link)
        except (BadBidLink, UrlNotAllowed) as error:
            return ToolResult({"error": f"Only GeM bid links work here: {error}"})
        except SafetyError as error:  # stopped, blocked, budget used up
            return ToolResult({"error": f"Fetching from GeM isn't possible right now: {error}"})
        except (InvalidPdf, EvidenceMismatch) as error:
            return ToolResult({"error": f"Downloaded, but it couldn't be read: {error}"})
        except Exception as error:  # network trouble; the safety layer logged it, nothing is retried
            return ToolResult({"error": f"Couldn't reach GeM ({type(error).__name__})."})
        return self._get_bid(result.import_result.id)

    # ---- safety ----
    def _safety_status(self) -> ToolResult:
        s = self._c.safety.status()
        data = {"fetching_stopped": s.breaker.open, "stop_reason": s.breaker.reason,
                "requests_today": s.used_today, "daily_limit": s.daily_limit,
                "contact_email_set": bool(s.contact_email)}
        if s.breaker.open:
            return ToolResult(data, {"type": "safety_stop", "reason": s.breaker.reason})
        if not s.contact_email:
            return ToolResult(data, {"type": "contact_needed"})
        return ToolResult(data)
