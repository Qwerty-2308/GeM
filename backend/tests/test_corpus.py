"""The corpus command: collect real bid documents, and probe buyer attachments."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from gem_assistant.container import Container
from gem_assistant.domain.models import BidKind, DocStatus, ListingEntry, SellerProfile
from gem_assistant.infrastructure.pypdf_extractor import pdf_links
from gem_assistant.services.corpus_service import pick_varied
from gem_assistant.services.discovery_service import DiscoveryNotAllowed

from .conftest import SAMPLE_PDF
from .listing_fixtures import make_doc
from .test_discovery import FakeSource

T = datetime(2025, 11, 1, tzinfo=timezone.utc)


def test_pdf_links_find_the_buyer_attachment_and_spec_document():
    links = pdf_links(SAMPLE_PDF.read_bytes())
    atc = [l for l in links if l.url.startswith("https://fulfilment.gem.gov.in/contract/slafds?")]
    assert [l.page for l in atc] == [6]
    assert any(l.page == 5 and "SpecificationDocument" in l.url for l in links)
    assert len(links) == len(set(links))  # page 3 links the same URL twice


def _entry(n: int, keyword: str, ministry: str) -> ListingEntry:
    return ListingEntry(doc_id=str(n), bid_number=f"GEM/2026/B/{n}", kind=BidKind.BID,
                        doc_path="showbidDocument", keyword=keyword, ministry=ministry,
                        first_seen_at=T, last_seen_at=T, doc_status=DocStatus.PENDING)


def test_pick_varied_rotates_keywords_and_spreads_ministries():
    entries = [_entry(1, "pump", "Railways"), _entry(2, "pump", "Railways"), _entry(3, "pump", "Defence"),
               _entry(4, "laptop", "Railways"), _entry(5, "laptop", "Health")]
    assert [e.doc_id for e in pick_varied(entries, 4)] == ["1", "5", "3", "4"]
    assert len(pick_varied(entries, 99)) == 5
    assert pick_varied(entries, 0) == []


# ---------- collect ----------

DEFENCE = "Ministry of Defence"


@pytest.fixture
def source() -> FakeSource:
    return FakeSource(docs=[
        make_doc(1, final_end_date_sort="2026-10-20T15:00:00Z"),
        make_doc(2, final_end_date_sort="2026-10-21T15:00:00Z", ba_official_details_minName=DEFENCE),
        make_doc(3, bid_type=5),  # reverse auction: never downloaded
    ])


@pytest.fixture
def app(settings, clock, transport, source) -> Container:
    c = Container.build(settings, clock=clock, transport=transport, listing_source_factory=source.bind)
    c.profiles.save(SellerProfile(business_name="Test", categories=["Pump"]))
    return c


def _queue_pdf(transport):
    transport.queue(body=SAMPLE_PDF.read_bytes(), headers={"content-type": "application/pdf"})


def test_collect_saves_pdfs_and_a_manifest(app, transport):
    _queue_pdf(transport)
    _queue_pdf(transport)
    result = app.corpus.collect(["pump"], limit=5)
    assert (result.run.outcome, result.run.trigger) == ("ok", "corpus"), result.run.message

    manifest = json.loads((result.folder / "manifest.json").read_text())
    assert [row["doc_id"] for row in manifest] == ["9000001", "9000002"]
    assert (result.folder / "pdf" / "9000001.pdf").read_bytes() == SAMPLE_PDF.read_bytes()
    row = manifest[0]
    assert (row["keyword"], row["pages"], row["corrigendum"]) == ("pump", 10, "NONE")
    assert any(link["url"].startswith("https://fulfilment.gem.gov.in/") for link in row["links"])
    assert manifest[1]["ministry"] == DEFENCE
    assert "Saved 2 bid documents" in result.run.message


def test_collect_refuses_when_todays_budget_cannot_cover_it(settings, clock, transport, source):
    tight = replace(settings, safety=replace(settings.safety, daily_request_limit=20))
    c = Container.build(tight, clock=clock, transport=transport, listing_source_factory=source.bind)
    with pytest.raises(DiscoveryNotAllowed, match="up to 44 requests"):  # 1 + 1x2 + 30 + 1 + 10: limit clamped
        c.corpus.collect(["pump"], limit=1000)
    assert source.calls == [] and transport.calls == []


def test_a_corpus_run_counts_toward_the_gap_between_searches(app, transport):
    _queue_pdf(transport)
    app.corpus.collect(["pump"], limit=1)
    with pytest.raises(DiscoveryNotAllowed, match="15 minutes"):
        app.discovery.run()


def test_the_manifest_survives_a_safety_stop(app, transport):
    _queue_pdf(transport)
    transport.queue(status=403, body=b"denied")
    result = app.corpus.collect(["pump"], limit=5)
    assert result.run.outcome == "stopped"
    manifest = json.loads((result.folder / "manifest.json").read_text())
    assert [row["doc_id"] for row in manifest] == ["9000001"]
    assert app.safety.status().breaker.open
