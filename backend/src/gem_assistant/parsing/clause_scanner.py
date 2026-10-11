"""Scans the buyer-added terms section for clauses worth a human's attention."""

from __future__ import annotations

import re
from typing import Sequence

from ..domain.models import ClauseFlag, Evidence, ExtractionMethod, PageText
from .clause_patterns import ATTACHMENT_MARKER, DEFAULT_CLAUSE_PATTERNS, ClausePattern
from .normalizers import collapse_whitespace

SECTION_START = re.compile(r"/Buyer Added Bid Specific Terms and Conditions", re.IGNORECASE)
SECTION_END = re.compile(r"^/Disclaimer", re.IGNORECASE | re.MULTILINE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")


class PatternClauseScanner:
    def __init__(self, patterns: Sequence[ClausePattern] = DEFAULT_CLAUSE_PATTERNS):
        self._patterns = tuple(patterns)

    def scan(self, pages: Sequence[PageText]) -> list[ClauseFlag]:
        flags: list[ClauseFlag] = []
        seen: set[tuple[str, str]] = set()
        for page_number, section_text in self._section_by_page(pages):
            if ATTACHMENT_MARKER.search(section_text):
                flags.append(ClauseFlag(
                    category="unread_attachment",
                    label="Buyer attached a separate ATC document (not read by this tool)",
                    evidence=Evidence(page=page_number, quote=ATTACHMENT_MARKER.search(section_text).group(0),
                                      method=ExtractionMethod.PATTERN_SCAN),
                ))
            for sentence in self._sentences(section_text):
                for p in self._patterns:
                    key = (p.category, sentence)
                    if key in seen or not p.pattern.search(sentence):
                        continue
                    seen.add(key)
                    flags.append(ClauseFlag(
                        category=p.category,
                        label=p.label,
                        evidence=Evidence(page=page_number, quote=sentence, method=ExtractionMethod.PATTERN_SCAN),
                    ))
        return flags

    @staticmethod
    def _section_by_page(pages: Sequence[PageText]) -> list[tuple[int, str]]:
        """Return (page, text) slices that lie inside the buyer-added terms section."""
        result: list[tuple[int, str]] = []
        inside = False
        for page in pages:
            text = page.text
            start = 0
            if not inside:
                match = SECTION_START.search(text)
                if not match:
                    continue
                inside, start = True, match.end()
            end_match = SECTION_END.search(text, start)
            if end_match:
                result.append((page.number, text[start:end_match.start()]))
                break
            result.append((page.number, text[start:]))
        return result

    @staticmethod
    def _sentences(text: str) -> list[str]:
        flat = collapse_whitespace(text)
        return [s.strip() for s in _SENTENCE_SPLIT.split(flat) if len(s.strip()) > 3]
