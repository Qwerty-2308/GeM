"""pypdf adapter for TextExtractor.

GeM bid PDFs are bilingual. The Hindi text uses a broken font mapping and
extracts as garbage, so lines containing Devanagari or control characters are
dropped (and counted) rather than guessed at. Parsing keys off English labels.
"""

from __future__ import annotations

import io
import re

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
