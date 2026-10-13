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

# Bump whenever field rules, clause patterns or conflict checks change: stored bids analysed
# by an older version are re-read from their kept PDF on the next start.
PARSER_VERSION = 3  # 3: GeM's standard wording raises no flag; explaining EMD is no conflict

# Matches the whitespace/newlines pypdf puts between label words.
_ = r"\s+"


@dataclass(frozen=True)
class FieldRule:
    name: str
    label: str
    pattern: re.Pattern[str]  # must contain a named group 'value'
    normalize: Callable[[str], str]
    critical: bool = False
    # Other layouts of the same field, tried in order after `pattern` (GeM changes its form).
    also: tuple[tuple[re.Pattern[str], Callable[[str], str]], ...] = ()

    def layouts(self) -> tuple[tuple[re.Pattern[str], Callable[[str], str]], ...]:
        return ((self.pattern, self.normalize), *self.also)


def rule(name: str, label: str, regex: str, normalize: Callable[[str], str], critical: bool = False,
         also: tuple[tuple[str, Callable[[str], str]], ...] = ()) -> FieldRule:
    return FieldRule(name, label, re.compile(regex), normalize, critical,
                     tuple((re.compile(r), norm) for r, norm in also))


def _relaxation(who: str, what: str) -> str:
    """MSE/Startup relaxation of the experience or turnover criterion. One label may cover
    both ("...for Years of Experience and Turnover") or just one; seen in all three shapes in
    live bids, worded "Exemption" in 2025 and "Relaxation" in 2026."""
    experience = rf"Years{_}[Oo]f{_}Experience"
    criterion = rf"{experience}(?:{_}and{_}Turnover)?" if what == "experience" else rf"(?:{experience}{_}and{_})?Turnover"
    value = r"(?P<value>No\b|Yes(?:\s*\n\|\s*\n(?:Complete|Partial)\b)?)"  # "Yes | Partial" is seen live
    return rf"{who}{_}(?:Exemption|Relaxation){_}for{_}{criterion}\s*\n{value}"


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
    # Not critical: service bids (manpower, hiring, maintenance) have no quantity at all.
    rule("total_quantity", "Total quantity", rf"/Total Quantity\s*\n(?P<value>[\d,]+)", n.integer),
    rule("item_category", "Item category",
         rf"/Item Category\s*\n(?P<value>[^\n]+(?:\n(?!/|GeMARPTS)[^\n]+)*)", n.text, critical=True),
    rule("relevant_categories", "Categories selected for notification",
         rf"Categories selected for notification\s*\n{_LINE}", n.text),
    rule("min_avg_turnover", "Minimum average annual turnover, 3 years (₹)",
         rf"Minimum Average Annual Turnover of the{_}bidder \(For (?P<value>\d+ Years?\)\s*\n[^\n]+)",
         n.three_year_turnover_lakh),
    rule("min_experience_years", "Years of past experience required",
         rf"Years of Past Experience Required for{_}same/similar \w+\s*\n(?P<value>[^\n]+)", n.years),
    # Not critical: a bid with no turnover or experience criterion has no relaxation row. The
    # requirement rule asks about relaxation only when the seller would otherwise fall short.
    rule("mse_experience_relaxation", "MSE relaxation: experience", _relaxation("MSE", "experience"), n.relaxation),
    rule("mse_turnover_relaxation", "MSE relaxation: turnover", _relaxation("MSE", "turnover"), n.relaxation),
    rule("startup_experience_relaxation", "Startup relaxation: experience",
         _relaxation("Startup", "experience"), n.relaxation),
    rule("startup_turnover_relaxation", "Startup relaxation: turnover", _relaxation("Startup", "turnover"),
         n.relaxation),
    rule("bid_to_ra", "Bid to RA enabled", rf"/Bid to RA enabled\s*\n{_YES_NO}", n.yes_no),
    rule("type_of_bid", "Type of bid", rf"/Type of Bid\s*\n{_LINE}", n.text),
    rule("evaluation_method", "Evaluation method", rf"/Evaluation Method\s*\n{_LINE}", n.text),
    rule("emd_required", "EMD required", rf"/EMD Detail\s*\n/Required\s*\n{_YES_NO}", n.yes_no, critical=True,
         also=((rf"/EMD Detail\s*\n/Advisory Bank\s*\n[^\n]+\n(?:Schedule \d+\s*\n/\s*\n)?/?\s*"
                rf"EMD Amount(?: \(In INR\))?\s*\n(?P<value>[\d,]+)", n.shown_means_yes),)),
    # One amount only. Schedule-wise bids ("EMD Amount (In INR)" per schedule) are not reduced
    # to a single figure; they stay NOT_FOUND here and EMD shows as required.
    rule("emd_amount", "EMD amount (₹)", rf"/EMD Amount\s*\n(?P<value>[\d,]+)", n.rupees),
    rule("epbg_required", "ePBG required", rf"/ePBG Detail\s*\n/Required\s*\n{_YES_NO}", n.yes_no,
         also=((rf"/ePBG Detail\s*\n/Advisory Bank\s*\n[^\n]+\n\(%\)\s*\n/ePBG Percentage\(%\)\s*\n"
                rf"(?P<value>\d+(?:\.\d+)?)", n.shown_means_yes),)),
    rule("epbg_percent", "ePBG (% of order value)", rf"/ePBG Percentage\(%\)\s*\n(?P<value>\d+(?:\.\d+)?)", n.percent),
    rule("mii_preference", "MII purchase preference", rf"/\s*MII Purchase Preference\s*\n{_YES_NO}", n.yes_no),
    rule("mii_compliance", "MII compliance required", rf"/\s*MII Compliance\s*\n{_YES_NO}", n.yes_no),
    rule("mse_preference", "MSE purchase preference", rf"/\s*MSE Purchase Preference\s*\n{_YES_NO}", n.yes_no),
)
