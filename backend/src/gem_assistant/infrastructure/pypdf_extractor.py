"""pypdf adapter for TextExtractor.

GeM bid PDFs are bilingual. The Hindi text uses a broken font mapping and
extracts as garbage, so lines containing Devanagari or control characters are
dropped (and counted) rather than guessed at. Parsing keys off English labels.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader

from ..domain.models import PageText

_UNREADABLE = re.compile(r"[ऀ-ॿ\x00-\x08\x0b-\x1f]")


class InvalidPdf(ValueError):
    pass


class PypdfTextExtractor:
    def __init__(self, max_pages: int):
        self._max_pages = max_pages

    def extract(self, pdf_bytes: bytes) -> list[PageText]:
        if not pdf_bytes.startswith(b"%PDF-"):
            raise InvalidPdf("This file is not a PDF")
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            if reader.is_encrypted:
                raise InvalidPdf("Encrypted PDFs are not supported")
            if len(reader.pages) > self._max_pages:
                raise InvalidPdf(f"PDF has {len(reader.pages)} pages; the limit is {self._max_pages}")
            raw_pages = [page.extract_text() or "" for page in reader.pages]
        except InvalidPdf:
            raise
        except Exception as error:
            raise InvalidPdf(f"Could not read PDF: {error}") from error

        return [self._clean(number, text) for number, text in enumerate(raw_pages, start=1)]

    @staticmethod
    def _clean(number: int, text: str) -> PageText:
        kept: list[str] = []
        unread = 0
        for line in text.replace(" ", " ").splitlines():
            if _UNREADABLE.search(line):
                unread += 1
                continue
            stripped = line.strip()
            if stripped:
                kept.append(stripped)
        return PageText(number=number, text="\n".join(kept), unread_lines=unread)


@dataclass(frozen=True)
class PdfLink:
    page: int
    url: str


def pdf_links(pdf_bytes: bytes) -> list[PdfLink]:
    """Web links embedded in the PDF as link annotations. GeM puts the buyer's uploaded
    terms file and specification document here, not in the page text."""
    found: dict[PdfLink, None] = {}
    for number, page in enumerate(PdfReader(io.BytesIO(pdf_bytes)).pages, start=1):
        for annotation in page.get("/Annots") or []:
            action = annotation.get_object().get("/A")
            uri = action.get_object().get("/URI") if action else None
            if isinstance(uri, str) and uri:
                found[PdfLink(number, str(uri))] = None
    return list(found)
