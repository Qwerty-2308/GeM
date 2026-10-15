"""Turns a bid PDF into a stored BidAnalysis, and re-reads stored bids when the rules
improve. Works on local files only — no network."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..domain.models import BidAnalysis, FieldStatus
from ..domain.ports import (
    BidReader, BidWriter, ClauseScanner, Clock, DocumentStore, FieldExtractor, FieldReconciler, TextExtractor,
)
from ..infrastructure.pypdf_extractor import InvalidPdf
from ..parsing.evidence import quote_is_on_page
from ..parsing.field_rules import PARSER_VERSION


class EvidenceMismatch(RuntimeError):
    """An extracted quote isn't on its page. Indicates a parser bug; never store it."""


@dataclass(frozen=True)
class ImportResult:
    id: int
    already_imported: bool


@dataclass(frozen=True)
class ReanalysisReport:
    updated: int      # re-read with the current rules
    without_pdf: int  # analysed before PDFs were kept; still on the old reading
    failed: int       # the kept PDF could not be read again; old reading kept


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
        documents: DocumentStore,
        bid_reader: BidReader,
    ):
        self._text = text_extractor
        self._fields = field_extractor
        self._clauses = clause_scanner
        self._reconciler = reconciler
        self._bids = bids
        self._clock = clock
        self._max_bytes = max_bytes
        self._documents = documents
        self._reader = bid_reader

    def import_pdf(self, pdf_bytes: bytes, source_name: str, doc_id: str | None = None) -> ImportResult:
        if len(pdf_bytes) > self._max_bytes:
            raise ValueError(f"File is larger than {self._max_bytes // (1024 * 1024)} MB")

        sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        existing = self._bids.find_id_by_sha256(sha256)
        if existing is not None:
            self._documents.save(sha256, pdf_bytes)  # brings back a PDF analysed before PDFs were kept
            return ImportResult(id=existing, already_imported=True)

        analysis = self._analyse(pdf_bytes, sha256, source_name, doc_id)
        self._documents.save(sha256, pdf_bytes)
        return ImportResult(id=self._bids.save(analysis), already_imported=False)

    def reanalyse_outdated(self) -> ReanalysisReport:
        """Re-read every stored bid analysed by older rules, from its kept PDF. The id,
        import time and GeM's corrigendum answer are kept; only the reading changes."""
        updated = without_pdf = failed = 0
        for bid in self._reader.list():
            if bid.parser_version >= PARSER_VERSION:
                continue
            pdf = self._documents.load(bid.sha256)
            if pdf is None:
                without_pdf += 1
                continue
            try:
                fresh = self._analyse(pdf, bid.sha256, bid.source_name, bid.doc_id)
            except (InvalidPdf, EvidenceMismatch, ValueError):
                failed += 1
                continue
            self._bids.save(fresh.model_copy(update={
                "id": bid.id, "imported_at": bid.imported_at, "corrigendum": bid.corrigendum,
            }))
            updated += 1
        return ReanalysisReport(updated=updated, without_pdf=without_pdf, failed=failed)

    def _analyse(self, pdf_bytes: bytes, sha256: str, source_name: str, doc_id: str | None) -> BidAnalysis:
        pages = self._text.extract(pdf_bytes)
        flags = self._clauses.scan(pages)
        fields = self._reconciler.reconcile(self._fields.extract(pages), flags)

        for item in [*(f.evidence for f in fields if f.evidence), *(f.evidence for f in flags)]:
            if not quote_is_on_page(item, pages):
                raise EvidenceMismatch(f"Quote not found on page {item.page}: {item.quote[:80]!r}")

        bid_number_field = next((f for f in fields if f.name == "bid_number"), None)
        return BidAnalysis(
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
            parser_version=PARSER_VERSION,
        )
