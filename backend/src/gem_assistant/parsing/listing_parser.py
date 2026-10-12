"""Parses the JSON that the /all-bids page's own search request returns.

Field names come from the page's JavaScript (saved in recon/fixtures). The JS
treats most fields as plain values but indexes b_category_name[0], and Solr
often returns single values as one-element arrays, so every field is accepted
as either. Dates are rendered by the page with getUTC*(), i.e. the timestamp
carries India wall-clock time with a fake "Z"; we re-attach +05:30.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain.models import BidKind, ListingBid

IST = timezone(timedelta(hours=5, minutes=30))
BID_NUMBER = re.compile(r"^GEM/\d{4}/[A-Z]/\d+$")
RA_TYPES = {2: "showradocumentPdf", 5: "showdirectradocumentPdf"}


class ListingFormatChanged(ValueError):
    """The response no longer has the shape we know. Stop rather than guess."""


class ListingRejected(ValueError):
    """GeM answered, but with a non-success code (e.g. expired token)."""


@dataclass(frozen=True)
class ListingPage:
    total_found: int
    start: int
    bids: list[ListingBid]
    skipped: int  # docs we couldn't read safely


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _text(value: Any) -> str | None:
    value = _first(value)
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _items(short: str | None, full: str | None) -> str | None:
    """b_category_name names each item once but stops at 99 characters; bd_category_name
    repeats every line item and stops at 1024. Merge them so an item past either cut
    still counts when matching the seller's categories."""
    first = [p.strip() for p in (short or "").split(",") if p.strip()]
    rest = [p.strip() for p in (full or "").split(",") if p.strip()]
    if first and any(p != first[-1] and p.startswith(first[-1]) for p in rest):
        first.pop()  # the item the 99-character cut sliced in half
    return ",".join(dict.fromkeys(first + rest)) or None


def _int(value: Any) -> int | None:
    value = _first(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _ist_wallclock(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=IST)


def parse_listing_response(data: Any) -> ListingPage:
    if not isinstance(data, dict) or "code" not in data:
        raise ListingFormatChanged("Listing response has no 'code'")
    if data.get("code") != 200:
        raise ListingRejected(f"GeM listing answered code {data.get('code')!r}")

    try:
        solr = data["response"]["response"]
        total = int(solr["numFound"])
        start = int(solr.get("start", 0))
        docs = solr["docs"]
    except (KeyError, TypeError, ValueError) as error:
        raise ListingFormatChanged(f"Listing response is missing {error}") from error
    if not isinstance(docs, list):
        raise ListingFormatChanged("Listing 'docs' is not a list")
    if total > start and not docs:
        raise ListingFormatChanged(f"GeM reports {total} results but returned none")

    bids: list[ListingBid] = []
    skipped = 0
    for doc in docs:
        bid = _parse_doc(doc)
        if bid is None:
            skipped += 1
        else:
            bids.append(bid)
    return ListingPage(total_found=total, start=start, bids=bids, skipped=skipped)


def _parse_doc(doc: Any) -> ListingBid | None:
    if not isinstance(doc, dict):
        return None
    doc_id = _text(doc.get("b_id"))
    bid_number = _text(doc.get("b_bid_number"))
    if not doc_id or not doc_id.isdigit() or not bid_number or not BID_NUMBER.match(bid_number):
        return None

    bid_type = _int(doc.get("b_bid_type"))
    kind = BidKind.RA if bid_type in RA_TYPES else BidKind.BID
    doc_path = RA_TYPES.get(bid_type, "showbidDocument")

    category = _items(_text(doc.get("b_category_name")), _text(doc.get("bd_category_name")))
    boq = _text(doc.get("bbt_title"))
    if category and boq:
        category = f"{category} ({boq})"

    return ListingBid(
        doc_id=doc_id,
        bid_number=bid_number,
        kind=kind,
        doc_path=doc_path,
        category=category,
        quantity=_text(doc.get("b_total_quantity")),
        ministry=_text(doc.get("ba_official_details_minName")),
        department=_text(doc.get("ba_official_details_deptName")),
        starts_at=_ist_wallclock(doc.get("final_start_date_sort")),
        ends_at=_ist_wallclock(doc.get("final_end_date_sort")),
    )
