"""One discovery run: search the public listing for the seller's categories,
store what's found, and download + analyse a few new bid documents.

Guard rails (on top of SafeFetcher's per-request rules):
- one run at a time, and at least N minutes between runs
- capped keywords, pages per keyword and downloads per run
- any safety stop ends the whole run immediately
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from ..config import DiscoverySettings
from ..domain.models import CorrigendumStatus, DiscoveryRun, ListingEntry
from ..domain.ports import (
    Clock, CorrigendumWriter, DiscoveryRunStore, ListingRepository, ListingSource, ProfileReader,
)
from ..infrastructure.browser_listing import BrowserUnavailable
from ..parsing.listing_parser import ListingFormatChanged, ListingRejected
from ..safety.circuit_breaker import CircuitBreaker
from ..safety.errors import SafetyError
from ..safety.safe_fetcher import SafeFetcher
from .bid_fetch_service import BidFetchService

# Same rule the GeM page applies to its search box.
_INVALID_KEYWORD = re.compile(r"[~`!#$%\^*@+=\\\[\];{}|\":<>?]")


class DiscoveryNotAllowed(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoveryStatus:
    running: bool
    keywords: list[str]
    last_run: DiscoveryRun | None
    next_allowed_at: datetime | None
    listings: list[ListingEntry]
    settings: DiscoverySettings


class DiscoveryService:
    def __init__(
        self,
        source_factory: Callable[[], ListingSource],
        listings: ListingRepository,
        runs: DiscoveryRunStore,
        profiles: ProfileReader,
        fetch_service: BidFetchService,
        fetcher: SafeFetcher,
        breaker: CircuitBreaker,
        clock: Clock,
        settings: DiscoverySettings,
        bids: CorrigendumWriter,
    ):
        self._source_factory = source_factory
        self._listings = listings
        self._runs = runs
        self._profiles = profiles
        self._fetch = fetch_service
        self._bids = bids
        self._fetcher = fetcher
        self._breaker = breaker
        self._clock = clock
        self._settings = settings
        self._lock = threading.Lock()

    # ---------- queries ----------

    def keywords(self) -> list[str]:
        profile = self._profiles.get()
        seen: dict[str, str] = {}
        for raw in (profile.categories if profile else []):
            keyword = " ".join(raw.split())
            if len(keyword) < 3 or _INVALID_KEYWORD.search(keyword):
                continue
            seen.setdefault(keyword.lower(), keyword)
        return list(seen.values())[: self._settings.max_keywords]

    def next_allowed_at(self) -> datetime | None:
        # Only runs that actually contacted GeM count (a missing browser shouldn't lock you out).
        last = self._runs.last(contacted_gem=True)
        if last is None:
            return None
        return last.started_at + timedelta(minutes=self._settings.min_minutes_between_runs)

    def status(self) -> DiscoveryStatus:
        return DiscoveryStatus(
            running=self._lock.locked(),
            keywords=self.keywords(),
            last_run=self._runs.last(),
            next_allowed_at=self.next_allowed_at(),
            listings=self._listings.recent(100),
            settings=self._settings,
        )

    # ---------- commands ----------

    def start_in_background(self, trigger: str = "manual") -> None:
        keywords = self._claim(trigger)
        thread = threading.Thread(target=self._run_claimed, args=(trigger, keywords), daemon=True,
                                  name="gem-discovery")
        thread.start()

    def run(self, trigger: str = "manual") -> DiscoveryRun:
        keywords = self._claim(trigger)
        return self._run_claimed(trigger, keywords)

    # ---------- internals ----------

    def _claim(self, trigger: str) -> list[str]:
        """Check every precondition and take the single-run lock, or raise."""
        if not self._lock.acquire(blocking=False):
            raise DiscoveryNotAllowed("A search is already running.")
        try:
            keywords = self.keywords()
            if not keywords:
                raise DiscoveryNotAllowed(
                    "Add your product categories to the seller profile first; they are the search keywords.")
            allowed_at = self.next_allowed_at()
            if allowed_at and self._clock.now() < allowed_at:
                minutes = int((allowed_at - self._clock.now()).total_seconds() // 60) + 1
                raise DiscoveryNotAllowed(
                    f"GeM updates its listing every 15 minutes. Try again in {minutes} min.")
            self._fetcher.ensure_ready()  # breaker closed, daily budget left
            return keywords
        except SafetyError as error:
            self._lock.release()
            raise DiscoveryNotAllowed(str(error)) from error
        except BaseException:
            self._lock.release()
            raise

    def _run_claimed(self, trigger: str, keywords: list[str]) -> DiscoveryRun:
        run = DiscoveryRun(trigger=trigger, started_at=self._clock.now(), keywords=keywords)
        sent_before = self._fetcher.requests_sent
        try:
            run.id = self._runs.start(run)
            self._execute(run)
            run.outcome = "ok"
            run.message = (f"Found {run.found} bids ({run.new} new); analysed {run.downloaded}; "
                           f"checked {run.corrigenda_checked} for corrigenda.")
        except ListingRejected as error:
            self._breaker.trip(f"Listing search refused: {error}")
            run.outcome, run.message = "stopped", f"GeM refused the search ({error}). Fetching stopped."
        except SafetyError as error:
            run.outcome, run.message = "stopped", str(error)
        except ListingFormatChanged as error:
            run.outcome, run.message = "failed", f"The GeM page has changed and needs an update: {error}"
        except BrowserUnavailable as error:
            run.outcome, run.message = "failed", str(error)
        except Exception as error:  # record, never retry
            run.outcome, run.message = "failed", f"{type(error).__name__}: {error}"
        finally:
            run.finished_at = self._clock.now()
            run.requests = self._fetcher.requests_sent - sent_before
            if run.id is not None:
                self._runs.finish(run)
            self._lock.release()
        return run

    def _execute(self, run: DiscoveryRun) -> None:
        source = self._source_factory()
        results = source.search(run.keywords, self._settings.max_pages_per_keyword)
        now = self._clock.now()
        for result in results:
            run.found += len(result.bids)
            run.new += len(self._listings.upsert(result.bids, result.keyword, now))

        analysed: dict[str, int] = {}  # doc_id -> bid_id, for the corrigendum pass
        for entry in self._listings.pending_downloads(self._settings.max_downloads_per_run, now):
            result = self._fetch.fetch_listing_entry(entry)  # safety stops propagate and end the run
            if result is not None:
                if result.downloaded:
                    run.downloaded += 1
                analysed[entry.doc_id] = result.import_result.id

        run.corrigenda_checked = self._check_corrigenda(analysed)

    def _check_corrigenda(self, analysed: dict[str, int]) -> int:
        """Ask GeM whether the bids we just analysed were amended. One request each, so
        only the bids we actually analysed are checked — never every bid we listed.

        Runs after the downloads so each answer can be stored against a real bid, and
        needs its own browser pass because the request carries a CSRF token that only
        the rendered listing page has (docs/02-RECON-FINDINGS.md §4).
        """
        doc_ids = list(analysed)[: self._settings.max_corrigendum_checks_per_run]
        if not doc_ids:
            return 0
        found = self._source_factory().corrigenda(doc_ids)
        for doc_id, corrigendum in found.items():
            self._bids.set_corrigendum(analysed[doc_id], corrigendum)
        # Only count bids GeM actually answered for. A failed check is not a check, and
        # saying otherwise would read as reassurance the run has not earned.
        return sum(1 for c in found.values()
                   if c.status in (CorrigendumStatus.NONE, CorrigendumStatus.PRESENT))
