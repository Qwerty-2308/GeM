# GeM Assistant

A local, read-only tool that analyses GeM bid documents against your seller
profile and shows evidence (page and exact quote) for every value. Design
rationale lives in `../docs/00-RESEARCH-AND-ARCHITECTURE.md`.

## Install (once)

1. **Put this folder somewhere iCloud, Dropbox or OneDrive does not sync**, e.g.
   `~/gem-assistant`. With "Desktop & Documents" in iCloud turned on, iCloud marks files
   inside dot-folders (like `.venv`) as hidden, and Python then skips the file that makes the
   command work. If it has to stay in a synced folder, keep the environment out of sync:
   `cd backend && UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync && ln -s .venv.nosync .venv`
   (iCloud never syncs a name ending in `.nosync`; this is how this repo is set up on its
   development Mac).
2. Install [uv](https://docs.astral.sh/uv/): `brew install uv`.
3. Install the app and the headless browser it uses for searching:
   ```bash
   cd backend
   uv sync
   uv run playwright install chromium
   ```
4. Give the chat its Groq API key (from console.groq.com; typed, not shown):
   ```bash
   uv run gem-assistant set-groq-key
   ```
   It is kept in the data folder (`groq-api-key.txt`, owner-only), never in this project, so it
   can't end up in git. `GROQ_API_KEY` works too; `GROQ_MODEL` changes the model
   (default `openai/gpt-oss-120b`).
5. Make it an app (macOS):
   ```bash
   uv run gem-assistant install-app
   ```
   This adds **GeM Assistant** to `~/Applications` (find it in Launchpad; drag it to the Dock)
   and registers the app to run in the background from login. macOS shows a "Background
   Items Added" notice once; that's this. From now on he only double-clicks the icon.
6. Open it. The first time it asks for an email, so every request names someone GeM can
   reach. **Nothing is sent to GeM until it is set.** It is kept in the data folder
   (`contact-email.txt`); `GEM_CONTACT_EMAIL` works too, but the app's value wins.
7. Tell the chat what he sells ("I sell fire extinguishers and office chairs, turnover 50 lakh,
   I'm an MSE"). It shows the profile change; he clicks **Confirm**.

## Run it

Double-click **GeM Assistant**. It opens a chat at http://127.0.0.1:8000. He asks in Hindi,
English or Hinglish: find bids, which close soon, what a bid needs. The chat runs the search,
download, parsing and eligibility checks as tools and shows their results as cards: verdict,
each reason, and the page and quote it came from. The figures in the cards come from the
pipeline, not from the model's wording. The server behind it is already running (it starts at
login and again after a crash) and only listens on this computer. Its output goes to `app.log`
in the data folder.

**What the chat sends to Groq:** what he types, and what the tools return: his products,
turnover, experience, MSE status, and bid details including quoted clauses. Not the contact
email, not the Groq key. The chat cannot reset the safety stop, change the contact email or
fetch anything outside GeM, and profile changes it suggests wait for his click.

The earlier pages (bid list, bid evidence, Safety) are no longer in a menu; chat cards link to
them, and `#/bids`, `#/safety` still open them.

Without the app (or on Linux):

```bash
cd backend
uv run gem-assistant serve        # starts on http://127.0.0.1:8000 and opens it (--no-open to skip)
```

Stop it with Ctrl-C. If the app's server is already running, `serve` just opens the page.

To remove the app: `launchctl bootout gui/$(id -u)/local.gem-assistant.server`, then delete
`~/Library/LaunchAgents/local.gem-assistant.server.plist` and `~/Applications/GeM Assistant.app`.
After moving the project folder, run `install-app` again.

## Your data

Everything the app stores is in **`~/Library/Application Support/GeM Assistant`**
(`$XDG_DATA_HOME/gem-assistant` on Linux; set `GEM_DATA_DIR` to use another folder). It is
kept out of the project on purpose: the project may sit in a synced folder, and the seller
profile must never leave this computer. The folder is owner-only.

| What | Where |
|---|---|
| Seller profile, bids, verdict inputs, request log, safety state | `gem.sqlite3` |
| Every analysed bid PDF, named by its SHA-256 | `documents/` |
| Corpus runs | `corpus/<YYYYMMDD-HHMM>/` |

**Backup:** Time Machine covers it. To copy it by hand, stop the app first and copy the
whole folder.

## After updating the code

```bash
git pull
cd backend && uv sync
```

The next `serve` re-reads every stored bid whose analysis came from older rules, from its
kept PDF (`gem-assistant reanalyse` does the same on demand). GeM changes its bid form every
few months, so this is how a parser fix reaches bids you already have.

Command-line equivalents:

```bash
uv run gem-assistant import path/to/bid.pdf     # analyse a PDF you already have (no network)
uv run gem-assistant fetch https://bidplus.gem.gov.in/showbidDocument/8506455
uv run gem-assistant discover                   # one search of the public listing
uv run gem-assistant corpus pump laptop manpower   # one guarded run: up to 30 varied bid PDFs + manifest
uv run gem-assistant probe <data folder>/corpus/<YYYYMMDD-HHMM>   # afterwards: do buyer attachments open without login?
uv run gem-assistant reanalyse                   # re-read stored bids with the current rules
```

Run the tests:

```bash
uv run pytest
```

## Troubleshooting

**`No module named 'gem_assistant'`.** The project is in an iCloud-synced folder and iCloud
has marked the venv's files hidden; Python 3.12 skips hidden `.pth` files. Check with
`ls -lO .venv/lib/python3.12/site-packages/*.pth` (the flags column reads `hidden`). Move
the project out of the synced folder, or use the `.venv.nosync` setup in Install step 1.
`chflags -R nohidden .venv` works only until the next `uv sync`.

**"Add your contact email on the Safety page first".** Type it on the Find bids or Safety page.

**"Fetching is stopped".** GeM answered with something that looks like a block, a login
wall or an error, and the tool stopped itself. Open GeM in a normal browser; if it loads
normally, resume on the Safety page and say what you checked.

**"GeM updates its listing every 15 minutes".** Searches are at least 15 minutes apart, on
purpose.

## What exists so far

| Area | Status |
|---|---|
| Frontend: bids, bid detail with evidence, find bids, seller profile, safety | done |
| Local PDF import → field extraction → clause flags → eligibility verdict | done |
| Safety layer (`SafeFetcher`): URL policy, rate limit, daily cap, circuit breaker, request log | done; every GeM request goes through it |
| Add a bid by its `showbidDocument` link | done; 1 request, never repeated for the same document |
| Search the public `/all-bids` listing (headless browser) | done; **run live on 11-Oct-2026** (56 requests, no block signals) |
| Corrigendum check for the bids it analyses | done; shown verbatim, never parsed into a new deadline |
| Corpus collection (`corpus`): varied real bid PDFs + manifest for parser work | done |
| Attachment check (`probe`): robots.txt first, then one spec document and one buyer terms file | done; buyer ATC files are public |
| Current GeM form (2026): relaxations (complete/partial), EMD/ePBG amounts, MII compliance | done; checked on 30 live bids |
| Turnover and experience requirements compared with the profile | done; blocks only on a clear shortfall no relaxation covers |
| Bid PDFs kept; stored bids re-read when the rules improve | done; automatic on `serve` |
| Mac app (`install-app`): icon opens the page; server runs in the background from login | done; checked on a Mac: survives the icon quitting, restarts after a crash |
| Chat (Groq) as the only screen; the pipeline as its tools | built and tested with a scripted model; **not yet run against Groq itself** (needs the key) |
| LLM reading of free-text ATC, ATC attachments | later |

## How bid search works

`infrastructure/browser_listing.py` opens `https://bidplus.gem.gov.in/all-bids` in headless Chromium:

- Only two kinds of request reach GeM: the page itself, and the page's own search request (`POST /all-bids-data`, made by GeM's JavaScript when we trigger its search). Both go through `SafeFetcher`.
- The page's scripts are served from `browser_assets/` (jQuery 3.6.0 + no-op stubs). `/resources/` is never fetched, which matches robots.txt. Images, CSS, fonts, third-party scripts and other background requests are blocked before they leave the machine.
- The page's automatic "show all bids" search on load is blocked too, so each keyword costs exactly one request per results page.
- Results are read from the JSON response, not the screen. The parser accepts both plain values and one-item arrays and stops (instead of guessing) if the shape changes.
- The browser's temporary cookies are discarded after each search.
- GeM's search matches loosely: "office chair" also returns photo paper and manpower services. So a bid counts only when one of its items contains every word of one of your products (a plural "s" is ignored, and items naming an ignored word don't count). Downloads go to those bids, either the ones made mostly of your products or the ones closing soonest, as chosen on the page. Every other bid is still listed, marked "Not analysed" with the reason, and a product you add later can still pick it up.
- Most bids (31 of 35 seen) are judged on total value, so the seller must quote every item. When only some items are his, the verdict says how many.

**The listing response** was captured live on 11-Oct-2026 and is pinned as a test fixture (`tests/fixtures/all-bids-data_2026-10-11.json`): every field arrives as a one-element list, as the parser expected. Each search still saves the last response to `<data folder>/recon/last-listing-response.json`; if GeM changes the shape, the search stops with `ListingFormatChanged` instead of guessing.

## How the corrigendum check works

A corrigendum is an amendment the buyer publishes after the bid goes up. It can move the
deadline, change the quantity or withdraw a clause, so a bid reported without one checked
is a bid reported on possibly stale facts.

After a search has downloaded and analysed its bids, the tool opens `/all-bids` once more
and calls the page's own corrigendum handler for each bid it just analysed — one request
per bid, so only bids that were actually analysed cost anything, never every bid that was
merely listed. The request has to go through the page because it carries a CSRF token
only the rendered page has.

GeM answers `0` for "no corrigendum", or an HTML fragment. **The fragment is shown to you
as plain text and is never parsed.** Nobody has yet captured a real one
(`docs/02-RECON-FINDINGS.md` §4), so guessing where a revised deadline sits inside it
would risk exactly the silent wrong answer this tool exists to avoid. The bid's own fields
keep showing what the original PDF said, and the corrigendum sits above them so you can
see both.

A bid that was never checked says so rather than staying quiet — including every PDF you
import by hand, since those have no GeM document id to check against.

## How the corpus command works

The parser was written against one real bid PDF, and new field labels may only come from
real documents (`../docs/02-RECON-FINDINGS.md`). `corpus` collects them:

- It is a discovery run with trigger `corpus`, so it shares the same guard rails: one run
  at a time, 15 minutes between runs, at most 5 keywords × 2 pages, the safety pre-check,
  and the same handling of stops.
- It downloads up to **30** bid documents (normal runs: 5), chosen for variety: it rotates
  through the keywords and, within each one, prefers ministries it hasn't picked yet. 30 is
  a constant in `services/corpus_service.py`; no setting can raise it.
- It refuses to start unless today's remaining request budget covers the worst case, so it
  never stops half-way for budget reasons.
- Output goes to `<data folder>/corpus/<YYYYMMDD-HHMM>/`: `pdf/<doc_id>.pdf`, `manifest.json` (one
  row per bid: ministry, keyword, fields not found, links inside the PDF, corrigendum
  status), `listing-response.json` (the real search response) and `corrigenda/` (raw
  fragments, if any). The data folder is outside the repo: bid PDFs contain consignee
  names and phone numbers.

`probe` is separate and runs afterwards. GeM puts the buyer's uploaded terms file and the
specification document in the PDF as links, not text. `probe` reads robots.txt for each
host first, then requests at most one of each, exactly as linked. If a file needs a login,
GeM answers with a redirect, which trips the breaker by design. That's why it runs after
the corpus is safely on disk. Reset the breaker on the Safety page after checking GeM in a
normal browser.

## Layout

```
frontend/                 plain HTML/CSS/JS, no build step
  js/api.js               the only file that makes HTTP calls
  js/router.js            hash router; register a route to add a page
  js/views/               one file per page
backend/src/gem_assistant/
  domain/models.py        data + invariants (FOUND requires evidence, etc.)
  domain/ports.py         interfaces the core depends on
  safety/                 one guard per file; safe_fetcher.py composes them
  infrastructure/browser_listing.py   headless search of the public listing
  services/discovery_service.py       one search run: search → store → download → analyse
  services/corpus_service.py          corpus collection + attachment probe (borrows discovery's guard rails)
  infrastructure/document_store.py    kept bid PDFs, named by content hash
  parsing/                field_rules.py, clause_patterns.py = what to look for
  parsing/corrigendum_parser.py       reads "0" (none) or the amendment, as plain text
  eligibility/rules.py    one class per eligibility check
  services/               use cases (import a PDF, list bids, profile, safety)
  infrastructure/         SQLite, httpx, pypdf, clock: adapters for the ports
  api/                    FastAPI routes + response schemas
  services/chat_service.py + chat_tools.py   the chat's tool loop and the tools it may call
  infrastructure/groq_chat.py         the only code that talks to Groq
  container.py            composition root: the only place that wires concrete classes
backend/tests/            283 tests, incl. four real bid PDFs (2025 + 2026), a real /all-bids-data response and the saved /all-bids page
(data lives in ~/Library/Application Support/GeM Assistant — see "Your data")
```

## How SOLID shows up

- **Single responsibility**: each safety guard, eligibility rule and repository does one job.
- **Open/closed**: add a field with a `FieldRule`, a warning pattern with a `ClausePattern`, a check with a new rule class, a page with `router.register()`. The engines don't change.
- **Liskov**: tests swap in `FakeClock` and `FakeTransport` for the real ones without changing any service.
- **Interface segregation**: `ProfileReader` and `ProfileWriter`, `BidReader` and `BidWriter` are separate ports.
- **Dependency inversion**: services depend on `domain/ports.py`; only `container.py` knows about SQLite, httpx or pypdf.

## Safety rules (enforced in `safety/`, covered by `tests/test_safe_fetcher.py` and `tests/test_listing.py`)

- No login, credentials or accounts anywhere. The browser's temporary cookies are discarded after each search.
- https on `*.gem.gov.in` only; robots.txt paths (`/resources/`, `/cgi-bin/`, `/bg_emd/`) are never fetched.
- One request at a time, at least 4 s apart, at most 250 per day (the env vars can only make these stricter).
- Nothing is sent to GeM until a contact email is set (on the page, or `GEM_CONTACT_EMAIL`).
- One request at a time and the 4 s gap hold across every running copy of the app (the
  request log is shared; an OS file lock in the data folder serialises requests).
- The local web app sends a strict Content-Security-Policy and refuses to be framed, so no
  website can trick a click on "Resume fetching".
- Searches at least 15 minutes apart; at most 5 keywords × 2 pages and 5 downloads per search
  (the one-off `corpus` command: at most 30 downloads and 10 corrigendum checks).
- POST only to the listing page's own search (`/all-bids-data`) and its own corrigendum view
  (`/bidding/bid/viewCorrigendum/<id>`), and only from the rendered listing page. The
  corrigendum path accepts an id and nothing deeper.
- Any 401/403/429, 5xx, redirect, CAPTCHA or block page trips the breaker. It stays tripped across restarts until a person resets it on the Safety page.
- No automatic retries, no proxies, no IP rotation.
- The web server binds to 127.0.0.1 and rejects other hosts and cross-site POSTs.
