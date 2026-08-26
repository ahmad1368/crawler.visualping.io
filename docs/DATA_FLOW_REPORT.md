# Data Flow Report

This document tracks how data moves through the system, issue by issue. Each
issue that touches the crawl, extraction, storage, or API layers appends its
own section below describing: inputs -> transformation -> outputs, with a
focus on where credentials or extracted secrets travel.

## Data flow tree (overview)

Updated as each issue lands. Nodes marked `(planned)` don't exist in code
yet -- they show where today's outputs are headed.

```
Browser: GET /                                app/api/routes.py + app/static/index.html
(operator fills in url/username/password/context_chars in an HTML form)
│
└── JS: fetch POST /crawls, then WebSocket /ws/crawls/{id}
    app/static/index.html -- credentials leave the browser only in the
    POST body (JSON over the page's own origin); the Run button is
    disabled from click until a crawl_finished message arrives

POST /crawls (CrawlRequest: url, username, password, context_chars)
app/api/routes.py -- validated by pydantic (HttpUrl, non-empty credentials);
returns {crawl_id} immediately, runs the crawl in a FastAPI BackgroundTask.
(`Settings`, app/settings.py issue #2, still exists for a future non-API
entry point, but the REST API takes url/credentials per request instead --
it does not read TARGET_URL/AUTH_USERNAME/AUTH_PASSWORD from the environment)
│
└── _build_orchestrator(request)                app/api/routes.py
    (constructs a fresh httpx.AsyncClient + Playwright browser +
     ExtractorRegistry + HeaderCookieExtractor + a per-crawl
     SqliteRepository -- the single seam integration tests replace with a
     fake Orchestrator, per issue #17's acceptance criteria)
    │
    └── Orchestrator.run()                      app/crawler/orchestrator.py
        (asyncio.Semaphore(concurrency)-bounded workers pop from the
         frontier until max_pages resources are checked or it's empty)
        │
        ├── UrlFrontier                          app/crawler/frontier.py
        │   (queue + visited-set seeded from the request URL; normalizes
        │    URLs, same-origin filter, dedupe prevents cyclic-link loops)
        │
        └── per URL popped: HttpFetcher.fetch(url)   app/crawler/fetcher.py
            (httpx.AsyncClient + Basic Auth, retry/backoff)
            └── FetchResult (content, content_type, status_code, headers, cookies)
                │
                ├── ExtractorRegistry.run_all(content, content_type, url)
                │   app/extractors/base.py -- dispatches to HtmlExtractor,
                │   CssJsExtractor, ImageExifExtractor, BinaryFallbackExtractor
                │   (each calls find_passwords() from app/matching.py)
                │
                ├── HeaderCookieExtractor.extract(headers, cookies, url)
                │   app/extractors/headers_cookies.py (not routed through
                │   ExtractorRegistry -- different input shape, see issue #11)
                │
                ├── if content_type is text/html:
                │   BrowserFetcher.fetch(url)     app/crawler/browser_fetcher.py
                │   └── BrowserFetchResult (dom_links, network_urls)
                │       └── UrlFrontier.add_many(...) -- feeds discovered
                │           links back into the queue (same-origin + dedup
                │           keeps the crawl bounded)
                │
                └── PageResult(url, status_code, fetched_at, matches)
                    app/models.py
                    │
                    ├── Repository.save_page(page, snapshot=content)
                    │   app/storage/ (SqliteRepository -- one *.db file per
                    │   crawl_id, durable, gitignored)
                    │   │
                    │   ├── get_snapshot(url)     raw bytes back out, for
                    │   │                         the (planned) UI's "jump
                    │   │                         to location" viewer
                    │   │
                    │   └── Orchestrator.run()'s return value -> CrawlSummary
                    │       for just this run, held in memory as
                    │       _crawls[crawl_id].report
                    │       │
                    │       └── GET /crawls/{id}/report   app/api/routes.py
                    │           (returns the in-memory report once
                    │           finished; 409 while running, 500 if the
                    │           crawl failed -- NOT the same as
                    │           Repository.get_report(), a separate,
                    │           DB-wide aggregate this endpoint doesn't use)
                    │
                    └── EventBus.publish(PAGE_FETCHED, page) and, per match,
                        publish(MATCH_FOUND, match)          app/events.py
                        (wired into Orchestrator in issue #18; one EventBus
                         per crawl, held on _CrawlState.event_bus)
                        └── WebSocket /ws/crawls/{id}        app/api/websocket.py
                            (subscribes to the crawl's EventBus, forwards
                             each event as JSON over the socket in order;
                             closes the socket on CRAWL_FINISHED, or
                             immediately with a synthesized CRAWL_FINISHED
                             message if the crawl already finished before
                             the client connected; unknown crawl_id closes
                             with code 4404)

GET /crawls/{id}/status -> in-memory _crawls[id].status (running/finished/failed)
```

Sensitive edges to keep an eye on: `Settings` → both fetchers (Basic Auth
credentials leave the process for the first time); `Extractors` →
`PasswordMatch` (the extracted secret + context is created); and
`PasswordMatch` → `Storage`/`API` (whatever persists or serializes it
becomes the durable/exposed copy of that secret).

## Issue #1: scaffold project structure & tooling

- **Inputs:** none (infra-only issue, no runtime data flow).
- **Transformation:** created the empty package skeleton (`app/crawler`,
  `app/extractors`, `app/storage`, `app/api`, `app/models.py`), pinned base
  dependencies, and set up `.gitignore` to exclude `.env`, `__pycache__/`,
  `.venv/`, `*.db`, and crawl snapshot output directories from version
  control.
- **Outputs:** none. No code here handles credentials, extracted matches, or
  network I/O yet.

## Issue #2: environment & secrets configuration

- **Inputs:** environment variables / a local `.env` file: `TARGET_URL`,
  `AUTH_USERNAME`, `AUTH_PASSWORD` (required, no defaults), `CONTEXT_CHARS`,
  `CONCURRENCY`, `MAX_PAGES` (optional, sane defaults).
- **Transformation:** `app/settings.py` defines a `pydantic-settings`
  `Settings` class that loads and validates these values at process start.
  Missing required vars raise a `ValidationError` immediately rather than
  the app running with blank credentials. `.env.example` documents the
  expected keys with placeholder values only.
- **Outputs:** an in-memory `Settings` instance held by the process,
  including the plaintext Basic Auth credentials (`auth_username`,
  `auth_password`) for the target site. This is the point where those
  credentials first enter the system. Nothing here logs, persists, or
  serializes `Settings` — the real `.env` stays local and gitignored, and
  `.env.example` never contains real values.

## Issue #3: core data models (PasswordMatch, PageResult, CrawlSummary)

- **Inputs:** none directly -- this issue defines the pydantic schemas that
  later crawler/extractor/storage/API issues will construct and pass
  around; no runtime data flows through it yet.
- **Transformation:** `app/models.py` now defines `SourceType` (the enum of
  password-hiding techniques extractors can find: html, css, js,
  http_header, cookie, image_metadata, binary), `PasswordMatch` (the
  extracted secret plus its context and locator), `PageResult` (one
  crawled page and its matches), and `CrawlSummary` (aggregate crawl
  stats). Required fields are validated at construction time.
- **Outputs:** none yet -- these are shapes, not a live data path. Flagging
  ahead: `PasswordMatch.value`, `context_before`, and `context_after` are
  the plaintext secret and its surrounding text. Every later issue that
  constructs, stores, or serializes a `PasswordMatch` must be checked
  against how far that value travels (DB row, log line, API/WebSocket
  payload) per the project's data-flow watchlist.

## Issue #4: HTTP fetcher with Basic Auth (httpx)

- **Inputs:** a target URL, plus the operator's Basic Auth credentials
  (`Settings.auth_username` / `auth_password` from issue #2) supplied to
  `HttpFetcher`'s constructor, and an injected `httpx.AsyncClient`.
- **Transformation:** `app/crawler/fetcher.py`'s `HttpFetcher.fetch()`
  builds an `httpx.BasicAuth` from the credentials and attaches it to every
  request it sends. Transient failures (5xx responses, timeouts, transport
  errors) are retried with exponential backoff up to a configurable
  `max_retries`; non-5xx responses and exhausted retries return/raise
  immediately.
- **Outputs:** a `FetchResult` (raw `bytes`, `content_type`, `status_code`,
  response `headers`, `cookies`) returned to the caller in memory only.
  **Data-flow note:** the Authorization header carrying the Basic Auth
  credentials is attached to outgoing requests but is never logged, and
  `FetchResult` does not echo the request headers back -- only the
  *response* headers/cookies from the target site, which themselves could
  contain secrets and should be treated as sensitive once extractors (later
  issues) start scanning them.

## Issue #5: browser-based fetcher (Playwright) with network capture

- **Inputs:** a target URL and the operator's Basic Auth credentials
  (`username`/`password`) supplied to `BrowserFetcher`'s constructor, plus
  an injected Playwright `Browser` instance.
- **Transformation:** `app/crawler/browser_fetcher.py`'s `BrowserFetcher`
  opens a new browser context with `http_credentials` set (so Basic Auth is
  applied automatically to every request Chromium makes), navigates to the
  URL, and listens for `request`/`response` events for the lifetime of the
  page load. It collects rendered-DOM `<a href>` links via
  `eval_on_selector_all` and all URLs observed in network traffic
  (including JS-driven `fetch`/XHR calls a raw-HTML parser would miss).
- **Outputs:** a `BrowserFetchResult` (`html`, `dom_links`, `network_urls`)
  returned to the caller in memory only. **Data-flow note:** the Basic Auth
  credentials are passed to Playwright's `http_credentials` context option
  and are not logged, echoed into `BrowserFetchResult`, or written to disk.
  `html` and `network_urls` are the rendered page content and every URL
  Chromium touched -- both should be treated as sensitive input once
  extractors (later issues) start scanning them for exposed passwords.

## Issue #6: URL frontier (queue + visited-set, same-origin filter)

- **Inputs:** a seed URL (the crawl's `TARGET_URL`), plus links discovered
  later from `FetchResult`/`BrowserFetchResult` (e.g. `dom_links`,
  `network_urls`).
- **Transformation:** `app/crawler/frontier.py`'s `UrlFrontier` normalizes
  each URL (strips fragments, collapses trailing-slash variants, lowercases
  scheme/host) before checking it against a `seen` set, so trivial variants
  of the same URL dedupe together. `add()` also rejects any URL outside the
  seed's origin (scheme + host). Because a normalized URL is only ever
  added to the queue once, feeding the same link back in repeatedly (a
  cyclic link) is a no-op after the first time -- this is what keeps the
  crawl from looping forever.
- **Outputs:** a FIFO queue of same-origin, deduped URLs, held in memory
  only. No credentials or extracted content pass through this component --
  it carries URLs, not page content or secrets.

## Issue #7: password regex matcher + context extractor

- **Inputs:** a text blob (page/resource content that an extractor has
  pulled out of a fetch result, in later issues) plus `before`/`after`
  context-length parameters (sourced from `Settings.context_chars`).
- **Transformation:** `app/matching.py`'s `find_passwords()` scans the
  content with `PASSWORD_PATTERN` (`VISUALPING\{[0-9a-f]{16}\}` -- exactly
  16 lowercase hex chars between literal braces; wrong length, uppercase,
  or malformed braces all fail to match) and, for every match, slices up to
  `before`/`after` characters of surrounding text, clamped to the string's
  bounds so a match at the very start or end never underflows/overflows.
- **Outputs:** a list of in-memory `RegexMatch` objects (`value`,
  `context_before`, `context_after`, `start`, `end`). **Data-flow note:**
  this is the point where the plaintext secret and its surrounding text are
  first extracted from raw content. `RegexMatch` isn't logged, persisted,
  or returned by any API here -- it's a pure function's return value.
  Whichever later issue turns a `RegexMatch` into a `PasswordMatch` (adding
  `source_type`/`source_url`/`locator`) inherits responsibility for
  everything downstream of that per the project's data-flow watchlist.

## Issue #8: Extractor strategy interface + registry

- **Inputs:** none directly -- this issue defines the interface concrete
  extractors (later issues) will implement, plus the registry that will
  invoke them; no runtime data flows through it yet.
- **Transformation:** `app/extractors/base.py` defines `Extractor`, a
  runtime-checkable `Protocol` with a single method,
  `extract(content: bytes, content_type: str, url: str) -> list[PasswordMatch]`,
  and `ExtractorRegistry`, which holds registered extractors (`register()`)
  and runs all of them against one fetched response, aggregating their
  results (`run_all()`).
- **Outputs:** none yet -- no concrete extractor exists to construct a real
  `PasswordMatch`. **Data-flow note:** once concrete extractors land (issues
  #9-13), every one of them returns `PasswordMatch` objects through this
  same registry, so `ExtractorRegistry.run_all()` becomes the single
  chokepoint where every extracted secret in the system passes through --
  worth keeping an eye on if logging/instrumentation is ever added here.

## Issue #9: HTML visible text & comments extractor

- **Inputs:** a fetched response's `content` (`bytes`), `content_type`, and
  `url` (the `Extractor.extract()` signature from issue #8).
- **Transformation:** `app/extractors/html.py`'s `HtmlExtractor` only acts
  on `text/html` content. It walks the markup with the stdlib
  `html.parser.HTMLParser`, collecting visible text-node data and
  `<!-- -->` comment data separately (content inside `<script>`/`<style>`
  is skipped -- that's the CSS/JS extractor's job). Each chunk is passed
  through `find_passwords()` from issue #7, and every match is wrapped into
  a `PasswordMatch` tagged `SourceType.HTML_TEXT` or `SourceType.HTML_COMMENT`
  depending on where it was found, with `locator` set to the chunk's
  `line:col` position from the parser. (`SourceType.HTML` from issue #3 was
  split into these two more specific values to match this issue's
  acceptance criteria.)
- **Outputs:** a `list[PasswordMatch]` returned to the caller in memory
  only. **Data-flow note:** this is the first concrete extractor -- it's
  the first place a real `PasswordMatch` (plaintext secret + context) gets
  constructed from live crawl content. Nothing here logs, persists, or
  transmits it; it's returned up the call stack for a later issue's
  storage/API layer to handle per the data-flow watchlist.

## Issue #10: CSS & JS file content extractor

- **Inputs:** a fetched response's `content` (`bytes`), `content_type`, and
  `url` (same `Extractor.extract()` signature).
- **Transformation:** `app/extractors/css_js.py`'s `CssJsExtractor` maps
  `content_type` to `SourceType.CSS` (`text/css`) or `SourceType.JS`
  (`application/javascript`, `text/javascript`, `application/x-javascript`;
  any other content type is skipped). Unlike the HTML extractor, there's no
  DOM to walk -- a password can be hidden anywhere (a `content:` property,
  a string literal, a `//`/`/* */` comment) -- so the whole decoded body is
  scanned as plain text through `find_passwords()`. `locator` is a
  `line:col` position computed by counting newlines up to the match offset.
- **Outputs:** a `list[PasswordMatch]` returned to the caller in memory
  only, tagged `CSS` or `JS`. **Data-flow note:** same as issue #9 -- this
  constructs real `PasswordMatch` objects from live crawl content; nothing
  here logs, persists, or transmits them.

## Issue #11: HTTP response headers & cookies extractor

- **Inputs:** a fetched response's `headers` and `cookies`
  (`dict[str, str]`, as already carried on `FetchResult` from issue #4) and
  the page `url`. This extractor's natural input is name/value maps, not a
  body blob, so its `extract()` doesn't match the `Extractor` Protocol's
  `(content, content_type, url)` shape from issue #8 -- it isn't routed
  through `ExtractorRegistry`.
- **Transformation:** `app/extractors/headers_cookies.py`'s
  `HeaderCookieExtractor` runs `find_passwords()` (issue #7) against every
  header value (including custom `X-*` headers) and every cookie value,
  tagging matches `SourceType.HTTP_HEADER` or `SourceType.COOKIE` with a
  `header:<name>` / `cookie:<name>` locator.
- **Outputs:** a `list[PasswordMatch]` returned to the caller in memory
  only. **Data-flow note:** response headers/cookies were already flagged
  as potentially sensitive back in issue #4's report section -- this is
  where that risk becomes concrete: any secret an operator's server leaks
  in a header or `Set-Cookie` value now gets extracted the same as a
  body-content match. Nothing here logs, persists, or transmits it further.

## Issue #12: image EXIF metadata extractor

- **Inputs:** a fetched response's `content` (`bytes`), `content_type`, and
  `url` (the `Extractor.extract()` signature from issue #8). Only acts on
  `image/*` content types.
- **Transformation:** `app/extractors/image_exif.py`'s `ImageExifExtractor`
  opens the image bytes with Pillow and reads its EXIF tags (e.g.
  `UserComment`, `ImageDescription`). `UserComment` values carry an 8-byte
  charset prefix (`ASCII\x00\x00\x00`, `UNICODE\x00`, ...) per the EXIF
  spec, which is stripped before decoding. Each decoded tag value is passed
  through `find_passwords()`, and matches are tagged
  `SourceType.IMAGE_METADATA` with `locator` set to `exif:<field name>`.
  Unparseable image bytes (corrupt/non-image content) return no matches
  rather than raising.
- **Outputs:** a `list[PasswordMatch]` returned to the caller in memory
  only. **Data-flow note:** operators sometimes stash debug notes --
  including credentials -- in EXIF fields without realizing they ship with
  the image; this is another place a secret gets extracted from content
  that looks innocuous (a plain image) at a glance. Nothing here logs,
  persists, or transmits the match further. Adds `Pillow==11.0.0` as a new
  dependency.

## Issue #13: generic binary/string fallback scanner

- **Inputs:** a fetched response's `content` (`bytes`), `content_type`, and
  `url` (the `Extractor.extract()` signature from issue #8). Skips any
  content type already handled by a more specific extractor (`text/html`,
  `text/css`, the JS variants, `image/*`) to avoid duplicate matches when
  run alongside them via `ExtractorRegistry`.
- **Transformation:** `app/extractors/binary_fallback.py`'s
  `BinaryFallbackExtractor` decodes the raw bytes with `latin-1` -- a 1:1
  byte<->codepoint mapping that never raises, unlike `utf-8` -- so
  arbitrary binary content (images without a recognized type, unknown
  file types, etc.) never crashes the scan. The decoded text is passed
  through `find_passwords()` like a `strings` dump, with `locator` set to
  `offset:<byte offset>` of the match.
- **Outputs:** a `list[PasswordMatch]` returned to the caller in memory
  only, tagged `SourceType.BINARY`. **Data-flow note:** this is the last of
  the five extractor issues (#9-13); together they mean every
  `FetchResult`/`BrowserFetchResult` byte range and every response
  header/cookie now has a code path that can turn it into a `PasswordMatch`.
  Nothing in any of them logs, persists, or transmits a match -- they all
  return plain in-memory values up the call stack, which is where the
  still-`(planned)` storage/API layer picks up responsibility next.

## Issue #14: Repository interface + SQLite implementation

- **Inputs:** a `PageResult` plus its raw content `snapshot` (`bytes`), for
  `save_page()`; a standalone `PasswordMatch`, for `save_match()` (used
  when there's no full page/snapshot to attach it to -- e.g. a match from
  `HeaderCookieExtractor`, issue #11). `get_snapshot(url)` and `get_report()`
  take no crawl data as input; they read back what's already stored.
- **Transformation:** `app/storage/repository.py` defines the `Repository`
  ABC (`save_page`, `save_match`, `get_report`, `get_snapshot`).
  `app/storage/sqlite.py`'s `SqliteRepository` implements it against a
  `sqlite3.Connection`, creating its schema (`pages`, `matches`,
  `snapshots` tables) on first use if not already present. `save_page()`
  upserts the page row, upserts its raw content into `snapshots`, and
  inserts a `matches` row for every match in `page.matches` -- all in one
  transaction. `get_report()` aggregates `CrawlSummary` fields from stored
  rows (`pages_visited`/`resources_checked` from the page count,
  `unique_passwords_found` from `COUNT(DISTINCT value)`,
  `started_at`/`finished_at` from `MIN`/`MAX(fetched_at)`); `queue_empty`
  and the pages-vs-resources distinction are placeholders (`True` / same
  count) until the orchestrator (issue #15) tracks real frontier state.
- **Outputs:** durable rows in a SQLite `*.db` file (gitignored per issue
  #1). **Data-flow note:** this is the durable store the project's
  data-flow watchlist has been anticipating since issue #1 -- every
  `PasswordMatch.value`/`context_before`/`context_after` and every raw
  page/resource `snapshot` (which can contain secrets beyond the matched
  one) now persists to disk. Nothing here adds logging, telemetry, or any
  network call; the database is the only sink. This is exactly the file
  operators must keep secured and out of version control.

## Issue #15: async orchestrator wiring frontier + fetchers + extractors + repository

- **Inputs:** a seeded `UrlFrontier`, an `HttpFetcher`, a `BrowserFetcher`,
  an `ExtractorRegistry`, a `HeaderCookieExtractor`, and a `Repository` --
  all injected via the constructor (each already independently tested in
  issues #6, #4, #5, #8-13, #11, #14) -- plus `concurrency` and `max_pages`
  limits.
- **Transformation:** `app/crawler/orchestrator.py`'s `Orchestrator.run()`
  starts `concurrency` worker tasks bounded by an `asyncio.Semaphore`. Each
  worker pops a URL from the frontier, fetches it via `HttpFetcher`, runs
  `ExtractorRegistry.run_all()` and `HeaderCookieExtractor.extract()`
  against the result, and -- only for `text/html` responses -- also fetches
  it via `BrowserFetcher` and feeds `dom_links`/`network_urls` back into the
  frontier via `add_many()`. Every fetched URL becomes a `PageResult` saved
  through `Repository.save_page()`. The loop stops once `max_pages`
  resources have been checked or the frontier is empty, whichever comes
  first. `pages_visited` counts HTML responses specifically;
  `resources_checked` counts everything fetched; `unique_passwords_found`
  is deduped by match value across the whole run. **Scope note:** the
  frontier (issue #6) has no depth concept and `Settings` has no
  `MAX_DEPTH`, so only `max_pages` is enforced here -- depth-limiting was
  left out rather than adding an untested concept to the frontier under
  this issue.
- **Outputs:** a `CrawlSummary` for this run (in memory, returned to the
  caller) plus everything `Repository.save_page()` already persists per
  issue #14. **Data-flow note:** no new sink is introduced here -- this
  issue only wires together components that were each already reviewed for
  where credentials/matches/snapshots travel. The one new behavior worth
  flagging: `BrowserFetcher` (which also carries Basic Auth credentials via
  `http_credentials`) now runs automatically for every HTML page during a
  real crawl, not just in isolated tests.

## Issue #16: progress event bus (Observer pattern)

- **Inputs:** an `event_type` string and an arbitrary `payload`, passed to
  `publish()`; a handler function and `event_type`, passed to `subscribe()`.
- **Transformation:** `app/events.py`'s `EventBus` keeps a
  `dict[event_type, list[handler]]`. `subscribe()` appends a handler to
  that event type's list; `publish()` calls every subscribed handler for
  that event type, in subscription order, with the payload. Three event
  type constants are defined for the orchestrator's future use:
  `PAGE_FETCHED`, `MATCH_FOUND`, `CRAWL_FINISHED`. **Scope note:**
  `Orchestrator.run()` (issue #15) does not call `publish()` yet -- this
  issue only builds the bus itself, per its acceptance criteria
  (`subscribe()`/`publish()` API + an ordered-delivery test). Wiring it
  into the orchestrator is left for whichever issue actually needs to
  consume these events (the WebSocket endpoint, issue #18).
- **Outputs:** handler calls happen synchronously, in-process, in memory --
  no new storage or network sink. **Data-flow note:** once wired up, a
  `MATCH_FOUND` payload will carry a `PasswordMatch` (the plaintext secret
  + context) directly to every subscriber. That's fine for an in-process
  WebSocket broadcaster relaying to the local operator's own UI, but any
  future subscriber added to this bus should be checked against the
  data-flow watchlist the same as the storage/API layers are.

## Issue #17: REST endpoints (start crawl, get status, get report)

- **Inputs:** `POST /crawls` takes a `CrawlRequest` body -- `url`
  (pydantic `HttpUrl`, so a malformed URL is rejected with 422),
  `username`/`password` (`Field(min_length=1)`, so empty credentials are
  rejected), and `context_chars`. This is the first point Basic Auth
  credentials enter the system from an untrusted HTTP client rather than a
  local `.env` file.
- **Transformation:** `app/api/routes.py`'s `start_crawl()` generates a
  `crawl_id`, records `_CrawlState(status=RUNNING)` in an in-memory dict,
  and schedules `_run_crawl()` as a `BackgroundTasks` job -- the endpoint
  returns `{crawl_id}` immediately (202) without waiting for the crawl.
  `_run_crawl()` calls `_build_orchestrator()`, which wires a real
  `httpx.AsyncClient`, a real Playwright browser, the four registry
  extractors, `HeaderCookieExtractor`, and a **new `SqliteRepository` per
  crawl** (`crawl_<uuid>.db`) into an `Orchestrator`, then awaits
  `orchestrator.run()`. `_build_orchestrator()` is the single seam the
  integration tests replace with a fake orchestrator, per this issue's
  acceptance criteria -- no test launches a real browser or hits the
  network. `GET /crawls/{id}/status` and `GET /crawls/{id}/report` just
  read the in-memory `_CrawlState` (404 if unknown, 409 if still running,
  500 if it failed).
- **Outputs:** the `{crawl_id}` and status responses never echo the
  request's `username`/`password` back. The `CrawlSummary` report exposes
  aggregate counts only (`pages_visited`, `unique_passwords_found`, etc.) --
  not the `PasswordMatch` values themselves; those stay in the per-crawl
  SQLite file. **Data-flow notes:** (1) credentials now arrive over HTTP
  from whoever can reach this API, not just from a trusted local `.env` --
  this API has no auth/rate-limiting of its own, so it should only be
  exposed to trusted operators/networks. (2) Every crawl now creates its
  own `crawl_<uuid>.db` file on disk (matched by the existing `*.db`
  gitignore pattern) that is never cleaned up by this issue -- an operator
  running many crawls will accumulate one durable snapshot/match database
  per crawl indefinitely; worth a retention/cleanup policy in a later issue.

## Issue #18: WebSocket endpoint for live progress

- **Inputs:** a `crawl_id` path parameter on `/ws/crawls/{crawl_id}`; no
  request body. Consumes `PAGE_FETCHED`/`MATCH_FOUND`/`CRAWL_FINISHED`
  events published to that crawl's `EventBus` (`_CrawlState.event_bus`,
  new field this issue adds).
- **Transformation:** `Orchestrator` (issue #15) now takes an optional
  `event_bus` -- `_build_orchestrator()` (issue #17) passes each crawl's
  `EventBus` into it. During `_process_url()`, the orchestrator publishes
  `PAGE_FETCHED` with the `PageResult` and `MATCH_FOUND` with each
  `PasswordMatch` right after `Repository.save_page()`; `run()` publishes
  `CRAWL_FINISHED` with the final `CrawlSummary` as its last action. If
  `_run_crawl()`'s try block raises before `orchestrator.run()` can do
  that, it publishes `CRAWL_FINISHED` with a `None` payload itself in the
  `except` block, so a connected socket is never left waiting forever.
  `app/api/websocket.py`'s `crawl_progress_websocket()` accepts the
  connection, checks `crawl_id` (closes with code 4404 if unknown), checks
  whether the crawl already finished (sends a synthesized `CRAWL_FINISHED`
  message immediately if so -- there's no `await` between that check and
  subscribing, so no event can be missed in between), then subscribes to
  all three event types with handlers that `queue.put_nowait()` onto a
  local `asyncio.Queue`, and a loop that dequeues and `send_json()`s each
  message in order until it sees `CRAWL_FINISHED`, then closes.
- **Outputs:** JSON messages `{"type": ..., "payload": ...}` sent directly
  to the connected client -- `PasswordMatch`/`PageResult`/`CrawlSummary`
  payloads are serialized via `model_dump(mode="json")`. **Data-flow
  note:** a `match_found` message puts the plaintext secret + context on
  the wire to whoever holds this socket, same as the REST report but
  live and per-match rather than aggregated -- this endpoint should be
  exposed under the same trust assumptions as the REST API (issue #17):
  no auth of its own, operator-trusted networks only. **Testing note:**
  Starlette's `BackgroundTasks` run to completion before a `TestClient`
  call returns, so a REST-driven integration test can't observe a crawl
  genuinely "in progress" while a socket is attached. True
  live/in-order delivery is instead tested by invoking the websocket
  coroutine directly against a fake `WebSocket` double in the same event
  loop as the publishing code (avoiding unsafe cross-thread
  `asyncio.Queue` access); a separate test still exercises the real
  `TestClient.websocket_connect` transport for the "already finished" and
  "unknown crawl" paths.

## Issue #19: input form + Run button + live log (Visualping-branded)

- **Inputs:** operator-typed form fields in the browser: target URL,
  username, password, context length. No new backend input -- this issue
  is a static frontend consuming the REST/WebSocket API from issues
  #17-18.
- **Transformation:** `app/static/index.html` (plain HTML/CSS/vanilla JS,
  no build step) is served at `GET /` by a new route in
  `app/api/routes.py`. On submit, its script disables the Run button,
  `fetch()`s `POST /crawls` with the form values as JSON, then opens a
  `WebSocket` to `/ws/crawls/{crawl_id}` and appends one log line per
  `page_fetched`/`match_found` message it receives; on `crawl_finished` it
  appends a final line, closes the socket, and re-enables the Run button.
  Styled with Visualping's palette (`#da532c` accent, white background,
  rounded cards, system font stack) per the issue's design note.
  Username/password inputs are marked `autocomplete="off"` so the browser
  doesn't offer to save a third-party site's credentials.
- **Outputs:** the only data this page sends anywhere is the `POST
  /crawls` body (to this same app's own `/crawls` endpoint) -- no
  third-party requests, no analytics, nothing written to
  `localStorage`/cookies. **Data-flow note:** this is the first place an
  operator's typed credentials are held in browser memory (a page
  variable) before being sent over HTTP to `/crawls`, same trust
  assumptions already flagged for issues #17-18 (no auth of its own,
  trusted operators/networks only). **Testing note:** verified end-to-end
  with Playwright (already a project dependency, issue #5) driving a real
  Chromium against a real `uvicorn` server with `_build_orchestrator`
  mocked to a two-page crawl with an artificial delay -- this proves the
  Run button stays disabled while genuinely in progress and the log
  updates live, not just that the static markup looks right.
