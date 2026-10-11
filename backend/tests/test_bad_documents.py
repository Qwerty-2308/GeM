"""Documents that are broken, hostile, or simply not what they claim to be.

A bid document is a file fetched from a government portal and handed straight to a
parser. Anything unreadable must fail loudly and locally — never crash the run, never
silently become an empty analysis that looks like a clean bid.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfWriter

from gem_assistant.domain.models import FieldStatus
from gem_assistant.infrastructure.pypdf_extractor import InvalidPdf, PypdfTextExtractor

from .conftest import SAMPLE_PDF


def _pdf(pages: int = 1, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if password:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ---------- things that are not a usable PDF ----------

@pytest.mark.parametrize("name, data", [
    ("empty file", b""),
    ("html pretending to be a document", b"<html>not a pdf</html>"),
    ("header only", b"%PDF-"),
    ("header then rubbish", b"%PDF-1.7\n" + b"\x00\xff" * 500),
    ("truncated real bid document", SAMPLE_PDF.read_bytes()[:600]),
    ("a zip, not a pdf", b"PK\x03\x04" + b"\x00" * 100),
])
def test_unusable_files_are_refused(name, data):
    with pytest.raises(InvalidPdf):
        PypdfTextExtractor(max_pages=300).extract(data)


def test_encrypted_documents_are_refused_rather_than_guessed_at():
    with pytest.raises(InvalidPdf, match="Encrypted"):
        PypdfTextExtractor(max_pages=300).extract(_pdf(password="secret"))


def test_a_document_with_too_many_pages_is_refused():
    with pytest.raises(InvalidPdf, match="limit"):
        PypdfTextExtractor(max_pages=2).extract(_pdf(pages=3))


def test_the_page_limit_is_inclusive():
    assert len(PypdfTextExtractor(max_pages=3).extract(_pdf(pages=3))) == 3


# ---------- a readable PDF that says nothing ----------

def test_a_blank_document_reports_nothing_found_instead_of_looking_clean(container):
    """The dangerous outcome is a bid that extracted nothing being presented as a bid
    with no requirements."""
    result = container.bid_import.import_pdf(_pdf(pages=2), source_name="blank.pdf")
    bid = container.bid_query.get(result.id)

    assert bid.bid.bid_number is None
    assert {f.status for f in bid.bid.fields} == {FieldStatus.NOT_FOUND}
    assert bid.bid.fields, "fields should still be listed, each as NOT_FOUND"
    # every critical field missing must be put in front of the person
    assert any(r.rule == "critical_fields" for r in bid.assessment.results)
    assert bid.assessment.verdict.value in ("NEEDS_REVIEW", "BLOCKED")


def test_two_unreadable_documents_can_both_be_stored(container):
    """Both have no bid number; one must not overwrite the other."""
    first = container.bid_import.import_pdf(_pdf(pages=1), source_name="one.pdf")
    second = container.bid_import.import_pdf(_pdf(pages=2), source_name="two.pdf")
    assert first.id != second.id
    assert len(container.bid_query.list()) == 2


def test_the_same_document_twice_is_not_analysed_twice(container):
    data = _pdf(pages=1)
    first = container.bid_import.import_pdf(data, source_name="a.pdf")
    again = container.bid_import.import_pdf(data, source_name="renamed.pdf")
    assert again.already_imported and again.id == first.id


# ---------- size and names ----------

def test_a_document_above_the_size_cap_is_refused(container):
    cap = container.settings.safety.max_download_bytes
    with pytest.raises(ValueError, match="larger than"):
        container.bid_import.import_pdf(b"%PDF-" + b"0" * cap, source_name="huge.pdf")


@pytest.mark.parametrize("hostile_name", [
    "../../../../etc/passwd",
    "GEM/2026/B/../../.ssh/authorized_keys",
    "..\\..\\windows\\system32\\drivers\\etc\\hosts",
])
def test_a_hostile_file_name_never_becomes_a_path(container, tmp_path, hostile_name):
    """Bid numbers contain slashes by design, so a name from the portal must never be
    used to build a path. Nothing is written to disk except the database."""
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    result = container.bid_import.import_pdf(_pdf(), source_name=hostile_name)

    stored = container.bid_query.get(result.id).bid
    assert stored.source_name == hostile_name  # kept as data, used as data
    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert after - before <= {container.settings.db_path}
    assert not Path("/tmp/etc/passwd").exists()
