"""Deterministic value normalisers. Pure functions, no LLM, no guessing.

Each raises ValueError when the raw text isn't what it should be, so a bad
value becomes UNCLEAR instead of being stored wrong.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def text(raw: str) -> str:
    value = collapse_whitespace(raw)
    if not value:
        raise ValueError("empty")
    return value


def yes_no(raw: str) -> str:
    value = collapse_whitespace(raw).lower()
    if value in ("yes", "no"):
        return value.capitalize()
    raise ValueError(f"expected Yes/No, got {raw!r}")


def integer(raw: str) -> str:
    digits = collapse_whitespace(raw).replace(",", "")
    if not re.fullmatch(r"\d+", digits):
        raise ValueError(f"not an integer: {raw!r}")
    return str(int(digits))


def ist_datetime(raw: str) -> str:
    """'14-11-2025 12:00:00' (DD-MM-YYYY, India time) -> ISO 8601 with +05:30."""
    value = collapse_whitespace(raw)
    parsed = datetime.strptime(value, "%d-%m-%Y %H:%M:%S").replace(tzinfo=IST)
    return parsed.isoformat()


def ist_date(raw: str) -> str:
    value = collapse_whitespace(raw)
    return datetime.strptime(value, "%d-%m-%Y").date().isoformat()


def rupees(raw: str) -> str:
    """'Rs. 5,00,000/-' or '₹ 50,000' -> '500000' (whole rupees, as text). Never a float."""
    value = collapse_whitespace(raw)
    match = re.search(r"(?:rs\.?|inr|₹)?\s*([\d,]+)(?:\.(\d{1,2}))?", value, re.IGNORECASE)
    if not match:
        raise ValueError(f"no amount in {raw!r}")
    whole = match.group(1).replace(",", "")
    if not whole.isdigit():
        raise ValueError(f"bad amount {raw!r}")
    return str(int(whole))
