# Production Hardening — Review Findings and Plan

**Date:** 2026-10-11 · **Branch:** `corpus-and-live-run` · **Owner instruction:** "rethink each and
every part of code and make sure it would work production use git hub and internet and skills
available to you make it happen" (decide, don't ask).

"Production" here means what `../../../../docs/00-RESEARCH-AND-ARCHITECTURE.md` means: one seller
uses it daily on his own Mac. Not SaaS, not multi-user. Whole codebase reviewed on five axes
(correctness, readability, architecture, security, performance); internet research and a
dependency audit done. Every fix is test-first; each finding gets its own commit.

## Findings (most severe first)

| # | Severity | Finding | Evidence | Fix |
|---|---|---|---|---|
| F1 | Critical | Data dir (`gem-assistant/data/`: SQLite with the seller profile, bid PDFs, request log) sits on an **iCloud-synced Desktop** (`FXICloudDriveDesktop = 1`). The profile, which the architecture says never leaves the machine, is uploaded to iCloud, and SQLite under iCloud sync risks corruption. | `defaults read com.apple.finder FXICloudDriveDesktop` → 1 | Default data dir = the OS's per-user app-data dir (macOS `~/Library/Application Support/GeM Assistant`), created 0700. `GEM_DATA_DIR` still overrides. Move this machine's data. |
| F2 | Critical | CLI breaks: iCloud sets the BSD hidden flag on files inside dot-folders (`.git`, `.venv`); Python 3.12 skips hidden `.pth` files, so the editable install vanishes. | `ls -lO`: `.git/HEAD` hidden, `README.md` not; eclecticlight.co 2026-06-02 | venv at `backend/.venv.nosync` (iCloud skips `*.nosync`), `.venv` symlinked to it — done and verified. `.gitignore` both. README: install outside iCloud folders. |
| F3 | Critical | Layer-0 misses on current GeM PDFs. All 30 live bids end up NEEDS_REVIEW, so the verdict tells you nothing. | Live corpus 2026-10-11: "MSE **Relaxation**" (was "Exemption"); EMD shown as `/Advisory Bank … /EMD Amount <n>` with no `/Required`; ePBG the same; new `MII Compliance` label; page-footer lines (`3 / 11`) between label and value; service bids have no `Total Quantity`. | Field rules from the corpus; `emd_amount`, `epbg_percent`, `mii_compliance` fields; quantity no longer critical; promote varied corpus PDFs to fixtures with golden expectations. |
| F4 | Important | A corrigendum check that said NONE before the PDF deadline goes stale: a later extension gives a false "Bid has already closed". | Same bug class as the 11/30 found live | BLOCKED only if GeM said NONE **after** the document's deadline. |
| F5 | Important | A discovery run interrupted by an app restart or crash stays "running" forever in the UI. | `finished_at IS NULL` never closed | Close unfinished runs at startup as `failed: interrupted`. |
| F6 | Important | Local server sends no security headers. Any website can frame `127.0.0.1:8000` and clickjack "Resume fetching". Swagger UI pulls third-party JS. | `api/app.py` | CSP with `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`. Swagger UI off (OpenAPI JSON kept). |
| F7 | Important | SQLite: default 5 s busy timeout, rollback journal, migrations not atomic; the web thread pool and the discovery thread write concurrently. | `infrastructure/database.py` | `timeout=30`, WAL, each migration plus its `user_version` in one transaction. |
| F8 | Important | Rate limiter and "one request at a time" are per process: `serve` plus a CLI command can exceed the 4 s gap. | `safety/rate_limiter.py` | Read the last request time from the log before each request; an OS file lock serialises requests across processes. |
| F9 | Important | One oversized PDF (`ResponseTooLarge`) aborts the whole discovery run instead of skipping that bid. | `infrastructure/httpx_transport.py` | Make it a per-document failure. |
| F10 | Moderate | No one-step start for a non-developer; browser must be opened by hand. | `cli.py serve` | `serve` opens the browser (`--no-open` to skip). README run-book. |
| F11 | Moderate | Prior deferred minors: `probe` traceback without a manifest; corpus silently drops keywords past 5. | ledger 2026-10-11 | Plain-English error; print the keywords used. |
| F12 | Moderate | No CI; the private GitHub repo `Qwerty-2308/GeM` is empty; no dependency-update automation. | `gh repo view` | GitHub Actions (uv, Chromium, pytest, pip-audit), Dependabot (uv + actions), push. |
| F13 | Minor | Starlette ≥1.2 deprecates httpx in its TestClient (warning on every run). | starlette.dev release notes | Add `httpx2` to the dev group. |
| F14 | Minor | Python version not pinned for uv/CI; fastapi 0.143, pypdf 6.20 and pydantic 2.14 available. | `uv pip list --outdated`; pip-audit: no known vulns | `.python-version` 3.12; upgrade one package at a time, suite green each time. |

Kept as documented ceilings: PDF parsing runs in-process (inputs are GeM PDFs; pypdf has no known
vulnerabilities); bid list does N+1 queries (fine at hundreds of bids); the GeM listing's
live deadline is not yet shown on the bid page.

## Order of work
F1 → F2 (docs) → F3 → F4 → F5 → F9 → F6 → F7 → F8 → F10/F11 → F13/F14 → README run-book → F12 (CI, push, watch the run) → final self-review.

## Progress
