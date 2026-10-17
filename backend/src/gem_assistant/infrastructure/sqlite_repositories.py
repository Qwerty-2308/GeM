"""SQLite implementations of the persistence ports. One class per aggregate."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Sequence

from ..domain.models import (
    BidAnalysis,
    BidKind,
    Corrigendum,
    DiscoveryRun,
    DocStatus,
    ListingBid,
    ListingEntry,
    BreakerState,
    ClauseFlag,
    Evidence,
    ExtractedField,
    RequestRecord,
    SellerProfile,
)
from .database import Database


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SqliteProfileRepository:
    def __init__(self, db: Database):
        self._db = db

    def get(self) -> SellerProfile | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM seller_profile WHERE id = 1").fetchone()
        if row is None:
            return None
        return SellerProfile(
            business_name=row["business_name"],
            is_mse=bool(row["is_mse"]),
            is_startup=bool(row["is_startup"]),
            is_oem=bool(row["is_oem"]),
            annual_turnover_rupees=row["annual_turnover_rupees"],
            years_experience=row["years_experience"],
            categories=json.loads(row["categories_json"]),
            certifications=json.loads(row["certifications_json"]),
            states=json.loads(row["states_json"]),
            ignore_words=json.loads(row["ignore_json"]),
            bid_order=row["bid_order"],
            updated_at=_dt(row["updated_at"]),
        )

    def save(self, profile: SellerProfile) -> SellerProfile:
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO seller_profile (id, business_name, is_mse, is_startup, is_oem,
                    annual_turnover_rupees, years_experience, categories_json,
                    certifications_json, states_json, ignore_json, bid_order, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    business_name = excluded.business_name,
                    is_mse = excluded.is_mse,
                    is_startup = excluded.is_startup,
                    is_oem = excluded.is_oem,
                    annual_turnover_rupees = excluded.annual_turnover_rupees,
                    years_experience = excluded.years_experience,
                    categories_json = excluded.categories_json,
                    certifications_json = excluded.certifications_json,
                    states_json = excluded.states_json,
                    ignore_json = excluded.ignore_json,
                    bid_order = excluded.bid_order,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.business_name,
                    int(profile.is_mse),
                    int(profile.is_startup),
                    int(profile.is_oem),
                    profile.annual_turnover_rupees,
                    profile.years_experience,
                    json.dumps(profile.categories),
                    json.dumps(profile.certifications),
                    json.dumps(profile.states),
                    json.dumps(profile.ignore_words),
                    profile.bid_order,
                    _iso(profile.updated_at),
                ),
            )
        return profile


class SqliteBidRepository:
    def __init__(self, db: Database):
        self._db = db

    def find_id_by_sha256(self, sha256: str) -> int | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT id FROM bid WHERE sha256 = ?", (sha256,)).fetchone()
        return row["id"] if row else None

    def find_id_by_doc_id(self, doc_id: str) -> int | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT id FROM bid WHERE doc_id = ?", (doc_id,)).fetchone()
        return row["id"] if row else None

    def save(self, analysis: BidAnalysis) -> int:
        with self._db.connect() as conn:
            if analysis.id is not None:  # re-analysis: replace in place, keeping the id
                conn.execute("DELETE FROM bid WHERE id = ?", (analysis.id,))
            if analysis.bid_number:
                # A newer document for the same bid (e.g. after a corrigendum) replaces the old one.
                conn.execute("DELETE FROM bid WHERE bid_number = ?", (analysis.bid_number,))
            cursor = conn.execute(
                """INSERT INTO bid (id, bid_number, doc_id, source_name, sha256, page_count, unread_lines,
                                    imported_at, corrigendum_status, corrigendum_checked_at,
                                    corrigendum_text, corrigendum_note, parser_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    analysis.id,
                    analysis.bid_number,
                    analysis.doc_id,
                    analysis.source_name,
                    analysis.sha256,
                    analysis.page_count,
                    analysis.unread_lines,
                    _iso(analysis.imported_at),
                    analysis.corrigendum.status.value,
                    _iso(analysis.corrigendum.checked_at),
                    analysis.corrigendum.text,
                    analysis.corrigendum.note,
                    analysis.parser_version,
                ),
            )
            bid_id = cursor.lastrowid
            if analysis.bid_number or analysis.doc_id:
                conn.execute(
                    "UPDATE listing_bid SET doc_status = 'DOWNLOADED', doc_note = NULL, bid_id = ? "
                    "WHERE bid_number = ? OR doc_id = ?",
                    (bid_id, analysis.bid_number, analysis.doc_id),
                )
            conn.executemany(
                """INSERT INTO bid_field (bid_id, position, name, label, status, value, critical,
                                          note, page, quote, method)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        bid_id, i, f.name, f.label, f.status.value, f.value, int(f.critical), f.note,
                        f.evidence.page if f.evidence else None,
                        f.evidence.quote if f.evidence else None,
                        f.evidence.method.value if f.evidence else None,
                    )
                    for i, f in enumerate(analysis.fields)
                ],
            )
            conn.executemany(
                """INSERT INTO bid_flag (bid_id, position, category, label, page, quote, method)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (bid_id, i, flag.category, flag.label, flag.evidence.page,
                     flag.evidence.quote, flag.evidence.method.value)
                    for i, flag in enumerate(analysis.flags)
                ],
            )
        return int(bid_id)

    def list(self) -> list[BidAnalysis]:
        with self._db.connect() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM bid ORDER BY imported_at DESC")]
        return [bid for bid_id in ids if (bid := self.get(bid_id)) is not None]

    def get(self, bid_id: int) -> BidAnalysis | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM bid WHERE id = ?", (bid_id,)).fetchone()
            if row is None:
                return None
            field_rows = conn.execute(
                "SELECT * FROM bid_field WHERE bid_id = ? ORDER BY position", (bid_id,)
            ).fetchall()
            flag_rows = conn.execute(
                "SELECT * FROM bid_flag WHERE bid_id = ? ORDER BY position", (bid_id,)
            ).fetchall()
            listed = conn.execute(
                """SELECT ends_at FROM listing_bid WHERE bid_id = ? OR bid_number = ?
                   ORDER BY last_seen_at DESC LIMIT 1""", (bid_id, row["bid_number"]),
            ).fetchone()

        return BidAnalysis(
            id=row["id"],
            bid_number=row["bid_number"],
            doc_id=row["doc_id"],
            source_name=row["source_name"],
            sha256=row["sha256"],
            page_count=row["page_count"],
            unread_lines=row["unread_lines"],
            imported_at=_dt(row["imported_at"]),
            fields=[
                ExtractedField(
                    name=r["name"], label=r["label"], status=r["status"], value=r["value"],
                    critical=bool(r["critical"]), note=r["note"],
                    evidence=Evidence(page=r["page"], quote=r["quote"], method=r["method"])
                    if r["quote"] else None,
                )
                for r in field_rows
            ],
            flags=[
                ClauseFlag(
                    category=r["category"], label=r["label"],
                    evidence=Evidence(page=r["page"], quote=r["quote"], method=r["method"]),
                )
                for r in flag_rows
            ],
            corrigendum=Corrigendum(
                status=row["corrigendum_status"],
                checked_at=_dt(row["corrigendum_checked_at"]),
                text=row["corrigendum_text"],
                note=row["corrigendum_note"],
            ),
            parser_version=row["parser_version"],
            listed_end=_dt(listed["ends_at"]) if listed else None,
        )

    def set_corrigendum(self, bid_id: int, corrigendum: Corrigendum) -> None:
        with self._db.connect() as conn:
            conn.execute(
                """UPDATE bid SET corrigendum_status = ?, corrigendum_checked_at = ?,
                       corrigendum_text = ?, corrigendum_note = ? WHERE id = ?""",
                (corrigendum.status.value, _iso(corrigendum.checked_at), corrigendum.text,
                 corrigendum.note, bid_id),
            )


class SqliteRequestLog:
    def __init__(self, db: Database):
        self._db = db

    def record(self, record: RequestRecord) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO request_log (at, url, status, outcome) VALUES (?, ?, ?, ?)",
                (_iso(record.at), record.url, record.status, record.outcome),
            )

    def count_since(self, since: datetime) -> int:
        with self._db.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM request_log WHERE at >= ?", (_iso(since),)
            ).fetchone()[0]

    def recent(self, limit: int) -> list[RequestRecord]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM request_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            RequestRecord(at=_dt(r["at"]), url=r["url"], status=r["status"], outcome=r["outcome"])
            for r in rows
        ]


class SqliteBreakerStateStore:
    def __init__(self, db: Database):
        self._db = db

    def load(self) -> BreakerState:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM safety_state WHERE id = 1").fetchone()
        if row is None:
            return BreakerState()
        return BreakerState(
            open=bool(row["open"]),
            reason=row["reason"],
            opened_at=_dt(row["opened_at"]),
            last_reset_at=_dt(row["last_reset_at"]),
            last_reset_note=row["last_reset_note"],
        )

    def save(self, state: BreakerState) -> None:
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO safety_state (id, open, reason, opened_at, last_reset_at, last_reset_note)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    open = excluded.open, reason = excluded.reason, opened_at = excluded.opened_at,
                    last_reset_at = excluded.last_reset_at, last_reset_note = excluded.last_reset_note
                """,
                (int(state.open), state.reason, _iso(state.opened_at),
                 _iso(state.last_reset_at), state.last_reset_note),
            )


class SqliteListingRepository:
    def __init__(self, db: Database):
        self._db = db

    def upsert(self, bids: Sequence[ListingBid], keyword: str, now: datetime) -> list[str]:
        """Insert new cards, refresh known ones. Returns doc_ids seen for the first time."""
        new: list[str] = []
        with self._db.connect() as conn:
            for b in bids:
                exists = conn.execute("SELECT 1 FROM listing_bid WHERE doc_id = ?", (b.doc_id,)).fetchone()
                if exists:
                    conn.execute(
                        """UPDATE listing_bid SET bid_number=?, kind=?, doc_path=?, category=?, quantity=?,
                               ministry=?, department=?, starts_at=?, ends_at=?, last_seen_at=?
                           WHERE doc_id=?""",
                        (b.bid_number, b.kind.value, b.doc_path, b.category, b.quantity, b.ministry,
                         b.department, _iso(b.starts_at), _iso(b.ends_at), _iso(now), b.doc_id),
                    )
                    continue
                # Already analysed from a manual upload? Link it instead of downloading again.
                known = conn.execute("SELECT id FROM bid WHERE bid_number = ?", (b.bid_number,)).fetchone()
                if known:
                    status, bid_id = DocStatus.DOWNLOADED, known["id"]
                elif b.kind is BidKind.RA:
                    status, bid_id = DocStatus.SKIPPED, None
                else:
                    status, bid_id = DocStatus.PENDING, None
                conn.execute(
                    """INSERT INTO listing_bid (doc_id, bid_number, kind, doc_path, category, quantity, ministry,
                           department, starts_at, ends_at, keyword, first_seen_at, last_seen_at, doc_status,
                           doc_note, bid_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (b.doc_id, b.bid_number, b.kind.value, b.doc_path, b.category, b.quantity, b.ministry,
                     b.department, _iso(b.starts_at), _iso(b.ends_at), keyword, _iso(now), _iso(now),
                     status.value, "Reverse auction: not analysed" if b.kind is BidKind.RA else None, bid_id),
                )
                new.append(b.doc_id)
        return new

    def pending_downloads(self, limit: int, now: datetime) -> list[ListingEntry]:
        with self._db.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM listing_bid
                   WHERE doc_status = ? AND kind = ? AND (ends_at IS NULL OR ends_at > ?)
                   ORDER BY ends_at IS NULL, ends_at LIMIT ?""",
                (DocStatus.PENDING.value, BidKind.BID.value, _iso(now), limit),
            ).fetchall()
        return [self._entry(r) for r in rows]

    def mark(self, doc_id: str, status: DocStatus, note: str | None = None, bid_id: int | None = None) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE listing_bid SET doc_status = ?, doc_note = ?, bid_id = COALESCE(?, bid_id) WHERE doc_id = ?",
                (status.value, note, bid_id, doc_id),
            )

    def recent(self, limit: int) -> list[ListingEntry]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM listing_bid ORDER BY last_seen_at DESC, ends_at LIMIT ?", (limit,)
            ).fetchall()
        return [self._entry(r) for r in rows]

    @staticmethod
    def _entry(r) -> ListingEntry:
        return ListingEntry(
            doc_id=r["doc_id"], bid_number=r["bid_number"], kind=r["kind"], doc_path=r["doc_path"],
            category=r["category"], quantity=r["quantity"], ministry=r["ministry"],
            department=r["department"], starts_at=_dt(r["starts_at"]), ends_at=_dt(r["ends_at"]),
            keyword=r["keyword"], first_seen_at=_dt(r["first_seen_at"]), last_seen_at=_dt(r["last_seen_at"]),
            doc_status=r["doc_status"], doc_note=r["doc_note"], bid_id=r["bid_id"],
        )


class SqliteDiscoveryRunStore:
    def __init__(self, db: Database):
        self._db = db

    def start(self, run: DiscoveryRun) -> int:
        with self._db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO discovery_run (trigger, started_at, keywords_json) VALUES (?, ?, ?)",
                (run.trigger, _iso(run.started_at), json.dumps(run.keywords)),
            )
            return int(cursor.lastrowid)

    def finish(self, run: DiscoveryRun) -> None:
        with self._db.connect() as conn:
            conn.execute(
                """UPDATE discovery_run SET finished_at=?, outcome=?, message=?, keywords_json=?,
                       found=?, new=?, downloaded=?, requests=?, corrigenda_checked=? WHERE id=?""",
                (_iso(run.finished_at), run.outcome, run.message, json.dumps(run.keywords),
                 run.found, run.new, run.downloaded, run.requests, run.corrigenda_checked, run.id),
            )

    def close_interrupted(self, started_before: datetime, now: datetime) -> int:
        with self._db.connect() as conn:
            return conn.execute(
                """UPDATE discovery_run SET finished_at = ?, outcome = 'failed',
                       message = 'Interrupted: the app stopped during this search.'
                   WHERE finished_at IS NULL AND started_at < ?""",
                (_iso(now), _iso(started_before)),
            ).rowcount

    def keywords_since(self, since: datetime) -> list[str]:
        """Every keyword GeM was asked about by runs started at or after `since`."""
        with self._db.connect() as conn:
            rows = conn.execute("""SELECT keywords_json FROM discovery_run
                                   WHERE (requests > 0 OR finished_at IS NULL) AND started_at >= ?""",
                                (_iso(since),)).fetchall()
        return [k for r in rows for k in json.loads(r["keywords_json"])]

    def last(self, contacted_gem: bool = False, trigger: str | None = None) -> DiscoveryRun | None:
        clauses = ["(requests > 0 OR finished_at IS NULL)"] if contacted_gem else []
        if trigger is not None:
            clauses.append("trigger = ?")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._db.connect() as conn:
            r = conn.execute(f"SELECT * FROM discovery_run {where} ORDER BY id DESC LIMIT 1",
                             () if trigger is None else (trigger,)).fetchone()
        if r is None:
            return None
        return DiscoveryRun(
            id=r["id"], trigger=r["trigger"], started_at=_dt(r["started_at"]),
            finished_at=_dt(r["finished_at"]), outcome=r["outcome"], message=r["message"],
            keywords=json.loads(r["keywords_json"]), found=r["found"], new=r["new"],
            downloaded=r["downloaded"], requests=r["requests"],
            corrigenda_checked=r["corrigenda_checked"],
        )
