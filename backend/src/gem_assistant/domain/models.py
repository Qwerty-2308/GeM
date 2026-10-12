"""Domain models. No I/O here — just data and the invariants that protect it."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal, Sequence

from pydantic import BaseModel, Field, model_validator

_WORD = re.compile(r"[a-z0-9]+")


def _stems(text: str) -> set[str]:
    # ponytail: drops one trailing "s" only; "boxes" vs "box" still differ. Add a stemmer if it bites.
    return {w.removesuffix("s") for w in _WORD.findall(text.lower())}


def _names(item: str, keyword: str) -> bool:
    """Every word of `keyword` is a word of `item`, ignoring case and a plural s.
    "pen" is not in "Open Racks"; "chairs" is in "Office Chair"."""
    words = _stems(keyword)
    return bool(words) and words <= _stems(item)


def item_match(text: str | None, keywords: Sequence[str], ignore: Sequence[str] = ()) -> tuple[int, int]:
    """(items that name one of `keywords` and none of `ignore`, distinct items) in a
    comma-separated GeM item list. Matching item by item keeps "Office Tables, Chair"
    from counting as an office chair."""
    # ponytail: GeM separates items with commas but a few item names hold commas too,
    # which only makes a basket look bigger than it is.
    items = list(dict.fromkeys(i.strip() for i in (text or "").split(",") if i.strip()))
    hits = sum(1 for i in items
               if any(_names(i, k) for k in keywords) and not any(_names(i, w) for w in ignore))
    return hits, len(items)


class FieldStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    UNCLEAR = "UNCLEAR"
    CONFLICTING = "CONFLICTING"


class Verdict(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"

    @property
    def severity(self) -> int:
        return {"ELIGIBLE": 0, "NEEDS_REVIEW": 1, "BLOCKED": 2}[self.value]


class ExtractionMethod(str, Enum):
    LABEL_PARSE = "LABEL_PARSE"
    PATTERN_SCAN = "PATTERN_SCAN"
    LLM = "LLM"


class Evidence(BaseModel):
    """Where a value came from. Every reported value must carry one."""

    page: int = Field(ge=1)
    quote: str = Field(min_length=1)
    method: ExtractionMethod


class PageText(BaseModel):
    number: int = Field(ge=1)
    text: str
    unread_lines: int = 0


class ExtractedField(BaseModel):
    name: str
    label: str
    status: FieldStatus
    value: str | None = None
    evidence: Evidence | None = None
    critical: bool = False
    note: str | None = None

    @model_validator(mode="after")
    def _evidence_or_nothing(self) -> "ExtractedField":
        if self.status is FieldStatus.FOUND and (self.value is None or self.evidence is None):
            raise ValueError(f"{self.name}: FOUND requires both a value and evidence")
        if self.status is FieldStatus.NOT_FOUND and self.value is not None:
            raise ValueError(f"{self.name}: NOT_FOUND must not carry a value")
        return self


class ClauseFlag(BaseModel):
    """A buyer clause that matched a warning pattern. A prompt to read, not a decision."""

    category: str
    label: str
    evidence: Evidence


class CorrigendumStatus(str, Enum):
    NOT_CHECKED = "NOT_CHECKED"  # no doc_id, or the check hasn't run yet
    NONE = "NONE"                # GeM answered "0": this bid has no corrigendum
    PRESENT = "PRESENT"          # GeM returned a corrigendum; `text` holds it
    FAILED = "FAILED"            # the check could not be completed; see `note`


class Corrigendum(BaseModel):
    """Whether a bid was amended after publication.

    A corrigendum can change the deadline, quantity, specification or terms, so a bid
    reported without one checked is a bid reported on possibly stale facts. The text is
    kept verbatim and never parsed for a revised deadline — see docs/02-RECON-FINDINGS.md
    §4: the fragment's structure has never been captured, and guessing it is exactly the
    silent wrong answer the architecture warns about.
    """

    status: CorrigendumStatus = CorrigendumStatus.NOT_CHECKED
    checked_at: datetime | None = None
    text: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _text_only_when_present(self) -> "Corrigendum":
        if self.status is CorrigendumStatus.PRESENT and not self.text:
            raise ValueError("PRESENT requires the corrigendum text")
        if self.status is not CorrigendumStatus.PRESENT and self.text:
            raise ValueError(f"{self.status.value} must not carry corrigendum text")
        return self


class BidAnalysis(BaseModel):
    """Facts extracted from one bid document. Verdicts are computed later, against a profile."""

    id: int | None = None
    bid_number: str | None
    doc_id: str | None = None  # GeM document id, when downloaded from GeM
    source_name: str
    sha256: str
    page_count: int
    unread_lines: int
    imported_at: datetime
    fields: list[ExtractedField]
    flags: list[ClauseFlag]
    corrigendum: Corrigendum = Field(default_factory=Corrigendum)
    parser_version: int = 0  # which version of the field rules produced `fields`

    def field(self, name: str) -> ExtractedField | None:
        return next((f for f in self.fields if f.name == name), None)

    def found_value(self, name: str) -> str | None:
        f = self.field(name)
        return f.value if f and f.status is FieldStatus.FOUND else None


class SellerProfile(BaseModel):
    business_name: str = ""
    is_mse: bool = False
    is_startup: bool = False
    is_oem: bool = False
    annual_turnover_rupees: int | None = Field(default=None, ge=0)
    years_experience: int | None = Field(default=None, ge=0)
    categories: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    # What to look for on GeM: items naming any of these words never count as his products,
    # and which bids a search downloads first.
    ignore_words: list[str] = Field(default_factory=list)
    bid_order: Literal["match", "closing"] = "match"
    updated_at: datetime | None = None


class RuleResult(BaseModel):
    rule: str
    outcome: Verdict
    reason: str
    evidence: Evidence | None = None


class Assessment(BaseModel):
    verdict: Verdict
    results: list[RuleResult]

    @property
    def review_count(self) -> int:
        return sum(1 for r in self.results if r.outcome is not Verdict.ELIGIBLE)


class BreakerState(BaseModel):
    open: bool = False
    reason: str | None = None
    opened_at: datetime | None = None
    last_reset_at: datetime | None = None
    last_reset_note: str | None = None


class RequestRecord(BaseModel):
    at: datetime
    url: str
    status: int | None
    outcome: str


# ---------- discovery (public bid listing) ----------


class BidKind(str, Enum):
    BID = "BID"
    RA = "RA"  # reverse auction: listed, but never downloaded or analysed


class DocStatus(str, Enum):
    PENDING = "PENDING"        # not downloaded yet
    DOWNLOADED = "DOWNLOADED"  # analysed; see bid_id
    FAILED = "FAILED"          # download or parse failed; see note
    SKIPPED = "SKIPPED"        # deliberately not downloaded (RA, closed, ...)


class ListingBid(BaseModel):
    """One card from the public /all-bids listing, as GeM's search returned it."""

    doc_id: str = Field(pattern=r"^\d+$")
    bid_number: str
    kind: BidKind
    doc_path: str  # "showbidDocument", taken from the listing, never guessed
    category: str | None = None
    quantity: str | None = None
    ministry: str | None = None
    department: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ListingEntry(ListingBid):
    keyword: str
    first_seen_at: datetime
    last_seen_at: datetime
    doc_status: DocStatus
    doc_note: str | None = None
    bid_id: int | None = None
    # Filled in for display from the current profile; not stored.
    matched_items: int = 0
    total_items: int = 0


class DiscoveryRun(BaseModel):
    id: int | None = None
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    outcome: str | None = None   # "ok" | "stopped" | "failed"
    message: str | None = None
    keywords: list[str] = Field(default_factory=list)
    found: int = 0
    new: int = 0
    downloaded: int = 0
    corrigenda_checked: int = 0
    requests: int = 0  # requests that actually went to GeM during this run
