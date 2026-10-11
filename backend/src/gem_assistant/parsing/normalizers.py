"""Deterministic value normalisers. Pure functions, no LLM, no guessing.

Each raises ValueError when the raw text isn't what it should be, so a bad
value becomes UNCLEAR instead of being stored wrong.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

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


def shown_means_yes(raw: str) -> str:
    """The newer bid form lists the bank and the amount (or percentage) instead of
    printing "Required: Yes". A value shown there means the thing is required."""
    if not collapse_whitespace(raw):
        raise ValueError("empty")
    return "Yes"


def percent(raw: str) -> str:
    """'5.00' -> '5'. Rejects anything outside 0-100."""
    try:
        value = Decimal(collapse_whitespace(raw))
    except InvalidOperation as error:
        raise ValueError(f"not a percentage: {raw!r}") from error
    if not 0 < value <= 100:
        raise ValueError(f"percentage out of range: {raw!r}")
    return f"{value.normalize():f}"


def three_year_turnover_lakh(raw: str) -> str:
    """'3 Years)\n255 Lakh (s)' -> '25500000' (rupees). The seller profile holds a 3-year
    average, so a requirement averaged over any other period is UNCLEAR, not compared."""
    match = re.fullmatch(r"(\d+) Years?\) (\d+(?:\.\d{1,2})?) Lakh \(s\)", collapse_whitespace(raw))
    if match is None:
        raise ValueError(f"unexpected turnover format: {raw!r}")
    if match.group(1) != "3":
        raise ValueError(f"turnover averaged over {match.group(1)} years; the profile holds a 3-year average")
    return str(int(Decimal(match.group(2)) * 100_000))


def years(raw: str) -> str:
    """'5 Year (s)' -> '5'."""
    match = re.fullmatch(r"(\d+) Years? \(s\)", collapse_whitespace(raw))
    if match is None:
        raise ValueError(f"unexpected years format: {raw!r}")
    return str(int(match.group(1)))


def relaxation(raw: str) -> str:
    """'No', 'Yes', 'Yes | Complete' -> 'No'/'Yes'; 'Yes | Partial' -> 'Partial'. A partial
    relaxation lowers the threshold instead of removing it, so it must never read as 'Yes'."""
    value = collapse_whitespace(raw)
    if value == "No":
        return "No"
    if value in ("Yes", "Yes | Complete"):
        return "Yes"
    if value == "Yes | Partial":
        return "Partial"
    raise ValueError(f"unexpected relaxation value: {raw!r}")
