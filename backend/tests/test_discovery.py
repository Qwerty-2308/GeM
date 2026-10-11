"""Discovery runs and fetch-by-link, with a fake listing source and fake network."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from gem_assistant.api.app import create_app
from gem_assistant.container import Container
from gem_assistant.domain.models import CorrigendumStatus, DocStatus, SellerProfile
from gem_assistant.parsing.corrigendum_parser import parse_corrigendum_response
from gem_assistant.domain.ports import HttpResponse, ListingSearchResult
from gem_assistant.safety.response_classifier import Expect
from gem_assistant.infrastructure.browser_listing import BrowserUnavailable
from gem_assistant.parsing.listing_parser import ListingRejected, parse_listing_response
from gem_assistant.services.bid_fetch_service import BadBidLink
from gem_assistant.services.discovery_service import DiscoveryNotAllowed

from .conftest import SAMPLE_PDF
from .listing_fixtures import make_doc, make_response

SAMPLE_DOC = make_doc(0, b_id=8506455, b_bid_number="GEM/2025/B/6819518",
                      final_end_date_sort="2025-11-14T12:00:00Z")


class FakeSource:
    """Pretends to search: one guarded request (like loading the page), then canned results."""

    def __init__(self, docs=None, error: Exception | None = None):
        self.docs = docs if docs is not None else [SAMPLE_DOC, make_doc(1, bid_type=5)]
        self.error = error
        self.calls = []
        self.corrigendum_calls = []
        self.corrigendum_body = b"0"  # GeM's "no corrigendum for this bid"
        self.fetcher = None

    def bind(self, fetcher):
        self.fetcher = fetcher
        return self

    def _load_page(self):
        page = HttpResponse(url="", status=200, headers={}, body=b"<html>listing</html>")
        self.fetcher.execute("https://bidplus.gem.gov.in/all-bids", "GET", lambda: page, Expect.HTML)

    def search(self, keywords, max_pages):
        self.calls.append((list(keywords), max_pages))
        if self.error:
            raise self.error
        self._load_page()
        bids = parse_listing_response(make_response(self.docs)).bids
        return [ListingSearchResult(keyword=keywords[0], total_found=len(bids), bids=bids)]

    def corrigenda(self, doc_ids):
        """Mirrors the real source: render the listing page once, then one guarded POST
        per bid."""
        self.corrigendum_calls.append(list(doc_ids))
        self._load_page()
        checked_at = datetime.now(timezone.utc)
        result = {}
        for doc_id in doc_ids:
            response = HttpResponse(url="", status=200, headers={}, body=self.corrigendum_body)
            self.fetcher.execute(
                f"https://bidplus.gem.gov.in/bidding/bid/viewCorrigendum/{doc_id}",
                "POST", lambda r=response: r, Expect.HTML,
            )
            result[doc_id] = parse_corrigendum_response(self.corrigendum_body, checked_at)
        return result


@pytest.fixture
def source() -> FakeSource:
    return FakeSource()


@pytest.fixture
def app(settings, clock, transport, source) -> Container:
    c = Container.build(settings, clock=clock, transport=transport, listing_source_factory=source.bind)
    c.profiles.save(SellerProfile(business_name="Test", categories=["Pump", "x", "bad*kw", "pump"]))
    return c


def _queue_pdf(transport):
    transport.queue(body=SAMPLE_PDF.read_bytes(), headers={"content-type": "application/pdf"})


# ---------- discovery service ----------

def test_keywords_come_from_profile_and_follow_gem_rules(app):
    assert app.discovery.keywords() == ["Pump"]  # too short, invalid chars and duplicates dropped


def test_needs_profile_categories(settings, clock, transport, source):
    c = Container.build(settings, clock=clock, transport=transport, listing_source_factory=source.bind)
    with pytest.raises(DiscoveryNotAllowed, match="categories"):
        c.discovery.run()
    assert source.calls == []


def test_a_failed_corrigendum_check_is_not_counted_as_checked(app, transport, source):
    """"Checked 1" must mean GeM answered, not that we tried and could not tell."""
    _queue_pdf(transport)
    source.corrigendum_body = b"<div><script>nothing readable</script></div>"
    run = app.discovery.run()
    assert run.outcome == "ok", run.message
    assert run.corrigenda_checked == 0
    assert "checked 0 for corrigenda" in run.message

    (assessed,) = app.bid_query.list()
    assert assessed.bid.corrigendum.status is CorrigendumStatus.FAILED
    assert any("did not complete" in r.reason for r in assessed.assessment.results)


def test_run_finds_stores_and_analyses_new_bids(app, transport, source):
    _queue_pdf(transport)
    run = app.discovery.run()
    assert run.outcome == "ok", run.message
    assert (run.found, run.new, run.downloaded) == (2, 2, 1)
    assert source.calls == [(["Pump"], 2)]
    assert [u for u, _ in transport.calls] == ["https://bidplus.gem.gov.in/showbidDocument/8506455"]

    entries = {e.doc_id: e for e in app.discovery.status().listings}
    assert entries["8506455"].doc_status is DocStatus.DOWNLOADED
    assert entries["9000001"].doc_status is DocStatus.SKIPPED  # reverse auction
    bid = app.bid_query.get(entries["8506455"].bid_id).bid
    assert bid.bid_number == "GEM/2025/B/6819518" and bid.doc_id == "8506455"


def test_runs_are_spaced_and_documents_never_downloaded_twice(app, transport, source, clock):
    _queue_pdf(transport)
    app.discovery.run()
    with pytest.raises(DiscoveryNotAllowed, match="15 minutes"):
        app.discovery.run()

    clock.advance(minutes=16)
    run = app.discovery.run()
    assert run.outcome == "ok" and run.downloaded == 0
    assert len(transport.calls) == 1


def test_block_during_download_stops_run_and_future_runs(app, transport, clock):
    transport.queue(status=403)
    run = app.discovery.run()
    assert run.outcome == "stopped"
    assert app.safety.status().breaker.open
    clock.advance(minutes=30)
    with pytest.raises(DiscoveryNotAllowed, match="stopped"):
        app.discovery.run()


def test_missing_document_is_recorded_and_run_continues(app, transport):
    transport.queue(status=404)
    run = app.discovery.run()
    assert run.outcome == "ok"
    entry = next(e for e in app.discovery.status().listings if e.doc_id == "8506455")
    assert entry.doc_status is DocStatus.FAILED and "404" in entry.doc_note
    assert not app.safety.status().breaker.open


def test_rejected_search_trips_breaker(app, source):
    source.error = ListingRejected("code 403")
    run = app.discovery.run()
    assert run.outcome == "stopped"
    assert app.safety.status().breaker.open


def test_missing_browser_does_not_lock_out_next_run(app, source, transport):
    source.error = BrowserUnavailable("not installed")
    run = app.discovery.run()
    assert run.outcome == "failed" and run.requests == 0
    source.error = None
    _queue_pdf(transport)
    assert app.discovery.run().outcome == "ok"  # no 15-minute wait: GeM was never contacted


def test_downloads_per_run_are_capped(app, transport, source, settings):
    source.docs = [make_doc(i, final_end_date_sort="2025-11-20T12:00:00Z") for i in range(1, 9)]
    for _ in range(settings.discovery.max_downloads_per_run):
        _queue_pdf(transport)
    run = app.discovery.run()
    assert len(transport.calls) == settings.discovery.max_downloads_per_run


# ---------- fetch by link ----------

@pytest.mark.parametrize("link", [
    "https://bidplus.gem.gov.in/showbidDocument/8506455/../../bg_emd",
    "https://evil.com/showbidDocument/8506455",
    "http://bidplus.gem.gov.in/showbidDocument/8506455",
    "https://bidplus.gem.gov.in/bidding/bid/getBidResultView/4046359",
    "GEM/2025/B/6819518",
    "",
])
def test_bad_links_are_refused_before_any_request(app, transport, link):
    with pytest.raises(BadBidLink):
        app.bid_fetch.fetch_from_link(link)
    assert transport.calls == []


def test_fetch_by_link_downloads_once(app, transport):
    _queue_pdf(transport)
    first = app.bid_fetch.fetch_from_link("https://bidplus.gem.gov.in/showbidDocument/8506455")
    second = app.bid_fetch.fetch_from_link("8506455")
    assert first.downloaded and not second.downloaded
    assert second.import_result.id == first.import_result.id
    assert len(transport.calls) == 1


# ---------- API ----------

def test_api_fetch_and_background_discovery(app, transport):
    client = TestClient(create_app(app, allowed_hosts=frozenset({"testserver"})))

    assert client.post("/api/bids/fetch", json={"link": "nope"}).status_code == 400
    _queue_pdf(transport)
    ok = client.post("/api/bids/fetch", json={"link": "https://bidplus.gem.gov.in/showbidDocument/8506455"})
    assert ok.status_code == 200 and ok.json()["downloaded"] is True

    assert client.post("/api/discovery/run").status_code == 202
    for _ in range(50):
        status = client.get("/api/discovery").json()
        if not status["running"] and status["last_run"] and status["last_run"]["outcome"]:
            break
        time.sleep(0.05)
    assert status["last_run"]["outcome"] == "ok"
    assert status["keywords"] == ["Pump"]
    assert client.post("/api/discovery/run").status_code == 409  # 15-minute gap


def test_api_fetch_reports_safety_stop(app, transport):
    client = TestClient(create_app(app, allowed_hosts=frozenset({"testserver"})))
    transport.queue(status=429)
    response = client.post("/api/bids/fetch", json={"link": "8506455"})
    assert response.status_code == 409
    assert client.post("/api/bids/fetch", json={"link": "8506455"}).status_code == 409
    assert len(transport.calls) == 1


def test_run_lends_its_guard_rails_to_other_steps(app):
    seen = []

    def execute(run):
        seen.append(run.keywords)
        run.message = "custom"

    run = app.discovery.run(trigger="corpus", keywords=["laptop", "a", "pump*", "Laptop"], execute=execute)
    assert (run.outcome, run.message, run.trigger) == ("ok", "custom", "corpus")
    assert seen == [["laptop"]]  # GeM's own keyword rules still apply


def test_given_keywords_that_are_all_unusable_are_refused(app, source):
    with pytest.raises(DiscoveryNotAllowed, match="keywords"):
        app.discovery.run(trigger="corpus", keywords=["a", "x*"], execute=lambda run: None)
    assert source.calls == []
