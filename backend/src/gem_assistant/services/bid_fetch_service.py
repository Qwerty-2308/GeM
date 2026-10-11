"""Downloads a bid document from GeM and analyses it.

Only documents GeM itself linked to are fetched: either from a listing card
we read, or a showbidDocument link a person pasted. IDs are never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.models import DocStatus, ListingEntry
from ..domain.ports import ListingRepository
from ..infrastructure.pypdf_extractor import InvalidPdf
from ..safety.errors import UnexpectedResponse
from ..safety.response_classifier import Expect
from ..safety.safe_fetcher import SafeFetcher
from .bid_import_service import BidImportService, EvidenceMismatch, ImportResult

DOCUMENT_URL = "https://bidplus.gem.gov.in/showbidDocument/{doc_id}"
_LINK = re.compile(r"^https://bidplus\.gem\.gov\.in/showbidDocument/(\d{1,12})/?$")
_BARE_ID = re.compile(r"^\d{1,12}$")


class BadBidLink(ValueError):
    pass


@dataclass(frozen=True)
class FetchResult:
    import_result: ImportResult
    downloaded: bool  # False when we already had it and made no request
    pdf: bytes | None = None  # the downloaded document; None when nothing was downloaded


class BidFetchService:
    def __init__(self, fetcher: SafeFetcher, importer: BidImportService, listings: ListingRepository, bids):
        self._fetcher = fetcher
        self._importer = importer
        self._listings = listings
        self._bids = bids  # BidWriter (find_id_by_doc_id)

    @staticmethod
    def parse_link(link: str) -> str:
        text = link.strip()
        match = _LINK.match(text)
        if match:
            return match.group(1)
        if _BARE_ID.match(text):
            return text
        raise BadBidLink(
            "Paste a bid document link like https://bidplus.gem.gov.in/showbidDocument/8506455 "
            "(open the bid on GeM and copy the link of the bid number)."
        )

    def fetch_from_link(self, link: str) -> FetchResult:
        doc_id = self.parse_link(link)
        return self.fetch_document(doc_id, source_name=f"showbidDocument-{doc_id}.pdf")

    def fetch_document(self, doc_id: str, source_name: str) -> FetchResult:
        existing = self._bids.find_id_by_doc_id(doc_id)
        if existing is not None:  # cache: never download the same document twice
            return FetchResult(ImportResult(id=existing, already_imported=True), downloaded=False)
        response = self._fetcher.get(DOCUMENT_URL.format(doc_id=doc_id), expect=Expect.PDF)
        result = self._importer.import_pdf(response.body, source_name=source_name, doc_id=doc_id)
        return FetchResult(result, downloaded=True, pdf=response.body)

    def fetch_listing_entry(self, entry: ListingEntry) -> FetchResult | None:
        """Download and analyse one queued card, or None if this one document could not
        be handled. Problems with a single document are recorded and skipped; safety
        stops (blocks, budget, breaker) propagate and end the run."""
        try:
            result = self.fetch_document(entry.doc_id, source_name=f"{entry.bid_number.replace('/', '-')}.pdf")
        except UnexpectedResponse as error:
            self._listings.mark(entry.doc_id, DocStatus.FAILED, f"Download failed: {error}")
            return None
        except (InvalidPdf, EvidenceMismatch, ValueError) as error:
            self._listings.mark(entry.doc_id, DocStatus.FAILED, f"Could not analyse: {error}")
            return None
        self._listings.mark(entry.doc_id, DocStatus.DOWNLOADED, None, bid_id=result.import_result.id)
        return result
