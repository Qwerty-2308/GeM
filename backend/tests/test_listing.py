"""Listing JSON parsing, listing storage, and the headless-browser source."""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from gem_assistant.domain.models import BidKind, CorrigendumStatus, DocStatus, item_match
from gem_assistant.infrastructure.browser_listing import PlaywrightListingSource
from gem_assistant.infrastructure.database import Database
from gem_assistant.infrastructure.sqlite_repositories import SqliteListingRepository
from gem_assistant.parsing.listing_parser import (
    ListingFormatChanged, ListingRejected, parse_listing_response,
)
from gem_assistant.safety.errors import BlockedByGeM, FetchingStopped

from .conftest import ALL_BIDS_SHELL, FIXTURES
from .listing_fixtures import FakeGeM, make_doc, make_response

IST = timezone(timedelta(hours=5, minutes=30))


# ---------- parser ----------

@pytest.mark.parametrize("arrays", [False, True])
def test_parses_scalar_and_array_fields(arrays):
    page = parse_listing_response(make_response([make_doc(1, arrays=arrays)]))
    (bid,) = page.bids
    assert bid.doc_id == "9000001"
    assert bid.bid_number == "GEM/2026/B/7000001"
    assert bid.kind is BidKind.BID and bid.doc_path == "showbidDocument"
    assert bid.category == "Centrifugal Pump Type 1"
    assert bid.quantity == "11"
    # page shows these with getUTC*(): the value is India wall-clock time
    assert bid.ends_at == datetime(2026, 10, 20, 15, 0, tzinfo=IST)


def test_item_list_is_merged_from_the_short_and_the_full_field():
    """GeM's b_category_name lists each item once but stops at 99 characters;
    bd_category_name repeats every line item and stops at 1024. Neither is whole alone."""
    doc = make_doc(1, b_category_name=["Office Table,Office Ch"],
                   bd_category_name=["Office Table,Office Table,Office Chair,Visitor Chair"],
                   bbt_title=["Office Infra"])
    (bid,) = parse_listing_response(make_response([doc])).bids
    assert bid.category == "Office Table,Office Chair,Visitor Chair"


def test_bid_title_is_not_counted_as_an_item():
    """GeM's bid title often repeats the first item ("PLYWOOD GEN PURPOSE"). Glued onto the
    last item it made a plywood-and-adhesive bid look all plywood."""
    doc = make_doc(1, b_category_name=["Plywood,Adhesive"], bd_category_name=["Plywood,Adhesive"],
                   bbt_title=["Plywood"])
    (bid,) = parse_listing_response(make_response([doc])).bids
    assert item_match(bid.category, ["plywood"]) == (1, 2)


def test_reverse_auctions_are_marked_ra():
    page = parse_listing_response(make_response([make_doc(1, bid_type=5), make_doc(2, bid_type=2)]))
    assert [b.kind for b in page.bids] == [BidKind.RA, BidKind.RA]
    assert page.bids[0].doc_path == "showdirectradocumentPdf"


def test_unsafe_docs_are_skipped_not_guessed():
    docs = [make_doc(1, b_id="12;DROP"), make_doc(2, b_bid_number="../../etc"), make_doc(3)]
    page = parse_listing_response(make_response(docs))
    assert [b.doc_id for b in page.bids] == ["9000003"]
    assert page.skipped == 2


def test_non_success_code_is_rejected():
    with pytest.raises(ListingRejected):
        parse_listing_response(make_response([], code=403))


@pytest.mark.parametrize("data", [
    {"status": "ok"},
    {"code": 200, "response": {}},
    {"code": 200, "response": {"response": {"numFound": 5, "start": 0, "docs": []}}},
])
def test_unknown_shape_stops_instead_of_guessing(data):
    with pytest.raises(ListingFormatChanged):
        parse_listing_response(data)


# ---------- storage ----------

def test_upsert_tracks_new_and_pending(tmp_path):
    db = Database(tmp_path / "t.sqlite3"); db.migrate()
    repo = SqliteListingRepository(db)
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    bids = parse_listing_response(make_response([make_doc(1), make_doc(2, bid_type=5)])).bids

    assert repo.upsert(bids, "pump", now) == ["9000001", "9000002"]
    assert repo.upsert(bids, "pump", now) == []  # seen before
    pending = repo.pending_downloads(10, now)
    assert [p.doc_id for p in pending] == ["9000001"]  # RA is never queued
    assert repo.recent(10)[1].doc_status in (DocStatus.SKIPPED, DocStatus.PENDING)

    repo.mark("9000001", DocStatus.FAILED, "404")
    assert repo.pending_downloads(10, now) == []


def test_closed_bids_are_not_queued(tmp_path):
    db = Database(tmp_path / "t.sqlite3"); db.migrate()
    repo = SqliteListingRepository(db)
    bids = parse_listing_response(make_response([make_doc(1)])).bids
    after_close = datetime(2026, 10, 21, tzinfo=timezone.utc)
    repo.upsert(bids, "pump", after_close)
    assert repo.pending_downloads(10, after_close) == []


# ---------- headless browser against the saved GeM page ----------

def _chromium() -> str | None:
    if os.environ.get("GEM_CHROMIUM_PATH"):
        return os.environ["GEM_CHROMIUM_PATH"]
    found = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")
    return found[0] if found else None


def _browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch(executable_path=_chromium()).close()
    except Exception:
        return False
    return True


browser_test = pytest.mark.skipif(
    not _browser_available(), reason="Chromium not available (run: uv run playwright install chromium)")


@pytest.fixture
def fake_gem():
    pumps = [make_doc(i) for i in range(1, 24)]  # 23 results -> 3 pages
    return FakeGeM(ALL_BIDS_SHELL.read_bytes(), {"pump": pumps, "valve": [make_doc(100)]})


@pytest.fixture
def source(container, fake_gem, tmp_path):
    return PlaywrightListingSource(
        fetcher=container.fetcher, network=fake_gem, executable_path=_chromium(),
        timeout_seconds=20, save_raw_to=tmp_path / "raw.json",
    )


@browser_test
def test_one_bids_corrigendum_trouble_stays_with_that_bid(container, tmp_path):
    """Each bid gets its own answer. A network failure on the first must not decide the
    verdict for every bid after it."""
    gem = FakeGeM(ALL_BIDS_SHELL.read_bytes(), {},
                  corrigendum_bodies={"222": b"<p>Deadline extended</p>"})
    gem.corrigendum_errors = {"111"}
    src = PlaywrightListingSource(fetcher=container.fetcher, network=gem,
                                  executable_path=_chromium(), timeout_seconds=20)

    result = src.corrigenda(["111", "222"])

    assert result["111"].status is CorrigendumStatus.FAILED
    assert result["222"].status is CorrigendumStatus.PRESENT
    assert "Deadline extended" in result["222"].text
    # the second bid was really asked about, not written off on the first one's error
    asked = [u.rsplit("/", 1)[-1] for m, u, _ in gem.requests if "viewcorrigendum" in u.lower()]
    assert asked == ["111", "222"]


@browser_test
def test_browser_search_reads_results_from_the_page_request(source, fake_gem, container, tmp_path):
    (result,) = source.search(["pump"], max_pages=2)
    assert result.total_found == 23
    assert len(result.bids) == 20  # capped at 2 pages
    assert result.bids[0].bid_number == "GEM/2026/B/7000001"
    assert (tmp_path / "raw.json").exists()

    methods = [(m, u.split("gem.gov.in")[1]) for m, u, _ in fake_gem.requests]
    # exactly: the page, then one search request per results page.
    # The page's automatic "show everything" search on load was aborted locally.
    assert methods == [("GET", "/all-bids"), ("POST", "/all-bids-data"), ("POST", "/all-bids-data")]
    assert [p["param"]["searchBid"] for _, _, p in fake_gem.requests[1:]] == ["pump", "pump"]
    assert container.safety.status().used_today == 3


@browser_test
def test_browser_respects_rate_limit(source, clock, settings):
    source.search(["pump", "valve"], max_pages=1)
    # 3 requests (page + 2 searches): two waits of the minimum interval
    assert clock.slept.count(settings.safety.min_interval_seconds) == 2


@browser_test
def test_block_page_trips_breaker_and_stops_browser(source, fake_gem, container):
    from gem_assistant.domain.ports import HttpResponse
    fake_gem.override = lambda route: HttpResponse(url="", status=403, headers={}, body=b"")
    with pytest.raises(BlockedByGeM):
        source.search(["pump"], max_pages=1)
    assert len(fake_gem.requests) == 1
    with pytest.raises(FetchingStopped):
        source.search(["pump"], max_pages=1)
    assert len(fake_gem.requests) == 1  # nothing left the machine


@browser_test
def test_challenge_instead_of_json_trips_breaker(source, fake_gem, container):
    from gem_assistant.domain.ports import HttpResponse
    original = fake_gem.fetch

    def challenge_on_search(route):
        if route.request.method == "POST":
            return HttpResponse(url="", status=200, headers={}, body=b"<html>window['bobcmn']</html>")
        return HttpResponse(url="", status=200, headers={}, body=fake_gem.shell_html)

    fake_gem.override = challenge_on_search
    with pytest.raises(BlockedByGeM):
        source.search(["pump"], max_pages=1)
    assert container.safety.status().breaker.open


@browser_test
def test_changed_page_is_detected(container, tmp_path):
    gem = FakeGeM(b"<html><body>Site under maintenance</body></html>")
    src = PlaywrightListingSource(fetcher=container.fetcher, network=gem,
                                  executable_path=_chromium(), timeout_seconds=10)
    with pytest.raises(ListingFormatChanged):
        src.search(["pump"], max_pages=1)


@browser_test
def test_full_discovery_run_with_real_browser(settings, clock, transport, tmp_path):
    """Browser search -> store -> download PDF -> analyse, all offline."""
    from gem_assistant.container import Container
    from gem_assistant.domain.models import SellerProfile

    from .conftest import SAMPLE_PDF

    sample = make_doc(0, b_id=8506455, b_bid_number="GEM/2025/B/6819518", b_category_name=["Clip Assy"],
                      final_end_date_sort="2025-11-14T12:00:00Z")
    gem = FakeGeM(
        ALL_BIDS_SHELL.read_bytes(),
        {"Clip assy": [sample, make_doc(1, bid_type=5)]},
        # The bid we analyse has been amended; the reverse auction is never fetched.
        corrigendum_bodies={"8506455": b"<div><b>Bid End Date</b> extended to 20-11-2025</div>"},
    )
    c = Container.build(settings, clock=clock, transport=transport, listing_source_factory=lambda f:
                        PlaywrightListingSource(fetcher=f, network=gem, executable_path=_chromium(),
                                                timeout_seconds=20, clock=clock))
    c.profiles.save(SellerProfile(categories=["Clip assy"]))
    transport.queue(body=SAMPLE_PDF.read_bytes())

    run = c.discovery.run()
    assert run.outcome == "ok", run.message
    # page + search + 1 PDF, then the corrigendum pass: page + 1 corrigendum
    assert (run.found, run.new, run.downloaded, run.corrigenda_checked, run.requests) == (2, 2, 1, 1, 5)
    bids = c.bid_query.list()
    assert [b.bid.bid_number for b in bids] == ["GEM/2025/B/6819518"]

    # The corrigendum is stored as readable text and pushed to the human, never parsed
    # into a new deadline.
    (assessed,) = bids
    assert assessed.bid.corrigendum.status is CorrigendumStatus.PRESENT
    assert "extended to 20-11-2025" in assessed.bid.corrigendum.text
    assert any(r.rule == "corrigendum" for r in assessed.assessment.results)
    assert assessed.bid.found_value("bid_end") == "2025-11-14T12:00:00+05:30"  # unchanged by us


@browser_test
def test_real_corrigendum_fragments_are_kept_raw(container, tmp_path):
    """Nobody has seen a real fragment's structure yet, so every one we meet is kept."""
    body = b"<div><b>Bid end date</b> extended</div>"
    gem = FakeGeM(ALL_BIDS_SHELL.read_bytes(), {}, corrigendum_bodies={"222": body})
    src = PlaywrightListingSource(fetcher=container.fetcher, network=gem, executable_path=_chromium(),
                                  timeout_seconds=20, save_corrigenda_to=tmp_path / "corr")
    src.corrigenda(["111", "222"])  # 111 answers "0": nothing to keep
    assert sorted(p.name for p in (tmp_path / "corr").iterdir()) == ["222.html"]
    assert (tmp_path / "corr" / "222.html").read_bytes() == body


def test_a_real_listing_response_parses():
    """Captured in the first live run (11-Oct-2026; creator field redacted). Every field
    arrives as a one-element list, which the parser already accepted."""
    data = json.loads((FIXTURES / "all-bids-data_2026-10-11.json").read_text())
    page = parse_listing_response(data)
    assert (page.total_found, len(page.bids), page.skipped) == (231, 10, 0)
    assert all(b.bid_number.startswith("GEM/") and b.doc_id.isdigit() and b.ends_at for b in page.bids)
