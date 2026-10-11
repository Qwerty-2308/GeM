"""Classifies a response as fine, harmless failure, or warning sign."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from ..domain.ports import HttpResponse


class ResponseKind(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"   # normal failure, no trip
    BLOCK = "block"           # warning sign, trip the breaker


class Expect(str, Enum):
    """What a successful response must look like."""

    HTML = "html"
    PDF = "pdf"
    JSON = "json"
    # A snippet of page content, not a whole page. Its words are written by buyers, so it
    # is judged by shape rather than by vocabulary — see classify().
    HTML_FRAGMENT = "html_fragment"


@dataclass(frozen=True)
class Classification:
    kind: ResponseKind
    reason: str


# Text seen on CAPTCHA pages, F5 / WAF block pages and F5 JavaScript challenges.
# Checked against the real /all-bids page in tests so it can't false-alarm on it.
CHALLENGE_MARKERS = (
    "captcha",
    "the requested url was rejected",
    "request rejected",
    "your support id is",
    "access denied",
    "are you a robot",
    "unusual traffic",
    "bobcmn",          # F5 ASM JavaScript challenge
    "/tspd/",          # F5 Bot Defense
    "enable javascript",
)

BLOCK_STATUSES = {401, 403, 407, 429}

# Marks of a whole HTML page. Anything that should be a fragment but looks like a full
# document is a page GeM served in place of the content we asked for.
DOCUMENT_MARKERS = ("<!doctype", "<html", "<head", "<body")


class ResponseClassifier:
    def classify(self, response: HttpResponse, expect: Expect = Expect.HTML) -> Classification:
        status = response.status
        if status in BLOCK_STATUSES:
            return Classification(ResponseKind.BLOCK, f"GeM answered HTTP {status}")
        if status >= 500:
            return Classification(ResponseKind.BLOCK, f"GeM server error HTTP {status}")
        if 300 <= status < 400:
            location = response.headers.get("location", "?")
            return Classification(ResponseKind.BLOCK, f"Unexpected redirect to {location} (possible login wall)")
        if status in (404, 410):
            return Classification(ResponseKind.NOT_FOUND, f"HTTP {status}")
        if status != 200:
            return Classification(ResponseKind.BLOCK, f"Unexpected HTTP {status}")

        if expect is Expect.PDF:
            if response.body.startswith(b"%PDF-"):
                return Classification(ResponseKind.OK, "pdf")
            return Classification(ResponseKind.BLOCK, "Expected a PDF but got something else (possible challenge page)")

        if expect is Expect.JSON:
            try:
                parsed = json.loads(response.body)
            except ValueError:
                return Classification(ResponseKind.BLOCK, "Expected JSON but got something else (possible challenge page)")
            if not isinstance(parsed, dict):
                return Classification(ResponseKind.BLOCK, "Expected a JSON object from GeM")
            return Classification(ResponseKind.OK, "json")

        head = response.body[:65536].decode("utf-8", errors="ignore").lower()

        if expect is Expect.HTML_FRAGMENT:
            # Buyers write this text, and a corrigendum may legitimately say "access
            # denied to unempanelled vendors" or name a CAPTCHA product. Matching on those
            # words would stop all fetching over an ordinary sentence. A real challenge or
            # block page is a whole document, so that is what we look for instead.
            if any(marker in head for marker in DOCUMENT_MARKERS):
                return Classification(ResponseKind.BLOCK,
                                      "Expected a snippet of a page but got a whole page "
                                      "(possible challenge or block page)")
            return Classification(ResponseKind.OK, "fragment")

        for marker in CHALLENGE_MARKERS:
            if marker in head:
                return Classification(ResponseKind.BLOCK, f"Page looks like a challenge or block page ('{marker}')")
        return Classification(ResponseKind.OK, "ok")
