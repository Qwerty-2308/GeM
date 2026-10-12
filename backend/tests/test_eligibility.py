"""Eligibility rules that compare what a bid requires with the seller profile."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gem_assistant.domain.models import (
    BidAnalysis, Evidence, ExtractedField, ExtractionMethod, FieldStatus, SellerProfile,
)
from gem_assistant.eligibility.rules import CategoryMatchRule, EmdRule, TurnoverExperienceRule, inr

AT = datetime(2026, 10, 11, tzinfo=timezone.utc)


def _field(name: str, value: str | None, status: FieldStatus = FieldStatus.FOUND) -> ExtractedField:
    if value is None:
        return ExtractedField(name=name, label=name, status=FieldStatus.NOT_FOUND)
    return ExtractedField(name=name, label=name, status=status, value=value,
                          evidence=Evidence(page=1, quote=f"{name} {value}", method=ExtractionMethod.LABEL_PARSE))


def _bid(*fields: ExtractedField, **values: str | None) -> BidAnalysis:
    return BidAnalysis(bid_number="GEM/2026/B/1", source_name="x.pdf", sha256="a" * 64, page_count=1,
                       unread_lines=0, imported_at=AT, flags=[],
                       fields=[*fields, *(_field(name, value) for name, value in values.items())])


def _outcomes(bid: BidAnalysis, profile: SellerProfile | None) -> list[str]:
    return [r.outcome.value for r in TurnoverExperienceRule().evaluate(bid, profile, AT)]


@pytest.mark.parametrize("profile, relaxation, expected", [
    (SellerProfile(annual_turnover_rupees=3_000_000), None, []),
    (SellerProfile(annual_turnover_rupees=2_500_000), None, []),  # exactly the minimum
    (SellerProfile(annual_turnover_rupees=1_000_000), None, ["BLOCKED"]),
    (SellerProfile(annual_turnover_rupees=1_000_000, is_mse=True), "Yes", []),
    (SellerProfile(annual_turnover_rupees=1_000_000, is_mse=True), "No", ["BLOCKED"]),
    # A reduced threshold or a relaxation we could not read is a look, never a block.
    (SellerProfile(annual_turnover_rupees=1_000_000, is_mse=True), "Partial", ["NEEDS_REVIEW"]),
    (SellerProfile(annual_turnover_rupees=1_000_000, is_mse=True), None, ["NEEDS_REVIEW"]),
    (SellerProfile(), None, ["NEEDS_REVIEW"]),  # profile has no turnover yet
    (SellerProfile(annual_turnover_rupees=1_000_000), "Yes", ["BLOCKED"]),  # MSE relaxation, seller not an MSE
])
def test_turnover_requirement(profile, relaxation, expected):
    bid = _bid(min_avg_turnover="2500000", mse_turnover_relaxation=relaxation)
    assert _outcomes(bid, profile) == expected


def test_a_startup_relaxation_covers_a_startup():
    bid = _bid(min_avg_turnover="2500000", mse_turnover_relaxation="No", startup_turnover_relaxation="Yes")
    assert _outcomes(bid, SellerProfile(annual_turnover_rupees=1, is_mse=True, is_startup=True)) == []


def test_a_block_names_both_figures_and_points_at_the_clause():
    (result,) = TurnoverExperienceRule().evaluate(
        _bid(min_avg_turnover="25500000"), SellerProfile(annual_turnover_rupees=1_000_000), AT)
    assert "₹2,55,00,000" in result.reason and "₹10,00,000" in result.reason
    assert result.evidence.quote == "min_avg_turnover 25500000"


def test_experience_shortfall_blocks():
    (result,) = TurnoverExperienceRule().evaluate(
        _bid(min_experience_years="5"), SellerProfile(years_experience=3), AT)
    assert result.outcome.value == "BLOCKED" and "5 years" in result.reason and "3 years" in result.reason


def test_an_unreadable_requirement_needs_review():
    unclear = _field("min_avg_turnover", "x", FieldStatus.UNCLEAR)
    assert _outcomes(_bid(unclear), SellerProfile(annual_turnover_rupees=10**9)) == ["NEEDS_REVIEW"]


def test_no_requirement_or_no_profile_says_nothing():
    assert _outcomes(_bid(min_avg_turnover=None, min_experience_years=None), SellerProfile()) == []
    assert _outcomes(_bid(min_avg_turnover="1"), None) == []  # ProfileRequiredRule covers this


def test_emd_reason_shows_the_amount():
    (result,) = EmdRule().evaluate(_bid(emd_required="Yes", emd_amount="425000"), SellerProfile(), AT)
    assert "₹4,25,000" in result.reason


@pytest.mark.parametrize("rupees, text", [
    (0, "₹0"), (999, "₹999"), (1000, "₹1,000"), (425000, "₹4,25,000"), (25500000, "₹2,55,00,000"),
])
def test_inr_uses_indian_digit_grouping(rupees, text):
    assert inr(rupees) == text


@pytest.mark.parametrize("category, item, matches", [
    ("Chairs", "Office Chair", True),                       # plural in the profile, singular in the bid
    ("fire extinguisher", "Portable Fire Extinguishers (V3)", True),
    ("Pen", "Open Racks For Office", False),                # not a word in the bid, only letters inside one
    ("office chair", "Office Table,Visitor Chair", True),   # both words, not side by side
    ("fire extinguisher", "Fire Retardant Tarpaulin", False),
])
def test_category_match_compares_whole_words(category, item, matches):
    results = CategoryMatchRule().evaluate(_bid(item_category=item), SellerProfile(categories=[category]), AT)
    assert (results == []) is matches
