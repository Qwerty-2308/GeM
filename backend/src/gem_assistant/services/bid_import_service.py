"""Turns a bid PDF into a stored BidAnalysis. Works on local files only — no network."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..domain.models import BidAnalysis, FieldStatus
from ..domain.ports import BidWriter, ClauseScanner, Clock, FieldExtractor, FieldReconciler, TextExtractor
from ..parsing.evidence import quote_is_on_page


class EvidenceMismatch(RuntimeError):
    """An extracted quote isn't on its page. Indicates a parser bug; never store it."""


@dataclass(frozen=True)
class ImportResult:
    id: int
    already_imported: bool


class BidImportService:
    def __init__(
        self,
        text_extractor: TextExtractor,
        field_extractor: FieldExtractor,
        clause_scanner: ClauseScanner,
        reconciler: FieldReconciler,
        bids: BidWriter,
        clock: Clock,
        max_bytes: int,
    ):
        self._text = text_extractor
        self._fields = field_extractor
        self._clauses = clause_scanner
        self._reconciler = reconciler
        self._bids = bids
        self._clock = clock
        self._max_bytes = max_bytes

    def import_pdf(self, pdf_bytes: bytes, source_name: str, doc_id: str | None = None) -> ImportResult:
        if len(pdf_bytes) > self._max_bytes:
            raise ValueError(f"File is larger than {self._max_bytes // (1024 * 1024)} MB")

        sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        existing = self._bids.find_id_by_sha256(sha256)
        if existing is not None:
            return ImportResult(id=existing, already_imported=True)

        pages = self._text.extract(pdf_bytes)
        flags = self._clauses.scan(pages)
        fields = self._reconciler.reconcile(self._fields.extract(pages), flags)

        for item in [*(f.evidence for f in fields if f.evidence), *(f.evidence for f in flags)]:
            if not quote_is_on_page(item, pages):
                raise EvidenceMismatch(f"Quote not found on page {item.page}: {item.quote[:80]!r}")

        bid_number_field = next((f for f in fields if f.name == "bid_number"), None)
        analysis = BidAnalysis(
            bid_number=bid_number_field.value
            if bid_number_field and bid_number_field.status is FieldStatus.FOUND else None,
            doc_id=doc_id,
            source_name=source_name,
            sha256=sha256,
            page_count=len(pages),
            unread_lines=sum(p.unread_lines for p in pages),
            imported_at=self._clock.now(),
            fields=fields,
            flags=flags,
        )
        return ImportResult(id=self._bids.save(analysis), already_imported=False)
