"""Extraction against a real GeM bid document (GEM/2025/B/6819518)."""

from __future__ import annotations

import pytest

from gem_assistant.domain.models import (
    ClauseFlag, Evidence, ExtractedField, ExtractionMethod, FieldStatus, PageText,
)
from gem_assistant.infrastructure.pypdf_extractor import InvalidPdf, PypdfTextExtractor
from gem_assistant.parsing import normalizers as n
from gem_assistant.parsing.clause_scanner import PatternClauseScanner
from gem_assistant.parsing.evidence import quote_is_on_page
from gem_assistant.parsing.label_extractor import LabelFieldExtractor
from gem_assistant.parsing.reconciler import RuleBasedReconciler

from .conftest import SAMPLE_PDF


@pytest.fixture(scope="module")
def pages() -> list[PageText]:
    return PypdfTextExtractor(max_pages=300).extract(SAMPLE_PDF.read_bytes())


@pytest.fixture(scope="module")
def fields(pages) -> dict[str, ExtractedField]:
    return {f.name: f for f in LabelFieldExtractor().extract(pages)}


# Hand-checked against the PDF.
EXPECTED = {
    "bid_number": "GEM/2025/B/6819518",
    "bid_dated": "2025-10-25",
    "bid_end": "2025-11-14T12:00:00+05:30",
    "bid_opening": "2025-11-14T12:30:00+05:30",
    "offer_validity_days": "90",
    "ministry": "Ministry Of Defence",
    "department": "Department Of Defence Production",
    "organisation": "Armoured Vehicles Nigam Limited",
    "total_quantity": "6383",
    "item_category": "CLIP ASSY TO DRG NO. SB 3321-05 3 (AS PER THE DRG & SPECIFICATION)",
    "relevant_categories": "Diesel Engine as per IS 10001",
    "mse_exemption": "No",
    "startup_exemption": "No",
    "bid_to_ra": "No",
    "type_of_bid": "Two Packet Bid",
    "evaluation_method": "Total value wise evaluation",
    "emd_required": "No",
    "epbg_required": "No",
    "mii_preference": "Yes",
    "mse_preference": "Yes",
}


@pytest.mark.parametrize("name,expected", EXPECTED.items())
def test_field_values_match_the_pdf(fields, name, expected):
    assert fields[name].status is FieldStatus.FOUND
    assert fields[name].value == expected


def test_every_quote_is_really_on_its_page(pages, fields):
    for f in fields.values():
        assert quote_is_on_page(f.evidence, pages), f.name


def test_hindi_mojibake_is_dropped_not_parsed(pages):
    assert sum(p.unread_lines for p in pages) > 0
    assert not any("ऀ" <= ch <= "ॿ" for p in pages for ch in p.text)


def test_unknown_label_gives_not_found_never_a_guess():
    page = PageText(number=1, text="/Something Else\nYes")
    result = {f.name: f for f in LabelFieldExtractor().extract([page])}
    assert result["emd_required"].status is FieldStatus.NOT_FOUND
    assert result["emd_required"].value is None


def test_found_without_evidence_is_impossible():
    with pytest.raises(ValueError):
        ExtractedField(name="x", label="x", status=FieldStatus.FOUND, value="1")


def test_hidden_disqualifier_in_atc_is_flagged(pages):
    flags = PatternClauseScanner().scan(pages)
    restricted = [f for f in flags if f.category == "restricted_sources"]
    assert any("Established Vendors" in f.evidence.quote for f in restricted)
    assert any("Shall Not Be Considered" in f.evidence.quote for f in restricted)
    for f in flags:
        assert quote_is_on_page(f.evidence, pages)


def test_unread_attachment_is_flagged(pages):
    flags = PatternClauseScanner().scan(pages)
    assert any(f.category == "unread_attachment" for f in flags)


def test_scanner_ignores_text_outside_buyer_section():
    page = PageText(number=1, text="Bidder turnover must be 5 crore.\n/Disclaimer\nmore turnover text")
    assert PatternClauseScanner().scan([page]) == []


def test_structured_no_vs_clause_becomes_conflicting():
    ev = Evidence(page=1, quote="/EMD Detail /Required No", method=ExtractionMethod.LABEL_PARSE)
    emd = ExtractedField(name="emd_required", label="EMD", status=FieldStatus.FOUND, value="No", evidence=ev)
    flag = ClauseFlag(category="emd", label="EMD",
                      evidence=Evidence(page=4, quote="Submit EMD of Rs 1,00,000 by DD.",
                                        method=ExtractionMethod.PATTERN_SCAN))
    (result,) = RuleBasedReconciler().reconcile([emd], [flag])
    assert result.status is FieldStatus.CONFLICTING
    assert "page 4" in result.note


def test_non_pdf_is_rejected():
    with pytest.raises(InvalidPdf):
        PypdfTextExtractor(max_pages=300).extract(b"<html>not a pdf</html>")


@pytest.mark.parametrize("raw,expected", [
    ("Rs. 5,00,000/-", "500000"),
    ("₹ 50,000", "50000"),
    ("INR 1,25,00,000.00", "12500000"),
])
def test_indian_money_formats(raw, expected):
    assert n.rupees(raw) == expected


def test_dates_are_day_first_india_time():
    assert n.ist_datetime("04-11-2025 09:00:00") == "2025-11-04T09:00:00+05:30"
