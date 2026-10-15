"""Finds bids by rendering GeM's public /all-bids page in headless Chromium.

What actually reaches GeM per run:
  1 GET  /all-bids               (the page itself)
  1 POST /all-bids-data per search/page, made by the page's own JavaScript
Everything else the page asks for is handled locally:
  - scripts are served from browser_assets/ (vendored jQuery + no-op stubs),
    so robots.txt-disallowed /resources/ is never fetched
  - images, CSS, fonts, analytics and every other request are aborted
  - the page's automatic "show all bids" search on load is aborted
Every request that does go out passes through SafeFetcher (policy, rate
limit, daily cap, breaker, logging) exactly like a PDF download.

The search results are read from the JSON response, not scraped off the screen.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, Sequence
from urllib.parse import urlsplit

from ..domain.models import Corrigendum, CorrigendumStatus
from ..domain.ports import Clock, HttpResponse, ListingSearchResult
from ..parsing.corrigendum_parser import parse_corrigendum_response
from ..parsing.listing_parser import ListingFormatChanged, ListingPage, parse_listing_response
from ..safety.errors import SafetyError
from ..safety.response_classifier import Expect
from ..safety.safe_fetcher import SafeFetcher
from .system_clock import SystemClock

ASSETS = Path(__file__).parent / "browser_assets"
LISTING_URL = "https://bidplus.gem.gov.in/all-bids"
SEARCH_PATH = "/all-bids-data"
# The page's own corrigendum view; the bid's document id is appended.
# docs/02-RECON-FINDINGS.md §4.
CORRIGENDUM_PREFIX = "/bidding/bid/viewcorrigendum/"
PAGE_SIZE = 10
_DROP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}


class BrowserUnavailable(RuntimeError):
    pass


class RouteNetwork(Protocol):
    """Performs an already-approved browser request. Swapped for fixtures in tests."""

    def fetch(self, route: Any) -> HttpResponse: ...


class PlaywrightNetwork:
    def __init__(self, timeout_seconds: float):
        self._timeout_ms = timeout_seconds * 1000

    def fetch(self, route: Any) -> HttpResponse:
        r = route.fetch(max_redirects=0, timeout=self._timeout_ms)
        return HttpResponse(
            url=r.url, status=r.status,
            headers={k.lower(): v for k, v in r.headers.items()}, body=r.body(),
        )


@dataclass
class _Session:
    """Per-run state shared between the route handler and the driver code."""

    fetcher: SafeFetcher
    network: RouteNetwork
    listing_path: str
    jquery: bytes
    stubs: bytes
    armed: bool = False                      # True only while we expect one search request
    captured: list[Any] = field(default_factory=list)
    error: BaseException | None = None
    aborted: int = 0
    served_locally: int = 0

    def handle(self, route: Any) -> None:
        try:
            self._handle(route)
        except BaseException as error:  # never let an exception escape into Playwright
            self.error = self.error or error
            try:
                route.abort()
            except Exception:
                pass

    def _handle(self, route: Any) -> None:
        request = route.request
        parts = urlsplit(request.url)
        host = (parts.hostname or "").lower()
        is_gem = host == "gem.gov.in" or host.endswith(".gem.gov.in")
        path = parts.path.rstrip("/") or "/"

        if request.resource_type == "script":
            body = self.jquery if path.endswith("/jquery-3.6.0.min.js") else self.stubs
            self.served_locally += 1
            route.fulfill(status=200, content_type="application/javascript", body=body)
            return

        if is_gem and request.method == "GET" and request.resource_type == "document" and path == self.listing_path:
            self._through_gate(route, "GET", Expect.HTML)
            return

        if is_gem and request.method == "POST" and path == SEARCH_PATH:
            self._armed_post(route, Expect.JSON)
            return

        if is_gem and request.method == "POST" and path.lower().startswith(CORRIGENDUM_PREFIX):
            # Body is "0" or a snippet of buyer-written text, never a whole page.
            self._armed_post(route, Expect.HTML_FRAGMENT)
            return

        self.aborted += 1
        route.abort()

    def _armed_post(self, route: Any, expect: Expect) -> None:
        """Let a POST out only while we are expecting exactly one. Anything the page
        decides to send on its own (e.g. its "load everything" search on startup) is
        aborted before it leaves the machine."""
        if not self.armed:
            self.aborted += 1
            route.abort()
            return
        self.armed = False
        response = self._through_gate(route, "POST", expect)
        if response is not None:
            self.captured.append(response)

    def _through_gate(self, route: Any, method: str, expect: Expect) -> HttpResponse | None:
        try:
            response = self.fetcher.execute(route.request.url, method, lambda: self.network.fetch(route), expect)
        except BaseException as error:
            self.error = self.error or error
            route.abort()
            return None
        headers = {k: v for k, v in response.headers.items() if k.lower() not in _DROP_HEADERS}
        route.fulfill(status=response.status, headers=headers, body=response.body)
        return response


class PlaywrightListingSource:
    def __init__(
        self,
        fetcher: SafeFetcher,
        network: RouteNetwork,
        listing_url: str = LISTING_URL,
        executable_path: str | None = None,
        timeout_seconds: float = 60.0,
        save_raw_to: Path | None = None,
        save_corrigenda_to: Path | None = None,
        playwright_factory: Callable[[], Any] | None = None,
        clock: Clock | None = None,
    ):
        self._fetcher = fetcher
        self._network = network
        self._listing_url = listing_url
        self._executable = executable_path
        self._timeout = timeout_seconds
        self._save_raw_to = save_raw_to
        self._save_corrigenda_to = save_corrigenda_to
        self._playwright_factory = playwright_factory
        self._clock = clock or SystemClock()

    # ---- public ----

    def search(self, keywords: Sequence[str], max_pages: int) -> list[ListingSearchResult]:
        with self._listing_page() as (page, session):
            return [self._search_keyword(page, session, k, max_pages) for k in keywords]

    def corrigenda(self, doc_ids: Sequence[str]) -> dict[str, Corrigendum]:
        """One request per bid, through the page's own corrigendum handler so the CSRF
        token comes from the page rather than being forged by us."""
        if not doc_ids:
            return {}
        with self._listing_page() as (page, session):
            self._check_corrigendum_shape(page)
            return {doc_id: self._one_corrigendum(page, session, doc_id) for doc_id in doc_ids}

    # ---- internals ----

    @contextmanager
    def _listing_page(self) -> Iterator[tuple[Any, "_Session"]]:
        """Renders /all-bids once and yields the live page. Closing the context throws
        away the temporary session cookies."""
        session = _Session(
            fetcher=self._fetcher,
            network=self._network,
            listing_path=urlsplit(self._listing_url).path.rstrip("/"),
            jquery=(ASSETS / "jquery-3.6.0.min.js").read_bytes(),
            stubs=(ASSETS / "stubs.js").read_bytes(),
        )
        factory = self._playwright_factory or _default_playwright
        with factory() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, executable_path=self._executable)
            except Exception as error:
                raise BrowserUnavailable(
                    "Headless Chromium is not installed. Run: uv run playwright install chromium"
                ) from error
            try:
                context = browser.new_context(
                    user_agent=self._fetcher.user_agent, service_workers="block", accept_downloads=False,
                )
                try:
                    context.route("**/*", session.handle)
                    page = context.new_page()
                    page.set_default_timeout(self._timeout * 1000)
                    try:
                        page.goto(self._listing_url, wait_until="domcontentloaded")
                    except Exception:
                        self._raise_if_failed(session)  # a safety stop explains the failure better
                        raise
                    self._raise_if_failed(session)
                    page.wait_for_load_state("load")
                    self._check_page_shape(page)
                    yield page, session
                finally:
                    context.close()
            finally:
                browser.close()

    def _search_keyword(self, page: Any, session: _Session, keyword: str, max_pages: int) -> ListingSearchResult:
        first = self._search_page(page, session, lambda: self._submit_search(page, keyword))
        bids, total = list(first.bids), first.total_found
        for number in range(2, max_pages + 1):
            if total <= (number - 1) * PAGE_SIZE:
                break
            nxt = self._search_page(page, session, lambda n=number: page.evaluate("n => loadBids(n)", n))
            bids.extend(nxt.bids)
        return ListingSearchResult(keyword=keyword, total_found=total, bids=bids)

    def _search_page(self, page: Any, session: _Session, action: Callable[[], None]) -> ListingPage:
        response = self._trigger(page, session, action, "search")
        raw = json.loads(response.body)
        self._save_raw(raw)
        return parse_listing_response(raw)

    def _one_corrigendum(self, page: Any, session: _Session, doc_id: str) -> Corrigendum:
        """A problem with one bid's corrigendum is recorded against that bid. A safety
        stop or a changed page is not survivable and ends the whole run."""
        try:
            response = self._trigger(
                page, session,
                lambda: page.evaluate("id => view_corrigendum_modal(id)", doc_id),
                "corrigendum",
            )
        except (SafetyError, ListingFormatChanged):
            raise
        except Exception as error:
            return Corrigendum(
                status=CorrigendumStatus.FAILED,
                checked_at=self._clock.now(),
                note=f"Could not check for a corrigendum: {type(error).__name__}: {error}",
            )
        self._save_corrigendum(doc_id, response.body)
        return parse_corrigendum_response(response.body, self._clock.now())

    @staticmethod
    def _submit_search(page: Any, keyword: str) -> None:
        # Same path as typing in the box and pressing search, including the page's
        # own keyword validation. Done through the page's jQuery handler because
        # the box isn't laid out (we never load GeM's CSS).
        page.evaluate(
            "kw => { document.getElementById('searchBid').value = kw; jQuery('#searchBidRA').trigger('click'); }",
            keyword,
        )
        error_text = page.evaluate("() => (document.getElementById('searchError') || {}).textContent || ''")
        if error_text.strip():
            raise ValueError(f"GeM rejected the search '{keyword}': {error_text.strip()}")

    def _trigger(self, page: Any, session: _Session, action: Callable[[], None], what: str) -> HttpResponse:
        """Arm the session for exactly one request, run `action`, and wait for the page
        to make it."""
        # Each attempt starts clean. The route handler keeps the first error it sees, so
        # without this a failure on one bid would be re-raised for every bid after it —
        # reporting them as unchecked, with the wrong reason, without even asking GeM.
        # A safety stop is not lost by this: SafeFetcher re-checks the breaker on every
        # request, and _one_corrigendum re-raises SafetyError instead of absorbing it.
        session.error = None
        before = len(session.captured)
        session.armed = True
        try:
            action()
            deadline = time.monotonic() + self._timeout
            while len(session.captured) == before:
                self._raise_if_failed(session)
                if time.monotonic() > deadline:
                    raise ListingFormatChanged(
                        f"The page did not send its {what} request (page may have changed)")
                page.wait_for_timeout(100)
        finally:
            session.armed = False
        self._raise_if_failed(session)
        return session.captured[-1]

    @staticmethod
    def _check_page_shape(page: Any) -> None:
        ok = page.evaluate(
            "() => typeof window.loadBids === 'function' && !!document.getElementById('searchBid')"
            " && !!document.getElementById('searchBidRA')"
        )
        if not ok:
            raise ListingFormatChanged("The /all-bids page changed: search box or loadBids() not found")

    @staticmethod
    def _check_corrigendum_shape(page: Any) -> None:
        ok = page.evaluate("() => typeof window.view_corrigendum_modal === 'function'")
        if not ok:
            raise ListingFormatChanged(
                "The /all-bids page changed: view_corrigendum_modal() not found, so corrigenda "
                "cannot be checked")

    @staticmethod
    def _raise_if_failed(session: _Session) -> None:
        if session.error is not None:
            raise session.error

    def _save_raw(self, raw: Any) -> None:
        if self._save_raw_to is None:
            return
        self._save_raw_to.parent.mkdir(parents=True, exist_ok=True)
        self._save_raw_to.write_text(json.dumps(raw, ensure_ascii=False, indent=1))

    def _save_corrigendum(self, doc_id: str, body: bytes) -> None:
        """Keep a real fragment exactly as GeM sent it — its structure has never been
        captured (docs/02-RECON-FINDINGS.md §4). "0"/empty mean "none" and are not kept."""
        if self._save_corrigenda_to is None or body.strip() in (b"", b"0") or not doc_id.isdigit():
            return
        self._save_corrigenda_to.mkdir(parents=True, exist_ok=True)
        (self._save_corrigenda_to / f"{doc_id}.html").write_bytes(body)


def _default_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserUnavailable("Playwright is not installed. Run: uv sync") from error
    return sync_playwright()
