"""Bid PDFs are kept, so a better parser can re-read bids already analysed.

Without this, every parser fix (GeM changes its form every few months) would leave the
seller's existing bids with their old, wrong analysis until each was downloaded again.
"""

from __future__ import annotations

import sqlite3

import pytest

from gem_assistant.domain.models import Corrigendum, CorrigendumStatus
from gem_assistant.infrastructure.database import Database
from gem_assistant.infrastructure.document_store import FileDocumentStore
from gem_assistant.infrastructure.sqlite_repositories import SqliteBidRepository
from gem_assistant.parsing.field_rules import PARSER_VERSION

from .conftest import SAMPLE_PDF


def _make_outdated(settings, bid_id: int) -> None:
    """What an upgrade leaves behind: an analysis by version 1 of the rules."""
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("UPDATE bid SET parser_version = 1 WHERE id = ?", (bid_id,))
        conn.execute("DELETE FROM bid_field WHERE bid_id = ? AND name = 'emd_required'", (bid_id,))


def _document(settings, bid) -> object:
    return settings.data_dir / "documents" / f"{bid.sha256}.pdf"


def test_an_imported_pdf_is_kept(container, settings):
    result = container.bid_import.import_pdf(SAMPLE_PDF.read_bytes(), source_name="bid.pdf")
    bid = container.bid_query.get(result.id).bid
    assert bid.parser_version == PARSER_VERSION
    assert _document(settings, bid).read_bytes() == SAMPLE_PDF.read_bytes()


def test_an_outdated_bid_is_re_read_in_place(container, settings):
    bid_id = container.bid_import.import_pdf(SAMPLE_PDF.read_bytes(), source_name="bid.pdf").id
    repo = SqliteBidRepository(Database(settings.db_path))
    checked = Corrigendum(status=CorrigendumStatus.NONE, checked_at=container.bid_query.get(bid_id).bid.imported_at)
    repo.set_corrigendum(bid_id, checked)
    _make_outdated(settings, bid_id)

    report = container.bid_import.reanalyse_outdated()

    assert (report.updated, report.without_pdf, report.failed) == (1, 0, 0)
    bid = repo.get(bid_id)  # same id: links and open pages keep working
    assert bid.parser_version == PARSER_VERSION
    assert bid.found_value("emd_required") == "No"
    assert bid.corrigendum.status is CorrigendumStatus.NONE  # GeM's answer is not thrown away
    assert container.bid_import.reanalyse_outdated().updated == 0  # nothing left to do


def test_a_bid_whose_pdf_was_not_kept_is_left_alone(container, settings):
    bid_id = container.bid_import.import_pdf(SAMPLE_PDF.read_bytes(), source_name="bid.pdf").id
    _document(settings, container.bid_query.get(bid_id).bid).unlink()
    _make_outdated(settings, bid_id)

    report = container.bid_import.reanalyse_outdated()

    assert (report.updated, report.without_pdf) == (0, 1)
    assert container.bid_query.get(bid_id).bid.parser_version == 1


def test_importing_again_brings_back_a_missing_pdf(container, settings):
    first = container.bid_import.import_pdf(SAMPLE_PDF.read_bytes(), source_name="bid.pdf")
    document = _document(settings, container.bid_query.get(first.id).bid)
    document.unlink()
    again = container.bid_import.import_pdf(SAMPLE_PDF.read_bytes(), source_name="bid.pdf")
    assert again.already_imported and document.exists()


@pytest.mark.parametrize("name", ["../escape", "a" * 63, "A" * 64, "a" * 64 + "/x"])
def test_the_store_only_accepts_content_hashes_as_names(tmp_path, name):
    with pytest.raises(ValueError):
        FileDocumentStore(tmp_path).save(name, b"%PDF-")


def test_the_reanalyse_command_reports_what_it_did(monkeypatch, tmp_path, capsys):
    from gem_assistant.cli import main

    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path))
    assert main(["import", str(SAMPLE_PDF)]) == 0
    with sqlite3.connect(tmp_path / "gem.sqlite3") as conn:
        conn.execute("UPDATE bid SET parser_version = 1")
    capsys.readouterr()
    assert main(["reanalyse"]) == 0
    assert "Re-read 1 stored bid" in capsys.readouterr().out
