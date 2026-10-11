"""What to extract from the structured part of a GeM bid document.

To support a new field, add a FieldRule here. The extractor itself never
changes (Open/Closed). Labels come from real documents in recon/fixtures;
don't add one you haven't seen in an actual bid PDF.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from . import normalizers as n

# Matches the whitespace/newlines pypdf puts between label words.
_ = r"\s+"


@dataclass(frozen=True)
class FieldRule:
    name: str
    label: str
    pattern: re.Pattern[str]  # must contain a named group 'value'
    normalize: Callable[[str], str]
    critical: bool = False


def rule(name: str, label: str, regex: str, normalize: Callable[[str], str], critical: bool = False) -> FieldRule:
    return FieldRule(name, label, re.compile(regex), normalize, critical)


_LINE = r"(?P<value>[^\n]+)"
_YES_NO = r"(?P<value>Yes|No)\b"
_DATETIME = r"(?P<value>\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2})"

DEFAULT_FIELD_RULES: tuple[FieldRule, ...] = (
    rule("bid_number", "Bid number",
         rf"Bid{_}Number\s*:\s*(?P<value>GEM/\d{{4}}/[A-Z]/\d+)", n.text, critical=True),
    rule("bid_dated", "Bid dated", rf"Dated\s*:\s*(?P<value>\d{{2}}-\d{{2}}-\d{{4}})", n.ist_date),
    rule("bid_end", "Bid end date/time", rf"/Bid End Date/Time\s*\n{_DATETIME}", n.ist_datetime, critical=True),
    rule("bid_opening", "Bid opening date/time", rf"/Bid Opening{_}Date/Time\s*\n{_DATETIME}", n.ist_datetime),
    rule("offer_validity_days", "Bid offer validity (days)",
         rf"/Bid Offer{_}Validity \(From End Date\)\s*\n(?P<value>\d+)\s*\(Days\)", n.integer),
    rule("ministry", "Ministry / State", rf"/Ministry/State Name\s*\n{_LINE}", n.text),
    rule("department", "Department", rf"/Department Name\s*\n{_LINE}", n.text),
    rule("organisation", "Organisation", rf"/Organisation Name\s*\n{_LINE}", n.text),
    rule("total_quantity", "Total quantity", rf"/Total Quantity\s*\n(?P<value>[\d,]+)", n.integer, critical=True),
    rule("item_category", "Item category",
         rf"/Item Category\s*\n(?P<value>[^\n]+(?:\n(?!/|GeMARPTS)[^\n]+)*)", n.text, critical=True),
    rule("relevant_categories", "Categories selected for notification",
         rf"Categories selected for notification\s*\n{_LINE}", n.text),
    rule("mse_exemption", "MSE exemption (experience & turnover)",
         rf"/MSE Exemption for Years of{_}Experience and Turnover\s*\n{_YES_NO}", n.yes_no, critical=True),
    rule("startup_exemption", "Startup exemption (experience & turnover)",
         rf"/Startup Exemption for Years of{_}Experience and Turnover\s*\n{_YES_NO}", n.yes_no),
    rule("bid_to_ra", "Bid to RA enabled", rf"/Bid to RA enabled\s*\n{_YES_NO}", n.yes_no),
    rule("type_of_bid", "Type of bid", rf"/Type of Bid\s*\n{_LINE}", n.text),
    rule("evaluation_method", "Evaluation method", rf"/Evaluation Method\s*\n{_LINE}", n.text),
    rule("emd_required", "EMD required", rf"/EMD Detail\s*\n/Required\s*\n{_YES_NO}", n.yes_no, critical=True),
    rule("epbg_required", "ePBG required", rf"/ePBG Detail\s*\n/Required\s*\n{_YES_NO}", n.yes_no),
    rule("mii_preference", "MII purchase preference", rf"/\s*MII Purchase Preference\s*\n{_YES_NO}", n.yes_no),
    rule("mse_preference", "MSE purchase preference", rf"/\s*MSE Purchase Preference\s*\n{_YES_NO}", n.yes_no),
)
