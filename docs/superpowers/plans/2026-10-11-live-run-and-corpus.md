# First Live Run + Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a guarded `corpus` command (collect ~30 varied real bid PDFs + manifest) and a `probe` command (do buyer attachments open without login?), then run both live once and record what GeM actually returned.

**Architecture:** The corpus run borrows `DiscoveryService.run()`'s guard rails (lock, 15-min gap, safety pre-check, run record, error mapping) through an `execute` hook; all requests still go through `SafeFetcher`. New code lives in `services/corpus_service.py`; small, targeted changes elsewhere (contact-email guard, PDF link reading, raw corrigendum capture, keeping downloaded PDF bytes).

**Tech Stack:** Python 3.12, uv, pytest, pypdf, Playwright (Chromium), SQLite, stdlib `urllib.robotparser`.

**Spec:** `docs/superpowers/specs/2026-10-11-live-run-and-corpus-design.md`

All paths below are relative to `gem-assistant/backend/` unless they start with `docs/` (repo root `gem-assistant/`) or `../docs/` (the project docs folder next to the repo). Run every command from `gem-assistant/backend/`.

## Global Constraints

- Every request to GeM goes through `SafeFetcher` — no other HTTP client, no direct Playwright navigation outside `PlaywrightListingSource`.
- Per-request guards unchanged: ≥4 s apart, ≤250/day, breaker trips on 401/403/407/429/5xx/redirect/challenge, no retries.
- Corpus hard ceilings are constants, not settings: `CORPUS_MAX_DOWNLOADS = 30`, `CORPUS_MAX_CORRIGENDA = 10`. Keyword caps unchanged (≤5 keywords, ≤2 pages).
- File names written to disk come from numeric doc ids or fixed words, never portal text.
- Corpus output goes to `data/corpus/<YYYYMMDD-HHMM>/` (gitignored — bid PDFs contain personal data).
- Corrigendum fragments are saved raw and shown as text; never parsed for new values.
- Breaker reset is a human action; nothing in this plan resets it.
- Match the codebase's idiom: small focused classes, docstrings that explain *why*, plain-English user messages, tests named as sentences.

## Review Focus

1. A robots.txt that legitimately contains a word like `captcha` (e.g. `Disallow: /captcha/`) must not be mistaken for a challenge page and trip the breaker → pinned in Task 6 (`Expect.TEXT`).
2. A safety stop half-way through the corpus must still leave a manifest of what was saved → pinned in Task 5.
3. A limit typed larger than the ceiling (`--limit 1000`) must be clamped, not obeyed → pinned in Task 5 (budget message shows the clamped estimate).
4. Keywords that GeM's own validation would reject (`a`, `pump*`) must be dropped before any request → pinned in Task 3.
5. A manifest with no attachment links must make zero probe requests → pinned in Task 6.

---

### Task 0: Environment and baseline

**Files:** none changed.

- [ ] **Step 1: Unhide the venv and install Chromium**

```bash
chflags -R nohidden .venv
uv run playwright install chromium
```
Expected: Chromium downloads into `~/Library/Caches/ms-playwright/`.

- [ ] **Step 2: Run the full suite — the 7 browser tests now run**

Run: `uv run pytest -q`
Expected: `168 passed` (0 skipped).

- [ ] **Step 3: Baseline commit, then a branch for this work**

The repo has staged files and no commits.
```bash
cd .. && git add -A && git commit -m "chore: baseline of gem-assistant before live run work" && git switch -c corpus-and-live-run && cd backend
```

---

### Task 1: Refuse every request while the contact email is the placeholder

**Files:**
- Modify: `src/gem_assistant/config.py` (add constant, use it as default)
- Modify: `src/gem_assistant/safety/errors.py`
- Modify: `src/gem_assistant/safety/safe_fetcher.py`
- Modify: `src/gem_assistant/services/safety_service.py` (one rule line)
- Test: `tests/test_safe_fetcher.py`

**Interfaces:**
- Produces: `gem_assistant.config.PLACEHOLDER_CONTACT: str`; `gem_assistant.safety.errors.MissingContactEmail(SafetyError)`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_safe_fetcher.py`)

```python
def test_no_request_leaves_until_a_contact_email_is_set(settings, clock, transport):
    from dataclasses import replace

    from gem_assistant.config import PLACEHOLDER_CONTACT
    from gem_assistant.container import Container
    from gem_assistant.safety.errors import MissingContactEmail

    unset = replace(settings, safety=replace(settings.safety, contact_email=PLACEHOLDER_CONTACT))
    c = Container.build(unset, clock=clock, transport=transport)
    with pytest.raises(MissingContactEmail, match="GEM_CONTACT_EMAIL"):
        c.fetcher.get("https://bidplus.gem.gov.in/all-bids")
    with pytest.raises(MissingContactEmail):
        c.fetcher.ensure_ready()
    assert transport.calls == []
    assert c.safety.status().used_today == 0
```

- [ ] **Step 2: Run it — expect ImportError on `PLACEHOLDER_CONTACT`**

Run: `uv run pytest tests/test_safe_fetcher.py -q -k contact_email`

- [ ] **Step 3: Implement**

`config.py` — above `_env_int`:
```python
# Default contact address. SafeFetcher refuses to send anything while it is in use, so
# GeM can always reach whoever runs the tool.
PLACEHOLDER_CONTACT = "set-GEM_CONTACT_EMAIL@example.com"
```
and in `SafetySettings`: `contact_email: str = PLACEHOLDER_CONTACT`.

`safety/errors.py` — append:
```python
class MissingContactEmail(SafetyError):
    """No contact email is set, so GeM could not reach whoever runs this tool."""
```

`safety/safe_fetcher.py` — import `from ..config import PLACEHOLDER_CONTACT` and `MissingContactEmail`; first line inside `with self._limiter.lock:` becomes `self._ensure_contact()`; `ensure_ready()` calls `self._ensure_contact()` first; add:
```python
    def _ensure_contact(self) -> None:
        if PLACEHOLDER_CONTACT in self.user_agent:
            raise MissingContactEmail(
                "Set GEM_CONTACT_EMAIL first: every request names a contact so GeM can reach you.")
```

`services/safety_service.py` — add to `RULES` after the User-Agent line:
```python
    "Nothing is sent to GeM until GEM_CONTACT_EMAIL is set.",
```

- [ ] **Step 4: Run the whole suite** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit** — `git commit -am "feat(safety): refuse requests while the contact email is the placeholder"`

---

### Task 2: Keep real corrigendum fragments as GeM sent them

**Files:**
- Modify: `src/gem_assistant/infrastructure/browser_listing.py` (`__init__`, `_one_corrigendum`, new `_save_corrigendum`)
- Modify: `src/gem_assistant/container.py` (pass `save_corrigenda_to`)
- Test: `tests/test_listing.py`

**Interfaces:**
- Produces: `PlaywrightListingSource(..., save_corrigenda_to: Path | None = None)` — writes `<doc_id>.html` for any body other than `0`/empty.

- [ ] **Step 1: Failing browser test** (append to `tests/test_listing.py`)

```python
@browser_test
def test_real_corrigendum_fragments_are_kept_raw(container, tmp_path):
    """Nobody has seen a real fragment's structure yet, so every one we meet is kept."""
    body = b"<div><b>Bid end date</b> extended</div>"
    gem = FakeGeM(ALL_BIDS_SHELL.read_bytes(), {}, corrigendum_bodies={"222": body})
    src = PlaywrightListingSource(fetcher=container.fetcher, network=gem, executable_path=_chromium(),
                                  timeout_seconds=20, save_corrigenda_to=tmp_path / "corr")
    src.corrigenda(["111", "222"])  # 111 answers "0": nothing to keep
    assert sorted(p.name for p in (tmp_path / "corr").iterdir()) == ["222.html"]
    assert (tmp_path / "corr" / "222.html").read_bytes() == body
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_listing.py -q -k kept_raw` → FAIL (`unexpected keyword argument 'save_corrigenda_to'`).

- [ ] **Step 3: Implement**

`__init__`: add parameter `save_corrigenda_to: Path | None = None` after `save_raw_to`, store `self._save_corrigenda_to = save_corrigenda_to`.

In `_one_corrigendum`, replace the final `return parse_corrigendum_response(...)` with:
```python
        self._save_corrigendum(doc_id, response.body)
        return parse_corrigendum_response(response.body, self._clock.now())
```
Add next to `_save_raw`:
```python
    def _save_corrigendum(self, doc_id: str, body: bytes) -> None:
        """Keep a real fragment exactly as GeM sent it — its structure has never been
        captured (docs/02-RECON-FINDINGS.md §4). "0"/empty mean "none" and are not kept."""
        if self._save_corrigenda_to is None or body.strip() in (b"", b"0") or not doc_id.isdigit():
            return
        self._save_corrigenda_to.mkdir(parents=True, exist_ok=True)
        (self._save_corrigenda_to / f"{doc_id}.html").write_bytes(body)
```

`container.py`: in `default_source`, add `save_corrigenda_to=settings.recon_dir / "corrigenda",`. (Task 5 restructures this function; keep the argument.)

- [ ] **Step 4: Run the whole suite** — all pass.

- [ ] **Step 5: Commit** — `git commit -am "feat(listing): keep raw corrigendum fragments for recon"`

---

### Task 3: Let other use cases borrow the discovery run's guard rails

**Files:**
- Modify: `src/gem_assistant/services/discovery_service.py`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Produces:
  - `DiscoveryService.clean_keywords(raw: Sequence[str]) -> list[str]`
  - `DiscoveryService.run(trigger: str = "manual", keywords: Sequence[str] | None = None, execute: Callable[[DiscoveryRun], None] | None = None) -> DiscoveryRun` — if `execute` sets `run.message`, it is kept.
  - `DiscoveryService.check_corrigenda(analysed: dict[str, int], limit: int | None = None, source: ListingSource | None = None) -> int`

- [ ] **Step 1: Failing tests** (append to `tests/test_discovery.py`)

```python
def test_run_lends_its_guard_rails_to_other_steps(app):
    seen = []

    def execute(run):
        seen.append(run.keywords)
        run.message = "custom"

    run = app.discovery.run(trigger="corpus", keywords=["laptop", "a", "pump*", "Laptop"], execute=execute)
    assert (run.outcome, run.message, run.trigger) == ("ok", "custom", "corpus")
    assert seen == [["laptop"]]  # GeM's own keyword rules still apply


def test_given_keywords_that_are_all_unusable_are_refused(app, source):
    with pytest.raises(DiscoveryNotAllowed, match="keywords"):
        app.discovery.run(trigger="corpus", keywords=["a", "x*"], execute=lambda run: None)
    assert source.calls == []
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_discovery.py -q -k "lends or unusable"` → FAIL (`unexpected keyword argument 'keywords'`).

- [ ] **Step 3: Implement** in `discovery_service.py`

Add `Sequence` to the `typing` import (`ListingSource` is already imported). Replace `keywords()` with:
```python
    def keywords(self) -> list[str]:
        profile = self._profiles.get()
        return self.clean_keywords(profile.categories if profile else [])

    def clean_keywords(self, raw: Sequence[str]) -> list[str]:
        """GeM's own search-box rules, duplicates dropped, capped at max_keywords."""
        seen: dict[str, str] = {}
        for item in raw:
            keyword = " ".join(item.split())
            if len(keyword) < 3 or _INVALID_KEYWORD.search(keyword):
                continue
            seen.setdefault(keyword.lower(), keyword)
        return list(seen.values())[: self._settings.max_keywords]
```
Replace `start_in_background`, `run`, `_claim`, `_run_claimed` signatures/bodies:
```python
    def start_in_background(self, trigger: str = "manual") -> None:
        keywords = self._claim(trigger)
        thread = threading.Thread(target=self._run_claimed, args=(trigger, keywords, self._execute),
                                  daemon=True, name="gem-discovery")
        thread.start()

    def run(self, trigger: str = "manual", keywords: Sequence[str] | None = None,
            execute: Callable[[DiscoveryRun], None] | None = None) -> DiscoveryRun:
        """`keywords` and `execute` let another use case (the corpus) borrow this run's
        guard rails — one run at a time, the 15-minute gap, the safety pre-check, the run
        record and its error handling — instead of copying them. By default: the
        profile's categories and the normal discovery steps."""
        claimed = self._claim(trigger, keywords)
        return self._run_claimed(trigger, claimed, execute or self._execute)

    def _claim(self, trigger: str, keywords: Sequence[str] | None = None) -> list[str]:
        """Check every precondition and take the single-run lock, or raise."""
        if not self._lock.acquire(blocking=False):
            raise DiscoveryNotAllowed("A search is already running.")
        try:
            claimed = self.keywords() if keywords is None else self.clean_keywords(keywords)
            if not claimed:
                raise DiscoveryNotAllowed(
                    "Add your product categories to the seller profile first; they are the search keywords."
                    if keywords is None else
                    "None of those keywords can be searched on GeM (3+ characters, no special characters).")
            allowed_at = self.next_allowed_at()
            if allowed_at and self._clock.now() < allowed_at:
                minutes = int((allowed_at - self._clock.now()).total_seconds() // 60) + 1
                raise DiscoveryNotAllowed(
                    f"GeM updates its listing every 15 minutes. Try again in {minutes} min.")
            self._fetcher.ensure_ready()  # contact set, breaker closed, daily budget left
            return claimed
        except SafetyError as error:
            self._lock.release()
            raise DiscoveryNotAllowed(str(error)) from error
        except BaseException:
            self._lock.release()
            raise

    def _run_claimed(self, trigger: str, keywords: list[str],
                     execute: Callable[[DiscoveryRun], None]) -> DiscoveryRun:
        run = DiscoveryRun(trigger=trigger, started_at=self._clock.now(), keywords=keywords)
        sent_before = self._fetcher.requests_sent
        try:
            run.id = self._runs.start(run)
            execute(run)
            run.outcome = "ok"
            run.message = run.message or (
                f"Found {run.found} bids ({run.new} new); analysed {run.downloaded}; "
                f"checked {run.corrigenda_checked} for corrigenda.")
        except ListingRejected as error:
```
(the `except`/`finally` blocks below stay exactly as they are). In `_execute`, change the last line to `run.corrigenda_checked = self.check_corrigenda(analysed)`. Rename `_check_corrigenda` to:
```python
    def check_corrigenda(self, analysed: dict[str, int], limit: int | None = None,
                         source: ListingSource | None = None) -> int:
```
with first line `doc_ids = list(analysed)[: self._settings.max_corrigendum_checks_per_run if limit is None else limit]` and `found = (source or self._source_factory()).corrigenda(doc_ids)`. Keep the docstring and the counting lines.

- [ ] **Step 4: Run the whole suite** — all pass.

- [ ] **Step 5: Commit** — `git commit -am "refactor(discovery): let other use cases borrow the run's guard rails"`

---

### Task 4: Pure helpers — read PDF links, pick a varied set

**Files:**
- Modify: `src/gem_assistant/infrastructure/pypdf_extractor.py` (`PdfLink`, `pdf_links`)
- Create: `src/gem_assistant/services/corpus_service.py` (constants + `pick_varied` only in this task)
- Test: `tests/test_corpus.py` (new)

**Interfaces:**
- Produces: `PdfLink(page: int, url: str)` (frozen dataclass); `pdf_links(pdf_bytes: bytes) -> list[PdfLink]` (deduplicated, document order); `pick_varied(entries: Sequence[ListingEntry], limit: int) -> list[ListingEntry]`; `CORPUS_MAX_DOWNLOADS = 30`; `CORPUS_MAX_CORRIGENDA = 10`.

- [ ] **Step 1: Failing tests** — create `tests/test_corpus.py`:

```python
"""The corpus command: collect real bid documents, and probe buyer attachments."""

from __future__ import annotations

from datetime import datetime, timezone

from gem_assistant.domain.models import BidKind, DocStatus, ListingEntry
from gem_assistant.infrastructure.pypdf_extractor import pdf_links
from gem_assistant.services.corpus_service import pick_varied

from .conftest import SAMPLE_PDF

T = datetime(2025, 11, 1, tzinfo=timezone.utc)


def test_pdf_links_find_the_buyer_attachment_and_spec_document():
    links = pdf_links(SAMPLE_PDF.read_bytes())
    atc = [l for l in links if l.url.startswith("https://fulfilment.gem.gov.in/contract/slafds?")]
    assert [l.page for l in atc] == [6]
    assert any(l.page == 5 and "SpecificationDocument" in l.url for l in links)
    assert len(links) == len(set(links))  # page 3 links the same URL twice


def _entry(n: int, keyword: str, ministry: str) -> ListingEntry:
    return ListingEntry(doc_id=str(n), bid_number=f"GEM/2026/B/{n}", kind=BidKind.BID,
                        doc_path="showbidDocument", keyword=keyword, ministry=ministry,
                        first_seen_at=T, last_seen_at=T, doc_status=DocStatus.PENDING)


def test_pick_varied_rotates_keywords_and_spreads_ministries():
    entries = [_entry(1, "pump", "Railways"), _entry(2, "pump", "Railways"), _entry(3, "pump", "Defence"),
               _entry(4, "laptop", "Railways"), _entry(5, "laptop", "Health")]
    assert [e.doc_id for e in pick_varied(entries, 4)] == ["1", "5", "3", "4"]
    assert len(pick_varied(entries, 99)) == 5
    assert pick_varied(entries, 0) == []
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_corpus.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**

`pypdf_extractor.py` — add `from dataclasses import dataclass` and at the end:
```python
@dataclass(frozen=True)
class PdfLink:
    page: int
    url: str


def pdf_links(pdf_bytes: bytes) -> list[PdfLink]:
    """Web links embedded in the PDF as link annotations. GeM puts the buyer's uploaded
    terms file and specification document here, not in the page text."""
    found: dict[PdfLink, None] = {}
    for number, page in enumerate(PdfReader(io.BytesIO(pdf_bytes)).pages, start=1):
        for annotation in page.get("/Annots") or []:
            action = annotation.get_object().get("/A")
            uri = action.get_object().get("/URI") if action else None
            if isinstance(uri, str) and uri:
                found[PdfLink(number, str(uri))] = None
    return list(found)
```

Create `services/corpus_service.py`:
```python
"""Collects real bid documents for parser work, and checks once whether the buyer
attachments they link to open without login.

Why it exists: the parser was written against a single bid PDF, and new labels may only
come from real documents (docs/02-RECON-FINDINGS.md). A corpus run is a discovery run
(trigger "corpus") that downloads more documents than usual, chosen for variety, and
keeps them on disk with a manifest. Every request still goes through SafeFetcher.
"""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from ..domain.models import ListingEntry

# Hard ceilings for this one command. Constants, not settings, so nothing can raise them.
CORPUS_MAX_DOWNLOADS = 30
CORPUS_MAX_CORRIGENDA = 10


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
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_corpus.py -q` → 2 passed.

- [ ] **Step 5: Commit** — `git add -A . && git commit -m "feat(corpus): read PDF links and pick a varied set of bids"`

---

### Task 5: `corpus` command — collect PDFs and a manifest

**Files:**
- Modify: `src/gem_assistant/services/bid_fetch_service.py` (`FetchResult.pdf`)
- Modify: `src/gem_assistant/services/corpus_service.py` (`CorpusResult`, `CorpusService.collect`)
- Modify: `src/gem_assistant/container.py` (source builder, wire `corpus`)
- Modify: `src/gem_assistant/cli.py` (`corpus` subcommand)
- Test: `tests/test_corpus.py`

**Interfaces:**
- Consumes: `DiscoveryService.run/clean_keywords/check_corrigenda` (Task 3); `pick_varied`, `pdf_links`, constants (Task 4).
- Produces: `FetchResult.pdf: bytes | None`; `CorpusService.collect(keywords: Sequence[str], limit: int = CORPUS_MAX_DOWNLOADS) -> CorpusResult`; `CorpusResult(run: DiscoveryRun, folder: Path)`; `Container.corpus: CorpusService`. Manifest row keys: `doc_id, bid_number, keyword, ministry, department, category, ends_at, file, pages, bid_id, fields_not_ok (dict name→status), flags, links (list of {page,url}), corrigendum`, plus `links_error` when links could not be read.

- [ ] **Step 1: Failing tests** — append to `tests/test_corpus.py` (add imports at top: `import json`, `from dataclasses import replace`, `import pytest`, `from gem_assistant.container import Container`, `from gem_assistant.domain.models import SellerProfile`, `from gem_assistant.services.discovery_service import DiscoveryNotAllowed`, `from .listing_fixtures import make_doc`, `from .test_discovery import FakeSource`):

```python
DEFENCE = "Ministry of Defence"


@pytest.fixture
def source() -> FakeSource:
    return FakeSource(docs=[
        make_doc(1, final_end_date_sort="2026-10-20T15:00:00Z"),
        make_doc(2, final_end_date_sort="2026-10-21T15:00:00Z", ba_official_details_minName=DEFENCE),
        make_doc(3, bid_type=5),  # reverse auction: never downloaded
    ])


@pytest.fixture
def app(settings, clock, transport, source) -> Container:
    c = Container.build(settings, clock=clock, transport=transport, listing_source_factory=source.bind)
    c.profiles.save(SellerProfile(business_name="Test", categories=["Pump"]))
    return c


def _queue_pdf(transport):
    transport.queue(body=SAMPLE_PDF.read_bytes(), headers={"content-type": "application/pdf"})


def test_collect_saves_pdfs_and_a_manifest(app, transport):
    _queue_pdf(transport)
    _queue_pdf(transport)
    result = app.corpus.collect(["pump"], limit=5)
    assert (result.run.outcome, result.run.trigger) == ("ok", "corpus"), result.run.message

    manifest = json.loads((result.folder / "manifest.json").read_text())
    assert [row["doc_id"] for row in manifest] == ["9000001", "9000002"]
    assert (result.folder / "pdf" / "9000001.pdf").read_bytes() == SAMPLE_PDF.read_bytes()
    row = manifest[0]
    assert (row["keyword"], row["pages"], row["corrigendum"]) == ("pump", 10, "NONE")
    assert any(link["url"].startswith("https://fulfilment.gem.gov.in/") for link in row["links"])
    assert manifest[1]["ministry"] == DEFENCE
    assert "Saved 2 bid documents" in result.run.message


def test_collect_refuses_when_todays_budget_cannot_cover_it(settings, clock, transport, source):
    tight = replace(settings, safety=replace(settings.safety, daily_request_limit=20))
    c = Container.build(tight, clock=clock, transport=transport, listing_source_factory=source.bind)
    with pytest.raises(DiscoveryNotAllowed, match="up to 44 requests"):  # 1 + 1×2 + 30 + 1 + 10: limit clamped
        c.corpus.collect(["pump"], limit=1000)
    assert source.calls == [] and transport.calls == []


def test_a_corpus_run_counts_toward_the_gap_between_searches(app, transport):
    _queue_pdf(transport)
    app.corpus.collect(["pump"], limit=1)
    with pytest.raises(DiscoveryNotAllowed, match="15 minutes"):
        app.discovery.run()


def test_the_manifest_survives_a_safety_stop(app, transport):
    _queue_pdf(transport)
    transport.queue(status=403, body=b"denied")
    result = app.corpus.collect(["pump"], limit=5)
    assert result.run.outcome == "stopped"
    manifest = json.loads((result.folder / "manifest.json").read_text())
    assert [row["doc_id"] for row in manifest] == ["9000001"]
    assert app.safety.status().breaker.open
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_corpus.py -q` → FAIL (`Container` has no `corpus`).

- [ ] **Step 3: Implement**

`bid_fetch_service.py` — `FetchResult` gains `pdf: bytes | None = None  # the downloaded document; None when nothing was downloaded`; `fetch_document` returns `FetchResult(result, downloaded=True, pdf=response.body)`.

`corpus_service.py` — extend imports and append:
```python
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..domain.models import DiscoveryRun, FieldStatus, ListingEntry
from ..domain.ports import BidReader, Clock, ListingRepository, ListingSource
from ..infrastructure.pypdf_extractor import PdfLink
from ..parsing.normalizers import IST
from ..safety.daily_budget import DailyBudget
from .bid_fetch_service import BidFetchService
from .discovery_service import DiscoveryNotAllowed, DiscoveryService


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
```

`container.py` — import `from pathlib import Path`, `from .infrastructure.pypdf_extractor import PypdfTextExtractor, pdf_links`, `from .services.corpus_service import CorpusService`; add field `corpus: CorpusService` to `Container`. Replace `default_source`/`make_source` with:
```python
        def source_for(raw_to: Path, corrigenda_to: Path) -> ListingSource:
            if listing_source_factory is not None:
                return listing_source_factory(fetcher)
            return PlaywrightListingSource(
                fetcher=fetcher,
                network=PlaywrightNetwork(settings.safety.request_timeout_seconds),
                executable_path=settings.discovery.browser_executable_path,
                timeout_seconds=settings.discovery.browser_timeout_seconds,
                save_raw_to=raw_to,
                save_corrigenda_to=corrigenda_to,
                clock=clock,
            )
```
`DiscoveryService(source_factory=lambda: source_for(settings.recon_dir / "last-listing-response.json", settings.recon_dir / "corrigenda"), ...)`, then:
```python
        corpus = CorpusService(
            discovery=discovery,
            source_factory=lambda folder: source_for(folder / "listing-response.json", folder / "corrigenda"),
            listings=listing_repo,
            fetch_service=bid_fetch,
            bids=bid_repo,
            budget=budget,
            link_reader=pdf_links,
            clock=clock,
            corpus_root=settings.data_dir / "corpus",
            max_pages=settings.discovery.max_pages_per_keyword,
        )
```
and pass `corpus=corpus` in `cls(...)`.

`cli.py` — docstring line `gem-assistant corpus KEYWORD...    collect real bid documents for parser work (one run)`; subparser:
```python
    corpus = sub.add_parser("corpus", help="collect real bid documents for parser work (one guarded run)")
    corpus.add_argument("keywords", nargs="+")
    corpus.add_argument("--limit", type=int, default=30, help="documents to download (at most 30)")
```
handler (before `return 1`):
```python
    if args.command == "corpus":
        from .services.discovery_service import DiscoveryNotAllowed

        try:
            result = container.corpus.collect(args.keywords, args.limit)
        except DiscoveryNotAllowed as error:
            print(f"Not started: {error}")
            return 2
        run = result.run
        print(f"[{run.outcome}] {run.message}  (requests to GeM: {run.requests})")
        print(f"Folder: {result.folder}")
        return 0 if run.outcome == "ok" else 1
```

- [ ] **Step 4: Run the whole suite** — all pass.

- [ ] **Step 5: Commit** — `git add -A . && git commit -m "feat(corpus): collect varied real bid documents with a manifest"`

---

### Task 6: `probe` command — do buyer attachments open without login?

**Files:**
- Modify: `src/gem_assistant/safety/response_classifier.py` (`Expect.TEXT`)
- Modify: `src/gem_assistant/services/corpus_service.py` (`probe`)
- Modify: `src/gem_assistant/container.py` (pass `fetcher`)
- Modify: `src/gem_assistant/cli.py` (`probe` subcommand)
- Test: `tests/test_corpus.py`, `tests/test_safe_fetcher.py`

**Interfaces:**
- Consumes: manifest written by Task 5.
- Produces: `Expect.TEXT`; `CorpusService.probe(folder: Path) -> dict` with keys `robots: dict[host, str]`, `probes: list[{kind, url, outcome, file?}]`; writes `probe.json`. `CorpusService.__init__` gains `fetcher: SafeFetcher` (keyword argument).

- [ ] **Step 1: Failing tests**

Append to `tests/test_safe_fetcher.py`:
```python
def test_plain_text_is_judged_by_shape_not_by_words():
    from gem_assistant.domain.ports import HttpResponse
    from gem_assistant.safety.response_classifier import Expect, ResponseClassifier, ResponseKind

    robots = HttpResponse(url="", status=200, headers={}, body=b"User-agent: *\nDisallow: /captcha/\n")
    page = HttpResponse(url="", status=200, headers={}, body=b"<!DOCTYPE html><html>Request Rejected</html>")
    assert ResponseClassifier().classify(robots, Expect.TEXT).kind is ResponseKind.OK
    assert ResponseClassifier().classify(page, Expect.TEXT).kind is ResponseKind.BLOCK
```
Append to `tests/test_corpus.py`:
```python
SPEC_URL = ("https://mkp.gem.gov.in/catalog_data/catalog_support_document/buyer_documents/1/2/"
            "CatalogAttrs/SpecificationDocument/2025/10/24/spec_abc.pdf")
ATC_URL = "https://fulfilment.gem.gov.in/contract/slafds?fileDownloadPath=SLA_UPLOAD_PATH/2025/Oct/X/ATC_1.pdf"


def _folder_with_links(tmp_path, urls):
    folder = tmp_path / "corpus"
    folder.mkdir()
    rows = [{"doc_id": "1", "links": [{"page": 5, "url": u} for u in urls]}]
    (folder / "manifest.json").write_text(json.dumps(rows))
    return folder


def test_probe_never_requests_a_file_robots_txt_disallows(container, transport, tmp_path):
    folder = _folder_with_links(tmp_path, [SPEC_URL, ATC_URL])
    transport.queue(body=b"User-agent: *\nDisallow: /catalog_data/\n")
    transport.queue(body=b"User-agent: *\nDisallow: /\n")
    report = container.corpus.probe(folder)
    assert [p["outcome"] for p in report["probes"]] == ["robots_disallow", "robots_disallow"]
    assert [url for url, _ in transport.calls] == [
        "https://mkp.gem.gov.in/robots.txt", "https://fulfilment.gem.gov.in/robots.txt"]
    assert json.loads((folder / "probe.json").read_text()) == report


def test_probe_saves_a_public_file_and_stops_at_a_login_redirect(container, transport, tmp_path):
    folder = _folder_with_links(tmp_path, [SPEC_URL, ATC_URL])
    transport.queue(status=404, body=b"")                     # mkp has no robots.txt: no rules
    transport.queue(body=SAMPLE_PDF.read_bytes())             # the spec document
    transport.queue(status=404, body=b"")                     # fulfilment has no robots.txt
    transport.queue(status=302, body=b"", headers={"location": "https://sso.gem.gov.in/ARXSSO/oauth/doLogin"})
    report = container.corpus.probe(folder)
    spec, atc = report["probes"]
    assert spec["outcome"] == "public"
    assert (folder / spec["file"]).read_bytes() == SAMPLE_PDF.read_bytes()
    assert atc["outcome"].startswith("stopped:") and "sso.gem.gov.in" in atc["outcome"]
    assert set(report["robots"].values()) == {"absent (no rules)"}
    assert container.safety.status().breaker.open


def test_probe_with_no_attachment_links_sends_nothing(container, transport, tmp_path):
    folder = _folder_with_links(tmp_path, ["https://example.org/other.pdf"])
    report = container.corpus.probe(folder)
    assert [p["outcome"] for p in report["probes"]] == ["no_link_in_corpus", "no_link_in_corpus"]
    assert transport.calls == []
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_corpus.py tests/test_safe_fetcher.py -q` → FAIL (`Expect` has no `TEXT`; `CorpusService` has no `probe`).

- [ ] **Step 3: Implement**

`response_classifier.py` — add to `Expect`:
```python
    # Plain text such as robots.txt. Like a fragment, judged by shape: a robots file may
    # legitimately list a path like /captcha/.
    TEXT = "text"
```
and change `if expect is Expect.HTML_FRAGMENT:` to `if expect in (Expect.HTML_FRAGMENT, Expect.TEXT):`.

`corpus_service.py` — add imports `import re`, `from urllib.parse import urlsplit`, `from urllib.robotparser import RobotFileParser`, `from ..safety.errors import SafetyError, UnexpectedResponse`, `from ..safety.response_classifier import Expect`, `from ..safety.safe_fetcher import SafeFetcher`; add module constants:
```python
# Link shapes seen in a real bid PDF (recon/fixtures, docs/02-RECON-FINDINGS.md §7).
ATTACHMENT_LINKS = (
    ("spec", re.compile(r"^https://mkp\.gem\.gov\.in/catalog_data/.*SpecificationDocument/.*\.pdf$")),
    ("atc", re.compile(r"^https://fulfilment\.gem\.gov\.in/contract/slafds\?")),
)
```
add `fetcher: SafeFetcher` to `__init__` (store `self._fetcher`), and methods:
```python
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
```

`container.py` — pass `fetcher=fetcher,` to `CorpusService(...)`.

`cli.py` — docstring line `gem-assistant probe FOLDER       check once whether buyer attachments open without login`; subparser:
```python
    probe = sub.add_parser("probe", help="after corpus: check once whether buyer attachments open without login")
    probe.add_argument("folder", type=Path)
```
handler:
```python
    if args.command == "probe":
        report = container.corpus.probe(args.folder)
        for host, state in report["robots"].items():
            print(f"robots.txt {host}: {state}")
        for item in report["probes"]:
            print(f"{item['kind']}: {item['outcome']}")
        if container.safety.status().breaker.open:
            print("Fetching is now stopped. Check GeM in a normal browser, then reset it on the Safety page.")
        return 0
```

- [ ] **Step 4: Run the whole suite** — all pass.

- [ ] **Step 5: Commit** — `git add -A . && git commit -m "feat(corpus): probe whether buyer attachments open without login"`

---

### Task 7: Document it

**Files:**
- Modify: `README.md` (repo root `gem-assistant/README.md`)
- Modify: `../docs/02-RECON-FINDINGS.md`

- [ ] **Step 1: README** — in "Command-line equivalents" add:
```bash
uv run gem-assistant corpus pump laptop manpower   # one guarded run: up to 30 varied bid PDFs + manifest
uv run gem-assistant probe data/corpus/<folder>     # afterwards: do buyer attachments open without login?
```
Add a section "How the corpus command works" (what it saves where, why ≤30, why probe is separate and may trip the breaker, that `data/` holds personal data). In "What exists so far" add rows for corpus + probe. In "Safety rules" add "Nothing is sent until `GEM_CONTACT_EMAIL` is set." and amend the searches line: "…and 5 downloads per search (the one-off `corpus` command: at most 30)". Note under "Layout": `services/corpus_service.py`.

- [ ] **Step 2: Recon findings** — add §7 "Links inside the bid PDF — added 11-Oct-2026" listing the six annotations found in the fixture (page, host, what it is), the two link shapes `ATTACHMENT_LINKS` relies on, and that reachability is settled by `probe` (Task 8). Update §6 row H2: "URL is in the PDF (§7); reachability: see §8".

- [ ] **Step 3: Commit** — `git add -A .. && git commit -m "docs: corpus and probe commands"` (README is inside the repo; `../docs` is outside it and is not committed — note this in the hand-off).

---

### Task 8: Live run and findings (operational — touches GeM)

**Files:**
- Create: `tests/fixtures/all-bids-data_<date>.json` (real response, promoted)
- Modify: `tests/listing_fixtures.py` docstring, `tests/test_listing.py` (one test on the real response)
- Modify: `../docs/02-RECON-FINDINGS.md` (§8 live findings)

- [ ] **Step 1: Run the corpus** (contact email = owner's account email, as agreed)

```bash
export GEM_CONTACT_EMAIL=divyeshkarthik5@gmail.com
uv run gem-assistant corpus pump laptop manpower "vehicle hiring" medicine
```
Expected: `[ok] Saved N bid documents …`, N ≥ 25, requests ≤ 55. If `[stopped]` or `[failed]`: do **not** retry; record the message, the request log (`data/gem.sqlite3` → `request_log`) and the saved files, and go to Step 5 with what exists.

- [ ] **Step 2: Inspect** — count ministries and keywords in `manifest.json`; list the union of `fields_not_ok` across rows with counts; check `listing-response.json` against `tests/listing_fixtures.py::make_doc` field names.

- [ ] **Step 3: Probe** — `uv run gem-assistant probe data/corpus/<folder>`; read `probe.json`. If the breaker tripped, leave it tripped and tell the owner (reset is theirs).

- [ ] **Step 4: Hand-check 5 bids** — for 5 PDFs from different ministries, compare every FOUND Layer-0 value (DB `bid_field` rows) with the PDF text extracted by pypdf; note any wrong or missed value.

- [ ] **Step 5: Promote the real listing response to a fixture and pin it**

Copy `data/corpus/<folder>/listing-response.json` to `tests/fixtures/all-bids-data_<YYYY-MM-DD>.json`, after replacing any personal names/phones in it with placeholders if present. Add to `tests/test_listing.py`:
```python
def test_a_real_listing_response_parses():
    data = json.loads((FIXTURES / "all-bids-data_<YYYY-MM-DD>.json").read_text())
    page = parse_listing_response(data)
    assert page.bids and page.skipped == 0
    assert all(b.bid_number.startswith("GEM/") and b.doc_id.isdigit() for b in page.bids)
```
(import `json` and `FIXTURES` from `.conftest`). Update the `listing_fixtures.py` docstring to say the constructed JSON was checked against this real response (and note any differences found). Run `uv run pytest -q`.

- [ ] **Step 6: Record findings** — `../docs/02-RECON-FINDINGS.md` §8 "First live run — <date>": request count, outcome, H5 (real shape vs constructed), H1 (any corrigendum fragment, structure), H2 (probe outcomes with robots results), field coverage table from Step 2, hand-check results from Step 4; update §6 statuses.

- [ ] **Step 7: Commit** — `git add -A . && git commit -m "test: pin a real /all-bids-data response from the first live run"`
