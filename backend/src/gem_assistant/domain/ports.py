"""Interfaces the core depends on (Dependency Inversion).

Services and the safety layer only know these Protocols. SQLite, httpx,
pypdf and the system clock are plugged in by the composition root, and tests
plug in fakes. Interfaces are kept small (Interface Segregation): a class
that only reads profiles never sees a write method.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

from .models import (
    BidAnalysis,
    Corrigendum,
    DiscoveryRun,
    DocStatus,
    ListingBid,
    ListingEntry,
    BreakerState,
    ClauseFlag,
    ExtractedField,
    PageText,
    RequestRecord,
    RuleResult,
    SellerProfile,
)

# ---------- infrastructure ----------


class Clock(Protocol):
    def now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes


class HttpTransport(Protocol):
    def get(self, url: str, headers: dict[str, str], max_bytes: int) -> HttpResponse: ...


# ---------- persistence ----------


class ProfileReader(Protocol):
    def get(self) -> SellerProfile | None: ...


class ProfileWriter(Protocol):
    def save(self, profile: SellerProfile) -> SellerProfile: ...


class BidReader(Protocol):
    def list(self) -> list[BidAnalysis]: ...
    def get(self, bid_id: int) -> BidAnalysis | None: ...


class BidWriter(Protocol):
    def save(self, analysis: BidAnalysis) -> int: ...
    def find_id_by_sha256(self, sha256: str) -> int | None: ...
    def find_id_by_doc_id(self, doc_id: str) -> int | None: ...


class CorrigendumWriter(Protocol):
    def set_corrigendum(self, bid_id: int, corrigendum: Corrigendum) -> None: ...


class RequestLog(Protocol):
    def record(self, record: RequestRecord) -> None: ...
    def count_since(self, since: datetime) -> int: ...
    def recent(self, limit: int) -> list[RequestRecord]: ...


class BreakerStateStore(Protocol):
    def load(self) -> BreakerState: ...
    def save(self, state: BreakerState) -> None: ...


# ---------- document analysis ----------


class TextExtractor(Protocol):
    def extract(self, pdf_bytes: bytes) -> list[PageText]: ...


class FieldExtractor(Protocol):
    def extract(self, pages: Sequence[PageText]) -> list[ExtractedField]: ...


class ClauseScanner(Protocol):
    def scan(self, pages: Sequence[PageText]) -> list[ClauseFlag]: ...


class FieldReconciler(Protocol):
    def reconcile(
        self, fields: list[ExtractedField], flags: list[ClauseFlag]
    ) -> list[ExtractedField]: ...


class EligibilityRule(Protocol):
    name: str

    def evaluate(
        self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime
    ) -> list[RuleResult]: ...


# ---------- discovery ----------


@dataclass(frozen=True)
class ListingSearchResult:
    keyword: str
    total_found: int
    bids: list[ListingBid]


class ListingSource(Protocol):
    """Finds bids on the public listing. Implementations must send every request
    to GeM through SafeFetcher."""

    def search(self, keywords: Sequence[str], max_pages: int) -> list[ListingSearchResult]: ...


class CorrigendumSource(Protocol):
    """Asks GeM whether given bids were amended after publication.

    Separate from ListingSource because it is a separate decision to spend a request per
    bid on it. Returns one Corrigendum per requested doc_id; a doc_id that could not be
    checked comes back FAILED rather than being left out.
    """

    def corrigenda(self, doc_ids: Sequence[str]) -> dict[str, Corrigendum]: ...


class ListingRepository(Protocol):
    def upsert(self, bids: Sequence[ListingBid], keyword: str, now: datetime) -> list[str]: ...
    def pending_downloads(self, limit: int, now: datetime) -> list[ListingEntry]: ...
    def mark(self, doc_id: str, status: DocStatus, note: str | None = None, bid_id: int | None = None) -> None: ...
    def recent(self, limit: int) -> list[ListingEntry]: ...


class DiscoveryRunStore(Protocol):
    def start(self, run: DiscoveryRun) -> int: ...
    def finish(self, run: DiscoveryRun) -> None: ...
    def last(self, contacted_gem: bool = False) -> DiscoveryRun | None: ...
