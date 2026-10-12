"""Response shapes for the HTTP API (kept separate from domain models)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from ..domain.models import (
    BreakerState, ClauseFlag, Corrigendum, DiscoveryRun, ExtractedField, ListingEntry, RequestRecord,
    RuleResult, Verdict,
)
from ..services.bid_query_service import AssessedBid
from ..services.discovery_service import DiscoveryStatus
from ..services.safety_service import SafetyStatus


class BidSummaryOut(BaseModel):
    id: int
    bid_number: str | None
    item_category: str | None
    organisation: str | None
    ministry: str | None
    closes_at: str | None
    verdict: Verdict
    review_count: int
    imported_at: datetime

    @classmethod
    def from_assessed(cls, item: AssessedBid) -> "BidSummaryOut":
        bid = item.bid
        return cls(
            id=bid.id, bid_number=bid.bid_number,
            item_category=bid.found_value("item_category"),
            organisation=bid.found_value("organisation"),
            ministry=bid.found_value("ministry"),
            closes_at=bid.found_value("bid_end"),
            verdict=item.assessment.verdict,
            review_count=item.assessment.review_count,
            imported_at=bid.imported_at,
        )


class BidDetailOut(BaseModel):
    id: int
    bid_number: str | None
    source_name: str
    page_count: int
    unread_lines: int
    imported_at: datetime
    verdict: Verdict
    results: list[RuleResult]
    fields: list[ExtractedField]
    flags: list[ClauseFlag]
    corrigendum: Corrigendum

    @classmethod
    def from_assessed(cls, item: AssessedBid) -> "BidDetailOut":
        bid = item.bid
        return cls(
            id=bid.id, bid_number=bid.bid_number, source_name=bid.source_name,
            page_count=bid.page_count, unread_lines=bid.unread_lines, imported_at=bid.imported_at,
            verdict=item.assessment.verdict, results=item.assessment.results,
            fields=bid.fields, flags=bid.flags, corrigendum=bid.corrigendum,
        )


class ImportOut(BaseModel):
    id: int
    already_imported: bool


class BudgetOut(BaseModel):
    used_today: int
    daily_limit: int


class SafetyOut(BaseModel):
    breaker: BreakerState
    budget: BudgetOut
    min_interval_seconds: float
    user_agent: str
    contact_email: str | None
    recent_requests: list[RequestRecord]
    rules: list[str]

    @classmethod
    def from_status(cls, s: SafetyStatus) -> "SafetyOut":
        return cls(
            breaker=s.breaker,
            budget=BudgetOut(used_today=s.used_today, daily_limit=s.daily_limit),
            min_interval_seconds=s.min_interval_seconds,
            user_agent=s.user_agent,
            contact_email=s.contact_email,
            recent_requests=s.recent_requests,
            rules=s.rules,
        )


class ContactIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    confirmed: bool
    note: str = ""


class FetchIn(BaseModel):
    link: str


class FetchOut(BaseModel):
    id: int
    already_imported: bool
    downloaded: bool


class DiscoveryLimitsOut(BaseModel):
    min_minutes_between_runs: int
    max_keywords: int
    max_pages_per_keyword: int
    max_downloads_per_run: int


class DiscoveryOut(BaseModel):
    running: bool
    keywords: list[str]
    last_run: DiscoveryRun | None
    next_allowed_at: datetime | None
    limits: DiscoveryLimitsOut
    listings: list[ListingEntry]

    @classmethod
    def from_status(cls, s: DiscoveryStatus) -> "DiscoveryOut":
        return cls(
            running=s.running, keywords=s.keywords, last_run=s.last_run, next_allowed_at=s.next_allowed_at,
            limits=DiscoveryLimitsOut(
                min_minutes_between_runs=s.settings.min_minutes_between_runs,
                max_keywords=s.settings.max_keywords,
                max_pages_per_keyword=s.settings.max_pages_per_keyword,
                max_downloads_per_run=s.settings.max_downloads_per_run,
            ),
            listings=s.listings,
        )
