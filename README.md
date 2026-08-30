# crawler.visualping.io

A crawler that logs into a site with HTTP Basic Auth, walks every
same-origin page and resource it can reach, and scans everything it finds
for passwords hidden in HTML, CSS/JS, HTTP headers/cookies, image EXIF
metadata, and arbitrary binary content. Results are browsable through a
small web UI: a live progress log, a results table, and a snapshot viewer
that jumps straight to where each password was found.

## Requirements

- Python 3.11+
- A Chromium install for Playwright (see Setup)
- The Tesseract OCR binary on `PATH` (used to read passwords drawn as
  image pixels -- e.g. a screenshot or scanned whiteboard -- rather than
  present as parseable text/metadata). Debian/Ubuntu:
  `sudo apt-get install tesseract-ocr`; macOS: `brew install tesseract`;
  Windows: install via [winget](https://github.com/UB-Mannheim/tesseract)
  (`winget install --id UB-Mannheim.TesseractOCR`) or
  [choco](https://community.chocolatey.org/packages/tesseract)
  (`choco install tesseract`), then ensure the install directory is on
  `PATH`. If it's missing, `ImageOcrExtractor` degrades to finding no
  matches rather than failing the crawl.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # or .venv\Scripts\activate on Windows

pip install -e ".[dev]"          # runtime + dev tooling (pytest, ruff, black, mypy)
playwright install chromium      # one-time browser download
```

## Running the app

```bash
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/** in a browser. `app.main` is the real
entrypoint (not `app.api.routes` directly) -- it's what wires the
WebSocket route onto the shared FastAPI app before the server starts.

### Using the UI

1. Fill in the target URL and the site's Basic Auth username/password.
2. Click **Run**. The button stays disabled and a live log streams one
   line per page/resource fetched and per password found, over the
   `/ws/crawls/{id}` WebSocket.
3. When the crawl finishes, a summary panel (pages visited, resources
   checked, unique passwords found, queue empty) and a results table
   (page, source type, password, context, count) appear.
4. Click any password in the table to open a snapshot viewer: for
   text-based sources it shows the raw page/file with the match
   highlighted and scrolled into view; for image EXIF metadata or binary
   content, it shows the locator (field name / byte offset) and context
   instead, since there's no sensible page to scroll to.

The REST API can also be driven directly -- see `GET /docs` (FastAPI's
generated Swagger UI) once the server is running for the full schema of
`POST /crawls`, `GET /crawls/{id}/status`, `GET /crawls/{id}/report`, and
`GET /crawls/{id}/snapshot`.

## Running tests

```bash
pytest                              # full suite
pytest --cov=app --cov-report=term-missing   # with coverage
```

Most tests are pure unit tests, but a few spin up real local
infrastructure to prove the pipeline actually works end-to-end, not just
in isolation:

- `tests/test_browser_fetcher.py`, `tests/test_ui.py` -- a real local
  `http.server`/`uvicorn` server plus a real Playwright Chromium browser.
- `tests/test_e2e_crawl.py` -- a real `Orchestrator` (real HTTP client,
  real browser, real extractors, real SQLite repository) crawling a real
  local fixture site over Basic Auth, asserting it finds exactly the
  passwords planted there and nothing else.

These need the `playwright install chromium` step from Setup to have been
run at least once.

## Linting & type-checking

```bash
ruff check app tests
black --check app tests
mypy app
```

All three, plus `pytest --cov=app`, run in CI on every push to `main` and
every pull request into `main` or `staging` (see
`.github/workflows/ci.yml`).

## Project structure

```
app/
  main.py               ASGI entrypoint (uvicorn app.main:app)
  settings.py           env-based Settings (TARGET_URL/AUTH_*/etc.) for a
                         future non-API entry point -- the REST API/UI
                         below take target + credentials per request instead
  models.py             PasswordMatch, PageResult, CrawlSummary, SourceType
  matching.py           find_passwords(): the VISUALPING{16 hex} regex +
                         context-window extraction every extractor calls
  events.py             EventBus (Observer pattern) -- crawl progress events

  crawler/
    frontier.py          UrlFrontier: queue + visited-set, same-origin,
                          cycle-safe
    fetcher.py           HttpFetcher: httpx + Basic Auth, retry/backoff
    browser_fetcher.py   BrowserFetcher: Playwright, for JS-rendered links
    orchestrator.py      Orchestrator: wires everything into one crawl loop

  extractors/
    base.py              Extractor Protocol + ExtractorRegistry
    html.py              HtmlExtractor: visible text + <!-- comments -->
    css_js.py            CssJsExtractor: CSS/JS file bodies
    headers_cookies.py   HeaderCookieExtractor: response headers + cookies
    image_exif.py        ImageExifExtractor: EXIF fields (e.g. UserComment)
    binary_fallback.py   BinaryFallbackExtractor: catch-all for everything
                          else, safe on arbitrary non-UTF8 bytes

  storage/
    repository.py        Repository ABC
    sqlite.py            SqliteRepository: one *.db file per crawl

  api/
    routes.py            FastAPI app + REST endpoints, serves app/static/
    websocket.py         /ws/crawls/{id} live progress endpoint

  static/
    index.html           The web UI (plain HTML/CSS/vanilla JS)

tests/                    Unit, integration, and end-to-end tests
tests/fixtures/           Shared fixtures, one sample per source type
docs/DATA_FLOW_REPORT.md  Issue-by-issue record of how data moves through
                          the system -- see below
.github/workflows/ci.yml  CI: lint, format check, type-check, test
```

## Architecture

Four design patterns hold the pipeline together. Each exists to solve a
specific problem this project actually has, not for its own sake:

- **Strategy + Registry** (`extractors/base.py`). Eight different source
  types (HTML text/comments, CSS, JS, HTTP headers, cookies, EXIF
  metadata, arbitrary binary) each need their own scanning logic, but the
  orchestrator shouldn't need to know which extractors exist or how many
  there are. Every body-content extractor implements the same
  `Extractor` interface (`extract(content, content_type, url) ->
  list[PasswordMatch]`); `ExtractorRegistry.run_all()` just calls every
  registered one and merges the results. Adding a ninth source type later
  means writing one class and one `registry.register(...)` call --
  nothing else changes. (`HeaderCookieExtractor` is the one exception: its
  natural input is a headers/cookies dict, not a body blob, so it isn't
  registered in the same registry -- documented at the point in
  `orchestrator.py` where it's called directly instead.)

- **Repository** (`storage/repository.py` / `storage/sqlite.py`). The
  crawler, the extractors, and the API layer all need to read and write
  crawl state (pages, matches, snapshots), but none of them should need to
  know it's SQLite specifically. `Repository` is the abstract interface;
  `SqliteRepository` is the only implementation today. This is what makes
  the orchestrator and the extractors trivially testable with an
  in-memory database, and it's the seam a future alternative backend
  (e.g. Postgres for a multi-worker deployment) would slot into without
  touching crawl logic.

- **Observer** (`events.py`). The orchestrator produces progress
  (`page_fetched`, `match_found`, `crawl_finished`) that the WebSocket
  layer needs to relay to a browser -- but the orchestrator has no
  business knowing FastAPI or WebSockets exist. `EventBus` is a plain
  `subscribe()`/`publish()` pub/sub the orchestrator publishes into
  unconditionally; nothing breaks if there are zero subscribers (e.g. in
  most unit tests) or if a future subscriber wants the same events for
  something else entirely (metrics, a second UI, a webhook).

- **Background task + polling/streaming split** (`api/routes.py` /
  `api/websocket.py`). A crawl can take a while, so `POST /crawls` can't
  block until it's done -- it returns a `crawl_id` immediately and runs
  the crawl via FastAPI's `BackgroundTasks`. Two different consumption
  models sit on top of the same `EventBus`/`Repository` state: `GET
  /crawls/{id}/status` and `.../report` for simple polling, and `/ws/
  crawls/{id}` for a live stream -- callers pick whichever fits.

See `docs/DATA_FLOW_REPORT.md` for the fine-grained, issue-by-issue record
of exactly how data (especially credentials and extracted passwords)
moves through every one of these pieces -- it's the primary reference for
"where does this value go and who can see it," reviewed end-to-end for
consistency as part of this issue.

## Security considerations

This tool authenticates to a target site and persists whatever secrets it
finds, so a few things are true by design and worth being deliberate
about:

- **No auth of its own.** The REST/WebSocket API and the UI have no login
  or rate-limiting. Anyone who can reach the port this runs on can start
  crawls (supplying their own target credentials) and read back whatever
  any crawl found. Run this only on a trusted, operator-controlled network
  -- never expose it directly to the internet.
- **Everything persists to a local SQLite file.** Each crawl gets its own
  `crawl_<uuid>.db` in the working directory (gitignored, never
  committed), containing every extracted password, its context, and a raw
  snapshot of every page/resource fetched (which can contain secrets
  beyond the one matched). These files are never cleaned up automatically
  -- delete them yourself once you're done with a crawl's results.
- **Credentials are never logged, persisted standalone, or echoed back.**
  They exist only in memory for the duration of a crawl (as request
  parameters, then as `httpx`/Playwright auth objects) and in the
  Authorization header sent to the target site itself.

## Implemented so far

A running one-line-per-issue list for work landing after the original
28-issue backlog (see `docs/DATA_FLOW_REPORT.md` for the full history and
per-issue detail; this list starts fresh from here rather than
backfilling #1-28/#61/#63, which that report already covers in full).

- #68: Pause, stop, and resume controls alongside Run, in the UI and via
  three new `POST /crawls/{id}/{pause,resume,stop}` endpoints.
- #69: Results table now populates live from `match_found` WebSocket
  events as the crawl runs, instead of waiting for it to finish.
- #70: Verified (no code change) that the same password on two distinct
  pages already lists as two separate rows, not collapsed into one.
- #71: `max_pages`/new `max_duration_seconds` now default to `None` --
  a crawl runs until its frontier actually empties, not a guessed cap.
- #72: New `POST /crawls/{id}/re-extract` re-runs extraction against a
  crawl's already-stored pages -- no live re-fetch needed after tuning
  an extractor.
- #78: `PaginationGuard` now stops a pagination family on a lack of new
  *matches* (not just links), plus an always-on hard per-family page cap
  -- defeats an adversarial family that fakes novelty forever.
- #80: Each fetched-page log entry is now a real `<a href>` opening the
  page in a new tab, so you can browse exactly what the crawl visited.
- #86: A search box above the results table filters rows client-side by
  a case-insensitive substring match against the page URL.
- #91: That search box now always stays visible above the results list,
  regardless of whether any matches exist yet.
- #87: Re-verified the cache/replay path (#72); found and fixed #93 in
  the process (`JsCharCodeExtractor` was used but never imported,
  breaking every real crawl and `/re-extract` call).
- #96: Removed the "Context length" field -- no longer user-configurable,
  fixed internally at its old default (80 characters).
