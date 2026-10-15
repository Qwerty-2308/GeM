"""Upgrading a database that already holds a person's bids.

Every other test starts from an empty database, so this is the only place the path an
existing user actually takes is exercised. Losing or corrupting their stored bids on
upgrade would be worse than any extraction bug.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from gem_assistant.domain.models import Corrigendum, CorrigendumStatus
from gem_assistant.infrastructure import database as database_module
from gem_assistant.infrastructure.database import Database
from gem_assistant.infrastructure.sqlite_repositories import SqliteBidRepository, SqliteDiscoveryRunStore

AT = datetime(2026, 10, 5, 6, 30, tzinfo=timezone.utc)


def _version(path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


@pytest.fixture
def old_database(tmp_path, monkeypatch):
    """A database as it stood before the corrigendum check existed, holding one analysed
    bid and one finished search."""
    path = tmp_path / "existing.sqlite3"
    every = database_module.MIGRATIONS
    monkeypatch.setattr(database_module, "MIGRATIONS", every[:2])
    Database(path).migrate()
    assert _version(path) == 2

    with sqlite3.connect(path) as conn:
        conn.execute(
            """INSERT INTO bid (id, bid_number, doc_id, source_name, sha256, page_count,
                                unread_lines, imported_at)
               VALUES (1, 'GEM/2025/B/6819518', '8506455', 'old.pdf', ?, 10, 684, ?)""",
            ("a" * 64, AT.isoformat()),
        )
        conn.execute(
            """INSERT INTO bid_field (bid_id, position, name, label, status, value, critical,
                                      note, page, quote, method)
               VALUES (1, 0, 'bid_end', 'Bid end date/time', 'FOUND', '2026-12-01T12:00:00+05:30',
                       1, NULL, 1, 'Bid End Date/Time 01-12-2026 12:00:00', 'LABEL_PARSE')""",
        )
        conn.execute(
            """INSERT INTO bid_flag (bid_id, position, category, label, page, quote, method)
               VALUES (1, 0, 'turnover', 'Turnover requirement', 6, 'Annual turnover of Rs 50 lakh',
                       'PATTERN_SCAN')""",
        )
        conn.execute(
            """INSERT INTO discovery_run (id, trigger, started_at, finished_at, outcome, message,
                                          keywords_json, found, new, downloaded, requests)
               VALUES (1, 'manual', ?, ?, 'ok', 'Found 2 bids', '["pump"]', 2, 2, 1, 3)""",
            (AT.isoformat(), AT.isoformat()),
        )

    monkeypatch.setattr(database_module, "MIGRATIONS", every)
    return path


def test_upgrade_keeps_the_bids_already_stored(old_database):
    Database(old_database).migrate()
    assert _version(old_database) == len(database_module.MIGRATIONS)

    bid = SqliteBidRepository(Database(old_database)).get(1)
    assert bid is not None, "the upgrade lost a stored bid"
    assert bid.bid_number == "GEM/2025/B/6819518"
    assert bid.doc_id == "8506455" and bid.unread_lines == 684
    assert bid.found_value("bid_end") == "2026-12-01T12:00:00+05:30"
    assert bid.field("bid_end").evidence.page == 1
    assert [f.category for f in bid.flags] == ["turnover"]


def test_bids_from_before_the_check_are_not_claimed_to_be_clear(old_database):
    """An upgraded bid was never checked for a corrigendum, and must say so rather than
    inherit a reassuring default."""
    Database(old_database).migrate()
    bid = SqliteBidRepository(Database(old_database)).get(1)
    assert bid.corrigendum.status is CorrigendumStatus.NOT_CHECKED
    assert bid.corrigendum.checked_at is None and bid.corrigendum.text is None


def test_an_upgraded_bid_can_then_be_checked(old_database):
    Database(old_database).migrate()
    repo = SqliteBidRepository(Database(old_database))
    repo.set_corrigendum(1, Corrigendum(status=CorrigendumStatus.PRESENT, checked_at=AT,
                                        text="Deadline extended to 10-12-2026"))
    assert repo.get(1).corrigendum.text == "Deadline extended to 10-12-2026"


def test_earlier_searches_survive_the_upgrade(old_database):
    Database(old_database).migrate()
    run = SqliteDiscoveryRunStore(Database(old_database)).last()
    assert run is not None and run.found == 2 and run.requests == 3
    assert run.corrigenda_checked == 0


def test_upgrading_twice_is_harmless(old_database):
    Database(old_database).migrate()
    Database(old_database).migrate()  # e.g. the app restarting
    assert _version(old_database) == len(database_module.MIGRATIONS)
    assert SqliteBidRepository(Database(old_database)).get(1) is not None


def test_a_migration_that_fails_halfway_changes_nothing(tmp_path, monkeypatch):
    """An upgrade interrupted by an error must leave the database exactly as it was, so the
    next start can run it again instead of tripping over a half-applied change."""
    path = tmp_path / "db.sqlite3"
    Database(path).migrate()
    before = _version(path)
    broken = "CREATE TABLE half_done (a INTEGER); CREATE TABLE broken (;"
    monkeypatch.setattr(database_module, "MIGRATIONS", [*database_module.MIGRATIONS, broken])
    with pytest.raises(sqlite3.Error):
        Database(path).migrate()
    assert _version(path) == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'half_done'").fetchone() is None


def test_the_database_uses_write_ahead_logging(tmp_path):
    """The web server's threads and a running search write at the same time."""
    path = tmp_path / "db.sqlite3"
    Database(path).migrate()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_bids_from_before_this_version_are_marked_for_re_reading(old_database):
    Database(old_database).migrate()
    assert SqliteBidRepository(Database(old_database)).get(1).parser_version == 1
