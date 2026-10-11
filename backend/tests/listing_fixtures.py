"""Test doubles for the /all-bids-data response.

IMPORTANT: this JSON is CONSTRUCTED from the field names the real page's
JavaScript reads (see recon/fixtures/all-bids-shell_2026-09-13.html). It has
not been captured from GeM. The first live run saves a real response to
data/recon/last-listing-response.json — compare against it and replace this.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs


def make_doc(n: int, *, arrays: bool = False, bid_type: int = 1, **overrides) -> dict:
    doc = {
        "b_id": 9000000 + n,
        "b_bid_number": f"GEM/2026/B/{7000000 + n}",
        "b_bid_type": bid_type,
        "b_category_name": [f"Centrifugal Pump Type {n}"],
        "bbt_title": None,
        "b_total_quantity": 10 + n,
        "ba_official_details_minName": "Ministry of Railways",
        "ba_official_details_deptName": "Northern Railway",
        "final_start_date_sort": "2026-10-01T10:00:00Z",
        "final_end_date_sort": "2026-10-20T15:00:00Z",
    }
    if arrays:  # Solr-style single-element arrays
        for key in ("b_id", "b_bid_number", "b_bid_type", "b_total_quantity",
                    "ba_official_details_minName", "ba_official_details_deptName",
                    "final_start_date_sort", "final_end_date_sort"):
            doc[key] = [doc[key]]
    doc.update(overrides)
    return doc


def make_response(docs: list[dict], total: int | None = None, start: int = 0, code: int = 200) -> dict:
    return {"code": code, "response": {"response": {
        "numFound": len(docs) if total is None else total, "start": start, "docs": docs}}}


class FakeGeM:
    """Stands in for GeM behind the browser: serves the saved /all-bids page and
    answers the page's own search request from an in-memory catalogue."""

    def __init__(self, shell_html: bytes, catalogue: dict[str, list[dict]] | None = None,
                 corrigendum_bodies: dict[str, bytes] | None = None):
        self.shell_html = shell_html
        self.catalogue = catalogue or {}
        # doc_id -> response body. Missing ids answer "0" = no corrigendum, as GeM does.
        self.corrigendum_bodies = corrigendum_bodies or {}
        # doc_ids whose request blows up, to check one bid's trouble stays with that bid.
        self.corrigendum_errors: set[str] = set()
        self.requests: list[tuple[str, str, dict | None]] = []
        self.override = None  # callable(route) -> HttpResponse, to simulate blocks

    def fetch(self, route):
        from gem_assistant.domain.ports import HttpResponse

        request = route.request
        is_corrigendum = "/viewcorrigendum/" in request.url.lower()
        payload = None
        if request.method == "POST":
            form = parse_qs(request.post_data or "")
            assert form.get("csrf_bd_gem_nk"), "page must send its CSRF token"
            # The corrigendum request sends only the token; the search sends a payload.
            if not is_corrigendum:
                payload = json.loads(form["payload"][0])
        self.requests.append((request.method, request.url, payload))

        if self.override is not None:
            return self.override(route)
        if request.method == "GET":
            return HttpResponse(url=request.url, status=200, headers={"content-type": "text/html"},
                                body=self.shell_html)
        if is_corrigendum:
            doc_id = request.url.rstrip("/").rsplit("/", 1)[-1]
            if doc_id in self.corrigendum_errors:
                raise ConnectionError(f"network died on {doc_id}")
            return HttpResponse(url=request.url, status=200, headers={"content-type": "text/html"},
                                body=self.corrigendum_bodies.get(doc_id, b"0"))

        keyword = payload["param"]["searchBid"]
        page = int(payload.get("page") or 1)
        docs = self.catalogue.get(keyword, [])
        start = (page - 1) * 10
        body = make_response(docs[start:start + 10], total=len(docs), start=start)
        return HttpResponse(url=request.url, status=200, headers={"content-type": "application/json"},
                            body=json.dumps(body).encode())
