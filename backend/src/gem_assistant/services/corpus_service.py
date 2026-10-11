"""Collects real bid documents for parser work, and checks once whether the buyer
attachments they link to open without login.

Why it exists: the parser was written against a single bid PDF, and new labels may only
come from real documents (docs/02-RECON-FINDINGS.md). A corpus run is a discovery run
(trigger "corpus") that downloads more documents than usual, chosen for variety, and
keeps them on disk with a manifest. Every request still goes through SafeFetcher.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from ..domain.models import DiscoveryRun, FieldStatus, ListingEntry
from ..domain.ports import BidReader, Clock, ListingRepository, ListingSource
from ..infrastructure.pypdf_extractor import PdfLink
from ..parsing.normalizers import IST
from ..safety.daily_budget import DailyBudget
from ..safety.errors import SafetyError, UnexpectedResponse
from ..safety.response_classifier import Expect
from ..safety.safe_fetcher import SafeFetcher
from .bid_fetch_service import BidFetchService
from .discovery_service import DiscoveryNotAllowed, DiscoveryService

# Hard ceilings for this one command. Constants, not settings, so nothing can raise them.
CORPUS_MAX_DOWNLOADS = 30
CORPUS_MAX_CORRIGENDA = 10

# Link shapes seen in a real bid PDF (recon/fixtures, docs/02-RECON-FINDINGS.md §7).
ATTACHMENT_LINKS = (
    ("spec", re.compile(r"^https://mkp\.gem\.gov\.in/catalog_data/.*SpecificationDocument/.*\.pdf$")),
    ("atc", re.compile(r"^https://fulfilment\.gem\.gov\.in/contract/slafds\?")),
)


def pick_varied(entries: Sequence[ListingEntry], limit: int) -> list[ListingEntry]:
    """Up to `limit` entries: round-robin across keywords and, within a keyword, the
    entry whose ministry has been picked least so far. Ties keep the input order
    (soonest-closing first)."""
    pools: dict[str, list[ListingEntry]] = {}
    for entry in entries:
        pools.setdefault(entry.keyword, []).append(entry)
    per_ministry: Counter[str | None] = Counter()
    picked: list[ListingEntry] = []
    while len(picked) < limit and any(pools.values()):
        for pool in pools.values():
            if not pool or len(picked) == limit:
                continue
            best = min(pool, key=lambda e: per_ministry[e.ministry])
            pool.remove(best)
            per_ministry[best.ministry] += 1
            picked.append(best)
    return picked


@dataclass(frozen=True)
class CorpusResult:
    run: DiscoveryRun
    folder: Path


class CorpusService:
    def __init__(
        self,
        discovery: DiscoveryService,
        source_factory: Callable[[Path], ListingSource],  # folder -> source saving raw answers there
        listings: ListingRepository,
        fetch_service: BidFetchService,
        fetcher: SafeFetcher,
        bids: BidReader,
        budget: DailyBudget,
        link_reader: Callable[[bytes], list[PdfLink]],
        clock: Clock,
        corpus_root: Path,
        max_pages: int,
    ):
        self._discovery = discovery
        self._source_factory = source_factory
        self._listings = listings
        self._fetch = fetch_service
        self._fetcher = fetcher
        self._bids = bids
        self._budget = budget
        self._links = link_reader
        self._clock = clock
        self._root = corpus_root
        self._max_pages = max_pages

    def collect(self, keywords: Sequence[str], limit: int = CORPUS_MAX_DOWNLOADS) -> CorpusResult:
        limit = max(0, min(limit, CORPUS_MAX_DOWNLOADS))
        words = self._discovery.clean_keywords(keywords)
        # page + searches + downloads + page again + corrigenda: refuse rather than stop half-way.
        needed = 1 + len(words) * self._max_pages + limit + 1 + min(limit, CORPUS_MAX_CORRIGENDA)
        left = self._budget.daily_limit - self._budget.used_today()
        if needed > left:
            raise DiscoveryNotAllowed(f"This run needs up to {needed} requests but only {left} are left today.")
        folder = self._root / self._clock.now().astimezone(IST).strftime("%Y%m%d-%H%M")
        run = self._discovery.run(trigger="corpus", keywords=words,
                                  execute=lambda r: self._execute(r, folder, limit))
        return CorpusResult(run, folder)

    def _execute(self, run: DiscoveryRun, folder: Path, limit: int) -> None:
        rows: list[dict] = []
        try:
            source = self._source_factory(folder)
            now = self._clock.now()
            for result in source.search(run.keywords, self._max_pages):
                run.found += len(result.bids)
                run.new += len(self._listings.upsert(result.bids, result.keyword, now))

            analysed: dict[str, int] = {}
            for entry in pick_varied(self._listings.pending_downloads(10_000, now), limit):
                fetched = self._fetch.fetch_listing_entry(entry)  # safety stops propagate
                if fetched is None or fetched.pdf is None:
                    continue
                run.downloaded += 1
                analysed[entry.doc_id] = fetched.import_result.id
                rows.append(self._save(folder, entry, fetched.import_result.id, fetched.pdf))

            run.corrigenda_checked = self._discovery.check_corrigenda(
                analysed, CORPUS_MAX_CORRIGENDA, source=self._source_factory(folder))
            run.message = (f"Saved {len(rows)} bid documents (found {run.found}, {run.new} new); "
                           f"checked {run.corrigenda_checked} for corrigenda.")
        finally:  # a stopped run still records what it saved
            for row in rows:
                bid = self._bids.get(row["bid_id"])
                row["corrigendum"] = bid.corrigendum.status.value if bid else None
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "manifest.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))

    def _save(self, folder: Path, entry: ListingEntry, bid_id: int, pdf: bytes) -> dict:
        name = f"{entry.doc_id}.pdf"  # numeric document id, never portal text
        (folder / "pdf").mkdir(parents=True, exist_ok=True)
        (folder / "pdf" / name).write_bytes(pdf)
        bid = self._bids.get(bid_id)
        row = {
            "doc_id": entry.doc_id, "bid_number": entry.bid_number, "keyword": entry.keyword,
            "ministry": entry.ministry, "department": entry.department, "category": entry.category,
            "ends_at": entry.ends_at.isoformat() if entry.ends_at else None,
            "file": f"pdf/{name}", "pages": bid.page_count, "bid_id": bid_id,
            "fields_not_ok": {f.name: f.status.value for f in bid.fields if f.status is not FieldStatus.FOUND},
            "flags": len(bid.flags),
        }
        try:
            row["links"] = [{"page": link.page, "url": link.url} for link in self._links(pdf)]
        except Exception as error:  # one odd annotation must not cost the rest of the corpus
            row["links"], row["links_error"] = [], f"{type(error).__name__}: {error}"
        return row

    def probe(self, folder: Path) -> dict:
        """Ask GeM, once each, whether a buyer's specification document and uploaded terms
        file open without logging in. Run after `collect`: a login wall answers with a
        redirect, which trips the breaker by design, and that must not cost the corpus."""
        rows = json.loads((folder / "manifest.json").read_text())
        urls = [link["url"] for row in rows for link in row.get("links", [])]
        report: dict = {"robots": {}, "probes": []}
        rules: dict[str, RobotFileParser | None] = {}
        try:
            for kind, pattern in ATTACHMENT_LINKS:
                url = next((u for u in urls if pattern.match(u)), None)
                if url is None:
                    report["probes"].append({"kind": kind, "url": None, "outcome": "no_link_in_corpus"})
                else:
                    report["probes"].append(self._probe_one(folder, kind, url, rules, report["robots"]))
        finally:
            (folder / "probe.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
        return report

    def _probe_one(self, folder: Path, kind: str, url: str,
                   rules: dict[str, RobotFileParser | None], robots_report: dict[str, str]) -> dict:
        entry = {"kind": kind, "url": url}
        try:
            robots = self._robots(urlsplit(url).hostname or "", rules, robots_report)
            if robots is not None and not robots.can_fetch(self._fetcher.user_agent, url):
                entry["outcome"] = "robots_disallow"
                return entry
            response = self._fetcher.get(url, expect=Expect.PDF)  # exactly as linked, never edited
        except UnexpectedResponse as error:  # 404/410: gone, not a block
            entry["outcome"] = f"not_found: {error}"
            return entry
        except SafetyError as error:  # breaker tripped (or already open): stop, record why
            entry["outcome"] = f"stopped: {error}"
            return entry
        (folder / "attachments").mkdir(parents=True, exist_ok=True)
        entry["file"] = f"attachments/{kind}.pdf"
        (folder / entry["file"]).write_bytes(response.body)
        entry["outcome"] = "public"
        return entry

    def _robots(self, host: str, rules: dict[str, RobotFileParser | None],
                robots_report: dict[str, str]) -> RobotFileParser | None:
        """robots.txt for `host`, fetched once per probe. None when the host has none —
        RFC 9309: a missing robots.txt places no restriction."""
        if host not in rules:
            try:
                body = self._fetcher.get(f"https://{host}/robots.txt", expect=Expect.TEXT).body
            except UnexpectedResponse:
                rules[host], robots_report[host] = None, "absent (no rules)"
            else:
                parser = RobotFileParser()
                parser.parse(body.decode("utf-8", errors="replace").splitlines())
                rules[host], robots_report[host] = parser, "fetched"
        return rules[host]
