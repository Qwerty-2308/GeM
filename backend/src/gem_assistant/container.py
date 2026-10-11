"""Composition root: the one place that knows concrete classes.

Everything else receives its dependencies through constructors. Tests build a
Container with a fake clock/transport instead of patching internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .domain.ports import Clock, HttpTransport, ListingSource
from .eligibility.evaluator import EligibilityEvaluator
from .infrastructure.database import Database
from .infrastructure.browser_listing import PlaywrightListingSource, PlaywrightNetwork
from .infrastructure.httpx_transport import HttpxTransport
from .infrastructure.pypdf_extractor import PypdfTextExtractor
from .infrastructure.sqlite_repositories import (
    SqliteBidRepository,
    SqliteBreakerStateStore,
    SqliteDiscoveryRunStore,
    SqliteListingRepository,
    SqliteProfileRepository,
    SqliteRequestLog,
)
from .infrastructure.system_clock import SystemClock
from .parsing.clause_scanner import PatternClauseScanner
from .parsing.label_extractor import LabelFieldExtractor
from .parsing.reconciler import RuleBasedReconciler
from .safety.circuit_breaker import CircuitBreaker
from .safety.daily_budget import DailyBudget
from .safety.rate_limiter import RateLimiter
from .safety.response_classifier import ResponseClassifier
from .safety.safe_fetcher import SafeFetcher
from .safety.url_policy import UrlPolicy
from .services.bid_fetch_service import BidFetchService
from .services.bid_import_service import BidImportService
from .services.bid_query_service import BidQueryService
from .services.discovery_service import DiscoveryService
from .services.profile_service import ProfileService
from .services.safety_service import SafetyService


@dataclass
class Container:
    settings: Settings
    profiles: ProfileService
    bid_import: BidImportService
    bid_query: BidQueryService
    safety: SafetyService
    fetcher: SafeFetcher
    bid_fetch: BidFetchService
    discovery: DiscoveryService

    @classmethod
    def build(cls, settings: Settings, clock: Clock | None = None,
              transport: HttpTransport | None = None,
              listing_source_factory: Callable[[SafeFetcher], ListingSource] | None = None) -> "Container":
        clock = clock or SystemClock()
        db = Database(settings.db_path)
        db.migrate()

        profile_repo = SqliteProfileRepository(db)
        bid_repo = SqliteBidRepository(db)
        request_log = SqliteRequestLog(db)
        breaker = CircuitBreaker(SqliteBreakerStateStore(db), clock)
        budget = DailyBudget(request_log, clock, settings.safety.daily_request_limit)

        last = request_log.recent(1)
        limiter = RateLimiter(clock, settings.safety.min_interval_seconds,
                              last_request_at=last[0].at if last else None)

        fetcher = SafeFetcher(
            transport=transport or HttpxTransport(settings.safety.request_timeout_seconds),
            policy=UrlPolicy(settings.safety.allowed_host_suffix, settings.safety.blocked_path_prefixes,
                             settings.safety.post_allowed_paths, settings.safety.post_allowed_prefixes),
            limiter=limiter,
            budget=budget,
            breaker=breaker,
            classifier=ResponseClassifier(),
            log=request_log,
            clock=clock,
            user_agent=settings.safety.user_agent,
            max_bytes=settings.safety.max_download_bytes,
        )

        bid_import = BidImportService(
            text_extractor=PypdfTextExtractor(settings.max_pdf_pages),
            field_extractor=LabelFieldExtractor(),
            clause_scanner=PatternClauseScanner(),
            reconciler=RuleBasedReconciler(),
            bids=bid_repo,
            clock=clock,
            max_bytes=settings.safety.max_download_bytes,
        )
        listing_repo = SqliteListingRepository(db)
        bid_fetch = BidFetchService(fetcher, bid_import, listing_repo, bid_repo)

        def default_source(f: SafeFetcher) -> ListingSource:
            return PlaywrightListingSource(
                fetcher=f,
                network=PlaywrightNetwork(settings.safety.request_timeout_seconds),
                executable_path=settings.discovery.browser_executable_path,
                timeout_seconds=settings.discovery.browser_timeout_seconds,
                save_raw_to=settings.recon_dir / "last-listing-response.json",
                clock=clock,
            )

        make_source = listing_source_factory or default_source
        discovery = DiscoveryService(
            source_factory=lambda: make_source(fetcher),
            listings=listing_repo,
            runs=SqliteDiscoveryRunStore(db),
            profiles=profile_repo,
            fetch_service=bid_fetch,
            fetcher=fetcher,
            breaker=breaker,
            clock=clock,
            settings=settings.discovery,
            bids=bid_repo,
        )

        return cls(
            settings=settings,
            profiles=ProfileService(profile_repo, profile_repo, clock),
            bid_import=bid_import,
            bid_query=BidQueryService(bid_repo, profile_repo, EligibilityEvaluator(), clock),
            safety=SafetyService(breaker, budget, request_log, settings.safety,
                                 settings.discovery.min_minutes_between_runs),
            fetcher=fetcher,
            bid_fetch=bid_fetch,
            discovery=discovery,
        )
