"""Composition root: the one place that knows concrete classes.

Everything else receives its dependencies through constructors. Tests build a
Container with a fake clock/transport instead of patching internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable

from .config import Settings
from .domain.ports import Clock, HttpTransport, ListingSource
from .eligibility.evaluator import EligibilityEvaluator
from .infrastructure.database import Database
from .infrastructure.document_store import FileDocumentStore
from .infrastructure.browser_listing import PlaywrightListingSource, PlaywrightNetwork
from .infrastructure.httpx_transport import HttpxTransport
from .infrastructure.pypdf_extractor import PypdfTextExtractor, pdf_links
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
from .services.corpus_service import CorpusService
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
    corpus: CorpusService

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

        def last_request_at():
            last = request_log.recent(1)
            return last[0].at if last else None

        limiter = RateLimiter(clock, settings.safety.min_interval_seconds,
                              last_request_source=last_request_at,
                              lock_path=settings.data_dir / "fetch.lock")

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
            documents=FileDocumentStore(settings.data_dir / "documents"),
            bid_reader=bid_repo,
        )
        listing_repo = SqliteListingRepository(db)
        bid_fetch = BidFetchService(fetcher, bid_import, listing_repo, bid_repo)

        def source_for(raw_to: Path, corrigenda_to: Path) -> ListingSource:
            if listing_source_factory is not None:
                return listing_source_factory(fetcher)
            return PlaywrightListingSource(
                fetcher=fetcher,
                network=PlaywrightNetwork(settings.safety.request_timeout_seconds),
                executable_path=settings.discovery.browser_executable_path,
                timeout_seconds=settings.discovery.browser_timeout_seconds,
                save_raw_to=raw_to,
                save_corrigenda_to=corrigenda_to,
                clock=clock,
            )

        runs = SqliteDiscoveryRunStore(db)
        # A run cut off by a crash or restart would show as running forever. The hard caps and
        # timeouts bound a real run to well under an hour, so an older unfinished one is dead
        # (a recent one may be a command-line search still going in another process).
        runs.close_interrupted(started_before=clock.now() - timedelta(hours=1), now=clock.now())
        discovery = DiscoveryService(
            source_factory=lambda: source_for(settings.recon_dir / "last-listing-response.json",
                                              settings.recon_dir / "corrigenda"),
            listings=listing_repo,
            runs=runs,
            profiles=profile_repo,
            fetch_service=bid_fetch,
            fetcher=fetcher,
            breaker=breaker,
            clock=clock,
            settings=settings.discovery,
            bids=bid_repo,
        )
        corpus = CorpusService(
            discovery=discovery,
            source_factory=lambda folder: source_for(folder / "listing-response.json", folder / "corrigenda"),
            listings=listing_repo,
            fetch_service=bid_fetch,
            fetcher=fetcher,
            bids=bid_repo,
            budget=budget,
            link_reader=pdf_links,
            clock=clock,
            corpus_root=settings.data_dir / "corpus",
            max_pages=settings.discovery.max_pages_per_keyword,
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
            corpus=corpus,
        )
