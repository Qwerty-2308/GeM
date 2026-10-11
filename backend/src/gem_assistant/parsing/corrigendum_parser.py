"""Reads GeM's corrigendum response.

Contract observed in the saved /all-bids page (docs/02-RECON-FINDINGS.md §4): the
response body is `0` when a bid has no corrigendum, otherwise an HTML fragment the page
drops into a modal.

The fragment's internal structure has never been captured, so this module does not look
for a revised deadline, quantity or specification inside it. It turns the fragment into
plain readable text and nothing more. A person reads it and decides.
"""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser

from ..domain.models import Corrigendum, CorrigendumStatus

# The page's own test is `if (data == 0)`, and in JavaScript both "0" and "" equal 0,
# so an empty body means "no corrigendum" exactly as "0" does.
_MEANS_NONE = frozenset({"", "0"})

# These never hold text meant for a reader, and we are not going to show code.
_SKIP_CONTENT = frozenset({"script", "style", "noscript", "template"})

# Tags whose boundaries are worth a line break when flattening to text.
_BLOCK = frozenset({
    "address", "article", "aside", "blockquote", "br", "div", "dd", "dl", "dt", "footer",
    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre",
    "section", "table", "tbody", "td", "th", "thead", "tr", "ul",
})

MAX_TEXT_CHARS = 20_000


class _TextCollector(HTMLParser):
    """Flattens an HTML fragment to readable text. convert_charrefs (the default) means
    handle_data already receives unescaped text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _SKIP_CONTENT:
            self._suppress += 1
        elif tag in _BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_CONTENT:
            self._suppress = max(0, self._suppress - 1)
        elif tag in _BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppress == 0:
            self._parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._parts).splitlines())
        return "\n".join(line for line in lines if line)


def html_to_text(fragment: str) -> str:
    collector = _TextCollector()
    collector.feed(fragment)
    collector.close()
    return collector.text()


def parse_corrigendum_response(body: bytes, checked_at: datetime) -> Corrigendum:
    """Turn a /bidding/bid/viewCorrigendum response into a Corrigendum.

    Anything unreadable becomes FAILED, never NONE: reporting "no corrigendum" when we
    simply could not tell is the one wrong answer that matters here.
    """
    raw = body.decode("utf-8", errors="replace")
    if raw.strip() in _MEANS_NONE:
        return Corrigendum(status=CorrigendumStatus.NONE, checked_at=checked_at)

    text = html_to_text(raw)
    if not text:
        return Corrigendum(
            status=CorrigendumStatus.FAILED,
            checked_at=checked_at,
            note="GeM returned a corrigendum but no readable text could be taken from it. "
                 "Open the bid on GeM and read it there.",
        )

    truncated = len(text) > MAX_TEXT_CHARS
    return Corrigendum(
        status=CorrigendumStatus.PRESENT,
        checked_at=checked_at,
        text=text[:MAX_TEXT_CHARS],
        note="Shown shortened; read the full corrigendum on GeM." if truncated else None,
    )
