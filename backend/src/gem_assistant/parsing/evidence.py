"""Checks that a quote really exists on the page it claims to come from.

PDF text breaks lines and spacing unpredictably, so comparison ignores
whitespace. Anything else must match exactly.
"""

from __future__ import annotations

import re
from typing import Sequence

from ..domain.models import Evidence, PageText

_WS = re.compile(r"\s+")


def _squash(text: str) -> str:
    return _WS.sub("", text)


def quote_is_on_page(evidence: Evidence, pages: Sequence[PageText]) -> bool:
    page = next((p for p in pages if p.number == evidence.page), None)
    if page is None:
        return False
    needle = _squash(evidence.quote)
    return bool(needle) and needle in _squash(page.text)
