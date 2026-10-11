# First live run + bid-document corpus — design

**Date:** 2026-10-11 · **Status:** decided (owner asked for decisions, not option menus)
**Builds on:** `../../../../docs/00-RESEARCH-AND-ARCHITECTURE.md` (Phase 0/1), `../../../../docs/02-RECON-FINDINGS.md`

## 1. Why this comes first

Everything in the tool was built against **one** real bid PDF and has **never** contacted GeM:
no `data/` directory exists, Chromium is not installed, and the `/all-bids-data` JSON shape in
the tests is reconstructed from page script. The architecture's own V0 gate — "correct fields for
5 real bids, checked by hand" — has not been met. The next features (EMD amounts,
turnover/experience requirements, LLM reading of buyer terms) all need labels and text taken from
real documents, and the standing rule forbids inventing them. So: go live once, carefully, and
collect a corpus.

## 2. Success criteria

1. The product's own search path (headless browser → `/all-bids-data`) runs live once, through
   `SafeFetcher`, and its real response is saved (closes H5).
2. ≥25 real bid PDFs (target 30) saved, spread over ≥5 ministries and all 5 keywords, with a
   manifest describing each.
3. Up to 10 of them checked for a corrigendum; any real corrigendum fragment saved raw (H1).
4. The attachment question answered with evidence: does the buyer-uploaded terms (ATC) file, and
   the buyer's specification document, open without login? robots.txt for each host checked first (H2).
5. Layer-0 fields hand-verified against the PDF text for 5 corpus bids; result recorded in
   `02-RECON-FINDINGS.md`.
6. Whole exercise ≤ ~70 requests to GeM (daily cap is 250); zero requests while the contact
   email is unset.

**Out of scope:** new field rules or parser changes (next spec, driven by this corpus); the
father's own categories (same command, later batch — categories not known yet); LLM work; storing
PDF links in the database.

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | New CLI command `corpus` built from the existing guarded search + download code. | One sitting, controlled variety, no second path to GeM. Rejected: running `discover` ×6 (90 min, always picks soonest-closing, pollutes the profile); hand collection (tedious, uneven). |
| D2 | Keyword caps unchanged (≤5 keywords × ≤2 pages). Only downloads are raised for this command: hard ceiling **30** (`CORPUS_MAX_DOWNLOADS`), corrigendum checks hard ceiling **10**. Constants, not settings. | The one deliberate exception to "5 downloads per run", kept impossible to raise by env. Every per-request guard (4 s gap, 250/day, breaker, URL policy) still applies. |
| D3 | A corpus run **is a discovery run** (`trigger="corpus"`). `DiscoveryService.run()` gains optional `keywords` and an `execute` hook. | The 15-min gap, single-run lock, pre-flight safety check, run record and exception→outcome mapping (incl. `ListingRejected` tripping the breaker) are reused, not copied. |
| D4 | Variety: round-robin across keywords; within a keyword pick the entry whose ministry is least represented so far; ties keep soonest-closing order. Pure function `pick_varied()`. | Ministries and keyword types both spread; deterministic and testable. |
| D5 | `FetchResult` carries the downloaded PDF bytes; corpus writes `pdf/<doc_id>.pdf`. | The tool currently discards PDFs after analysis. Filename from the numeric doc id only, never portal text (threat T3). |
| D6 | `pdf_links(pdf_bytes) -> list[PdfLink(page, url)]` reads `/Link` `/URI` annotations with pypdf. Injected into the corpus service as a callable. | Verified on the fixture: the buyer ATC file, spec document and GTC links are embedded as annotations. Not wired into `BidAnalysis` yet (next spec). |
| D7 | Attachment check is a **separate command `probe`**, run last. Per host: fetch `robots.txt` fresh (stdlib `urllib.robotparser`; 404 → no rules, per RFC 9309). Then at most **1 spec document + 1 buyer ATC file**, exact URL as linked, `Expect.PDF`. Stop at the first safety stop. | A login wall answers with a redirect, which the classifier treats as a block and trips the breaker — correct and conservative, but it must not cost the corpus. Breaker reset stays a human action. |
| D8 | `SafeFetcher` refuses every request (and `ensure_ready()` fails) while the User-Agent carries the placeholder contact email. | Today nothing stops `set-GEM_CONTACT_EMAIL@example.com` going to GeM. One choke point covers every path. |
| D9 | `PlaywrightListingSource` saves any non-`0` corrigendum body raw as `corrigenda/<doc_id>.html` under the recon dir (discovery too). | Nobody has seen a real fragment's structure (H1). It is still shown as text and never parsed. |
| D10 | Output to `data/corpus/<YYYYMMDD-HHMM>/` (gitignored). | Bid PDFs carry consignee names and phone numbers — personal data. Chosen files are promoted to `tests/fixtures/` in the next spec. |
| D11 | Spread keywords for the first batch: `pump`, `laptop`, `manpower`, `vehicle hiring`, `medicine`. | Products and services, engineering/IT/health; ministries vary naturally. Plain words that pass GeM's own keyword validation. |
| D12 | Corpus refuses to start unless remaining daily budget ≥ worst-case request estimate `1 + keywords×pages + downloads + 1 + corrigenda`. | Never stop half-way for budget reasons. |

## 4. Components

| File | Change |
|---|---|
| `safety/errors.py` | `MissingContactEmail(SafetyError)` |
| `config.py` | `PLACEHOLDER_CONTACT` constant (used as the default and by the guard) |
| `safety/safe_fetcher.py` | refuse when `PLACEHOLDER_CONTACT in user_agent`, in `execute()` and `ensure_ready()` |
| `safety/response_classifier.py` | `Expect.TEXT` for robots.txt: judged by shape, not words (a robots file may list `/captcha/`) |
| `infrastructure/pypdf_extractor.py` | `PdfLink`, `pdf_links()` |
| `infrastructure/browser_listing.py` | optional `save_corrigenda_to: Path`; container passes `recon_dir / "corrigenda"` |
| `services/bid_fetch_service.py` | `FetchResult.pdf: bytes | None` |
| `services/discovery_service.py` | `run(trigger, keywords=None, execute=None)`; keyword cleaning factored out; public `check_corrigenda(analysed, limit)` |
| `services/corpus_service.py` (new) | `pick_varied()`, `CorpusService.collect()`, `CorpusService.probe()` |
| `container.py` | wire `CorpusService` |
| `cli.py` | `corpus [--limit N] KEYWORD...`, `probe DIR` |
| `README.md`, `docs/02-RECON-FINDINGS.md` | document commands, the guard, and findings |

## 5. Data

`manifest.json` — a JSON list, one object per saved PDF (synthetic values):

```json
{"doc_id": "9000001", "bid_number": "GEM/2026/B/7000001", "keyword": "pump",
 "ministry": "Ministry of Railways", "department": "Northern Railway",
 "category": "Centrifugal Pump", "ends_at": "2026-10-20T15:00:00+05:30",
 "file": "pdf/9000001.pdf", "pages": 10, "bid_id": 4,
 "fields_not_ok": {"startup_exemption": "NOT_FOUND"}, "flags": 7,
 "corrigendum": "NONE",
 "links": [{"page": 6, "url": "https://fulfilment.gem.gov.in/contract/slafds?fileDownloadPath=..."}]}
```

Also in the folder: `listing-response.json` (copy of the last real search response),
`corrigenda/<doc_id>.html` (raw fragments, if any). `probe` writes `probe.json`:
`{"robots": {"<host>": "fetched|absent|..."}, "probes": [{"kind": "atc|spec", "url": ..., "outcome": "public|not_found|robots_disallow|stopped: <reason>", "file": ...}]}`.

## 6. Error handling

- Safety stops, `ListingRejected`, page changes: mapped exactly as discovery maps them (D3).
- One bad PDF: `fetch_listing_entry` already marks it FAILED and continues.
- The manifest is written in a `finally`, so a stopped run still records what it saved.
- `probe`: robots fetch failing with a safety stop ends the probe; disallowed URLs are recorded
  `robots_disallow` and never requested; a block/redirect trips the breaker, is recorded with its
  reason, and ends the probe.

## 7. Testing

New/changed tests (all offline, fakes from `conftest.py` / `test_discovery.py`):
placeholder email refuses with zero transport calls; `pdf_links` on the fixture PDF finds the
fulfilment ATC link; `pick_varied` spreads keywords and ministries and honours the limit;
`collect` saves PDFs + manifest, clamps the limit to 30, refuses on short budget, records a
`corpus` run that the 15-min gap then counts; manifest survives a mid-run safety stop;
`probe` honours robots disallow (no request), treats robots 404 as no rules, records a redirect
as a stop with the breaker open, saves a public PDF. Existing 161 tests keep passing; the 7
Chromium tests run once Chromium is installed.

## 8. Run procedure

1. `chflags -R nohidden .venv` (known Mac issue), `uv run playwright install chromium`.
2. `export GEM_CONTACT_EMAIL=<owner's email>`.
3. `uv run gem-assistant corpus pump laptop "vehicle hiring" manpower medicine`.
4. Inspect manifest; then `uv run gem-assistant probe data/corpus/<stamp>`.
5. Record findings (H1, H2, H5, request count) and the 5-bid hand check in `02-RECON-FINDINGS.md`;
   promote the real listing response to `tests/fixtures/` and adjust `listing_fixtures.py` if the
   shape differs.

## 9. Known ceilings

- Rate limiter and run lock are per-process: don't run `serve` and `corpus` at the same time.
  (`ponytail:` upgrade = limiter reads the last request time from the log before each request.)
- Variety is by keyword and ministry only; bid type (product/service/BOQ) comes from the keyword mix.
