# GeM Assistant

A local, read-only tool that analyses GeM bid documents against your seller
profile and shows evidence (page and exact quote) for every value. Design
rationale lives in `../docs/00-RESEARCH-AND-ARCHITECTURE.md`.

## Run it

```bash
cd backend
uv sync                                  # Python deps into backend/.venv
uv run playwright install chromium       # one-time: headless browser for bid search
export GEM_CONTACT_EMAIL=you@example.com # goes in the User-Agent so GeM can reach you
uv run gem-assistant serve               # open http://127.0.0.1:8000
```

Command-line equivalents:

```bash
uv run gem-assistant import path/to/bid.pdf     # analyse a PDF you already have (no network)
uv run gem-assistant fetch https://bidplus.gem.gov.in/showbidDocument/8506455
uv run gem-assistant discover                   # one search of the public listing
```

Run the tests:

```bash
uv run pytest
```

### If `gem-assistant` says `No module named 'gem_assistant'`

Something on this Mac sets the macOS *hidden* flag on files inside `.venv`, and Python
3.12 silently ignores hidden `.pth` files — including the one that puts `src/` on the
path. `uv run pytest` keeps working (it has its own `pythonpath`), so only the command
line breaks. Clear the flag:

```bash
chflags -R nohidden .venv
```

Check with `ls -lO .venv/lib/python3.12/site-packages/*.pth` — the flags column reads
`hidden` when this has happened. It is not a fault in the project or in uv, and nothing
needs reinstalling.

## What exists so far

| Area | Status |
|---|---|
| Frontend: bids, bid detail with evidence, find bids, seller profile, safety | done |
| Local PDF import → field extraction → clause flags → eligibility verdict | done |
| Safety layer (`SafeFetcher`): URL policy, rate limit, daily cap, circuit breaker, request log | done; every GeM request goes through it |
| Add a bid by its `showbidDocument` link | done; 1 request, never repeated for the same document |
| Search the public `/all-bids` listing (headless browser) | done; tested offline against the saved page, **not yet run live** |
| Corrigendum check for the bids it analyses | done; shown verbatim, never parsed into a new deadline |
| LLM reading of free-text ATC, ATC attachments | later |

## How bid search works

`infrastructure/browser_listing.py` opens `https://bidplus.gem.gov.in/all-bids` in headless Chromium:

- Only two kinds of request reach GeM: the page itself, and the page's own search request (`POST /all-bids-data`, made by GeM's JavaScript when we trigger its search). Both go through `SafeFetcher`.
- The page's scripts are served from `browser_assets/` (jQuery 3.6.0 + no-op stubs). `/resources/` is never fetched, which matches robots.txt. Images, CSS, fonts, third-party scripts and other background requests are blocked before they leave the machine.
- The page's automatic "show all bids" search on load is blocked too, so each keyword costs exactly one request per results page.
- Results are read from the JSON response, not the screen. The parser accepts both plain values and one-item arrays and stops (instead of guessing) if the shape changes.
- The browser's temporary cookies are discarded after each search.

**About the first live search:** the JSON shape in the tests is reconstructed from the page's JavaScript; it hasn't been captured from GeM yet. The first real search saves the response to `data/recon/last-listing-response.json`. Check it and turn it into a test fixture. If GeM's session handling works differently from what the tests assume, the first search may stop with the breaker tripped. That's the intended safe failure: check GeM in a normal browser, then reset on the Safety page.

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
  parsing/                field_rules.py, clause_patterns.py = what to look for
  parsing/corrigendum_parser.py       reads "0" (none) or the amendment, as plain text
  eligibility/rules.py    one class per eligibility check
  services/               use cases (import a PDF, list bids, profile, safety)
  infrastructure/         SQLite, httpx, pypdf, clock: adapters for the ports
  api/                    FastAPI routes + response schemas
  container.py            composition root: the only place that wires concrete classes
backend/tests/            168 tests, incl. the real bid PDF and the saved /all-bids page
data/                     SQLite database (gitignored, owner-only permissions)
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
- Searches at least 15 minutes apart; at most 5 keywords × 2 pages and 5 downloads per search.
- POST only to the listing page's own search (`/all-bids-data`) and its own corrigendum view
  (`/bidding/bid/viewCorrigendum/<id>`), and only from the rendered listing page. The
  corrigendum path accepts an id and nothing deeper.
- Any 401/403/429, 5xx, redirect, CAPTCHA or block page trips the breaker. It stays tripped across restarts until a person resets it on the Safety page.
- No automatic retries, no proxies, no IP rotation.
- The web server binds to 127.0.0.1 and rejects other hosts and cross-site POSTs.
