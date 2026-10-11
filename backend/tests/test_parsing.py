"""Extraction against a real GeM bid document (GEM/2025/B/6819518)."""

from __future__ import annotations

import functools

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

from .conftest import FIXTURES, SAMPLE_PDF


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
    "mse_experience_relaxation": "No",
    "mse_turnover_relaxation": "No",
    "startup_experience_relaxation": "No",
    "startup_turnover_relaxation": "No",
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


# Bid documents captured live on 11-Oct-2026, hand-checked against their text. GeM's form
# changed since the 2025 sample: "Exemption" became "Relaxation" (in three label shapes),
# EMD/ePBG show the bank and amount instead of "Required: Yes", MII Compliance is new, and
# the turnover/experience requirements are now labelled fields. None means NOT_FOUND.
CURRENT = {
    "GEM-2026-B-8061591_doc9921669.pdf": {  # service (laptop AMC): no quantity
        "bid_number": "GEM/2026/B/8061591",
        "mse_experience_relaxation": "Yes", "mse_turnover_relaxation": "Yes",
        "startup_experience_relaxation": "Yes", "startup_turnover_relaxation": "Yes",
        "emd_required": "Yes", "emd_amount": "425000",
        "epbg_required": "Yes", "epbg_percent": "5",
        "mii_compliance": "Yes",
        "min_avg_turnover": "25500000", "min_experience_years": "5",
        "total_quantity": None,
    },
    "GEM-2026-B-8100114_doc9965652.pdf": {  # product, nothing required
        "bid_number": "GEM/2026/B/8100114",
        "mse_experience_relaxation": "No", "mse_turnover_relaxation": "No",
        "startup_experience_relaxation": "No", "startup_turnover_relaxation": "No",
        "emd_required": "No", "emd_amount": None,
        "epbg_required": "No", "epbg_percent": None,
        "mii_preference": "No", "mii_compliance": None,
        "min_avg_turnover": None, "min_experience_years": "3",
        "total_quantity": "98",
    },
    "GEM-2026-B-7922876_doc9762022.pdf": {  # EMD per schedule: required, but no single amount
        "bid_number": "GEM/2026/B/7922876",
        "mse_experience_relaxation": "Yes", "mse_turnover_relaxation": None,
        "startup_experience_relaxation": "Yes", "startup_turnover_relaxation": None,
        "emd_required": "Yes", "emd_amount": None,
        "epbg_required": None,  # its label ends page 2 and its value starts page 3
        "mii_preference": "Yes",
        "min_avg_turnover": None, "min_experience_years": None,
        "total_quantity": "2",
    },
}


@functools.lru_cache(maxsize=None)
def _extract(pdf: str) -> tuple[list[PageText], dict[str, ExtractedField]]:
    pages = PypdfTextExtractor(max_pages=300).extract((FIXTURES / pdf).read_bytes())
    return pages, {f.name: f for f in LabelFieldExtractor().extract(pages)}


@pytest.mark.parametrize("pdf,name,expected",
                         [(pdf, name, value) for pdf, values in CURRENT.items() for name, value in values.items()])
def test_current_bid_documents(pdf, name, expected):
    pages, fields = _extract(pdf)
    field = fields[name]
    if expected is None:
        assert field.status is FieldStatus.NOT_FOUND, field
    else:
        assert (field.status, field.value) == (FieldStatus.FOUND, expected)
        assert quote_is_on_page(field.evidence, pages)


def test_every_quote_is_really_on_its_page(pages, fields):
    for f in fields.values():
        assert f.evidence is None or quote_is_on_page(f.evidence, pages), f.name


def test_the_2025_document_has_none_of_the_2026_fields(fields):
    for name in ("emd_amount", "epbg_percent", "mii_compliance", "min_avg_turnover", "min_experience_years"):
        assert fields[name].status is FieldStatus.NOT_FOUND, name


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


@pytest.mark.parametrize("text,expected", [
    ("/ MSE\nRelaxation for\nTurnover\nYes\n|\nComplete\n/ Startup", "Yes"),
    # Seen live (GEM/2026/B/8075770): a reduced threshold, not an exemption.
    ("/ MSE\nRelaxation for\nTurnover\nYes\n|\nPartial\n|\nTurn over value - 5 (in lakhs)\n", "Partial"),
    ("/MSE Relaxation for Years of\nExperience and Turnover\nNo\n/Startup", "No"),
    ("/MSE Exemption for Years of\nExperience and Turnover\nYes\n", "Yes"),  # the 2025 form
])
def test_relaxation_reads_complete_partial_or_none(text, expected):
    fields = {f.name: f for f in LabelFieldExtractor().extract([PageText(number=1, text=text)])}
    assert fields["mse_turnover_relaxation"].value == expected
