"""SQLite connection handling and schema migrations."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MIGRATIONS: list[str] = [
    # 1 — initial schema
    """
    CREATE TABLE seller_profile (
        id                     INTEGER PRIMARY KEY CHECK (id = 1),
        business_name          TEXT NOT NULL,
        is_mse                 INTEGER NOT NULL,
        is_startup             INTEGER NOT NULL,
        is_oem                 INTEGER NOT NULL,
        annual_turnover_rupees INTEGER,
        years_experience       INTEGER,
        categories_json        TEXT NOT NULL,
        certifications_json    TEXT NOT NULL,
        states_json            TEXT NOT NULL,
        updated_at             TEXT NOT NULL
    );

    CREATE TABLE bid (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        bid_number    TEXT UNIQUE,
        source_name   TEXT NOT NULL,
        sha256        TEXT NOT NULL UNIQUE,
        page_count    INTEGER NOT NULL,
        unread_lines  INTEGER NOT NULL,
        imported_at   TEXT NOT NULL
    );

    CREATE TABLE bid_field (
        bid_id    INTEGER NOT NULL REFERENCES bid(id) ON DELETE CASCADE,
        position  INTEGER NOT NULL,
        name      TEXT NOT NULL,
        label     TEXT NOT NULL,
        status    TEXT NOT NULL,
        value     TEXT,
        critical  INTEGER NOT NULL,
        note      TEXT,
        page      INTEGER,
        quote     TEXT,
        method    TEXT,
        PRIMARY KEY (bid_id, name)
    );

    CREATE TABLE bid_flag (
        bid_id    INTEGER NOT NULL REFERENCES bid(id) ON DELETE CASCADE,
        position  INTEGER NOT NULL,
        category  TEXT NOT NULL,
        label     TEXT NOT NULL,
        page      INTEGER NOT NULL,
        quote     TEXT NOT NULL,
        method    TEXT NOT NULL,
        PRIMARY KEY (bid_id, position)
    );

    CREATE TABLE request_log (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        at       TEXT NOT NULL,
        url      TEXT NOT NULL,
        status   INTEGER,
        outcome  TEXT NOT NULL
    );
    CREATE INDEX request_log_at ON request_log(at);

    CREATE TABLE safety_state (
        id               INTEGER PRIMARY KEY CHECK (id = 1),
        open             INTEGER NOT NULL,
        reason           TEXT,
        opened_at        TEXT,
        last_reset_at    TEXT,
        last_reset_note  TEXT
    );
    """,
    # 2 — discovery from the public listing
    """
    CREATE TABLE listing_bid (
        doc_id         TEXT PRIMARY KEY,
        bid_number     TEXT NOT NULL,
        kind           TEXT NOT NULL,
        doc_path       TEXT NOT NULL,
        category       TEXT,
        quantity       TEXT,
        ministry       TEXT,
        department     TEXT,
        starts_at      TEXT,
        ends_at        TEXT,
        keyword        TEXT NOT NULL,
        first_seen_at  TEXT NOT NULL,
        last_seen_at   TEXT NOT NULL,
        doc_status     TEXT NOT NULL,
        doc_note       TEXT,
        bid_id         INTEGER REFERENCES bid(id) ON DELETE SET NULL
    );
    ALTER TABLE bid ADD COLUMN doc_id TEXT;
    CREATE INDEX bid_doc_id ON bid(doc_id);
    CREATE INDEX listing_bid_status ON listing_bid(doc_status, ends_at);
    CREATE INDEX listing_bid_number ON listing_bid(bid_number);

    CREATE TABLE discovery_run (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger        TEXT NOT NULL,
        started_at     TEXT NOT NULL,
        finished_at    TEXT,
        outcome        TEXT,
        message        TEXT,
        keywords_json  TEXT NOT NULL,
        found          INTEGER NOT NULL DEFAULT 0,
        new            INTEGER NOT NULL DEFAULT 0,
        downloaded     INTEGER NOT NULL DEFAULT 0,
        requests       INTEGER NOT NULL DEFAULT 0
    );
    """,
    # 3 — corrigendum check. A bid reported without one checked is reported on
    # possibly stale facts, so existing rows default to NOT_CHECKED rather than NONE.
    """
    ALTER TABLE bid ADD COLUMN corrigendum_status TEXT NOT NULL DEFAULT 'NOT_CHECKED';
    ALTER TABLE bid ADD COLUMN corrigendum_checked_at TEXT;
    ALTER TABLE bid ADD COLUMN corrigendum_text TEXT;
    ALTER TABLE bid ADD COLUMN corrigendum_note TEXT;
    ALTER TABLE discovery_run ADD COLUMN corrigenda_checked INTEGER NOT NULL DEFAULT 0;
    """,
]


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        if not self.path.parent.exists():  # ours to create: seller data, owner-only
            self.path.parent.mkdir(mode=0o700, parents=True)
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
                conn.executescript(script)
                conn.execute(f"PRAGMA user_version = {index}")
        try:
            self.path.chmod(0o600)  # seller data: owner-only
        except OSError:
            pass
