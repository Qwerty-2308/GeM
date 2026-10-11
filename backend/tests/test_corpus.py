"""The corpus command: collect real bid documents, and probe buyer attachments."""

from __future__ import annotations

from datetime import datetime, timezone

from gem_assistant.domain.models import BidKind, DocStatus, ListingEntry
from gem_assistant.infrastructure.pypdf_extractor import pdf_links
from gem_assistant.services.corpus_service import pick_varied

from .conftest import SAMPLE_PDF

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
