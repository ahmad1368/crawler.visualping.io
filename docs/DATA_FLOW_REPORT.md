# Data Flow Report

This document tracks how data moves through the system, issue by issue. Each
issue that touches the crawl, extraction, storage, or API layers appends its
own section below describing: inputs -> transformation -> outputs, with a
focus on where credentials or extracted secrets travel.

All 28 issues are implemented as of issue #28. This file's tree and every
section below it were reviewed end-to-end for consistency as part of that
issue; see its own section at the bottom for what that review found. The
`README.md` "Architecture" section explains the *why* behind the
Strategy/Registry, Repository, and Observer patterns this tree's nodes
implement -- this document stays focused on the *what travels where*.

## Data flow tree (overview)

Updated as each issue lands. Nodes marked `(planned)` don't exist in code
yet -- they show where today's outputs are headed.

```
uvicorn app.main:app                          app/main.py (issue #28)
(the real entrypoint -- imports app.api.websocket for its side effect of
registering /ws/crawls/{id} on the shared app instance from app.api.routes,
then exposes that same app; running `uvicorn app.api.routes:app` directly
would silently never register the WebSocket route)
│
▼
Browser: GET /                                app/api/routes.py + app/static/index.html
(operator fills in url/username/password/context_chars in an HTML form)
│
└── JS: fetch POST /crawls, then WebSocket /ws/crawls/{id}, then
    fetch GET /crawls/{id}/report on crawl_finished, then (on a password
    cell click) fetch GET /crawls/{id}/snapshot?url=...
    app/static/index.html -- credentials leave the browser only in the
    POST body (JSON over the page's own origin); Run/Pause/Resume/Stop
    (issue #68) enable and disable together from one control-state
    function. The results table populates live, one row per match_found
    message as it arrives (issue #69) -- grouped client-side by
    (source_url, value) into the same shape the backend's
    _build_match_rows produces, incrementing count_in_page on a repeat
    rather than adding a duplicate row -- not just once at the end; each
    row still has a clickable password cell (dispatches a
    "password-cell-click" DOM event) that opens a modal showing the raw
    snapshot with the match `<mark>`ed and scrolled into view -- or, for
    image_metadata/binary, a locator + context fallback instead of trying
    to search a non-text body. A search box above the table (issue #86)
    filters the rendered rows client-side, case-insensitive substring
    match against page_url only -- no new request, applied to whatever
    rows are already in the browser (live or reconciled) and re-applied
    on every subsequent row update. Once crawl_finished arrives, GET
    /crawls/{id}/report is still fetched as the authoritative
    reconciliation pass and its `matches` replace the live-built table
    wholesale -- the live version is never treated as final. A
    completeness summary panel (pages visited, resources checked, unique
    passwords found, queue empty) updates live from page_fetched/
    match_found events during the crawl, then is overwritten with the
    authoritative CrawlSummary once GET /crawls/{id}/report loads. Each
    page_fetched log entry (issue #80) renders as a real `<a href>` to
    the fetched URL, target="_blank" -- opens the live target page in a
    new tab so the operator can browse exactly what the crawl visited,
    not just a styled/JS-only click handler

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
     fake Orchestrator, per issue #17's acceptance criteria; the built
     Orchestrator is also stashed on _CrawlState.orchestrator, issue #68,
     so the pause/resume/stop endpoints below have a live handle to it)
    │
    +-- POST /crawls/{id}/pause | /resume | /stop   app/api/routes.py (#68)
    |   (call state.orchestrator.pause()/resume()/stop() directly, then
    |    move _CrawlState.status to PAUSED/RUNNING/STOPPING; stop() uses a
    |    transitional STOPPING rather than the terminal STOPPED so GET
    |    /report's "report is set once status is terminal" guarantee still
    |    holds -- _run_crawl(), not this endpoint, finalizes STOPPING to
    |    STOPPED once Orchestrator.run() has actually returned)
    │
    └── Orchestrator.run()                      app/crawler/orchestrator.py
        (asyncio.Semaphore(concurrency)-bounded workers pop from the
         frontier until it's empty -- completion is queue_empty, not a
         page count; max_pages/max_duration_seconds default to None
         (issue #71) and are opt-in ceilings only, no longer the routine
         stopping condition; on start, calls Repository.get_visited_urls() and
         UrlFrontier.mark_visited() for each -- resumes a crashed prior
         run against the same *.db without re-fetching; a single URL's
         processing failure, e.g. the browser fetcher navigating into a
         genuine HTTP redirect loop, is caught per-URL so it can't hang
         or abort the rest of the crawl, issue #24; each worker also
         awaits an asyncio.Event before popping its next URL and checks a
         stop flag right after, issue #68 -- pause()/resume()/stop()
         control that Event/flag from outside an in-flight run(); once
         the frontier genuinely empties -- not an early/bounded stop --
         runs app/crawler/asset_scanner.py::audit_static_assets() as a
         final completeness pass, issue #99: re-scans every already-
         fetched page's stored text for /static/... references missed by
         structural link discovery, fetches whatever's missing through
         the same extractor pipeline, and attaches a
         StaticAssetCompletenessReport to the returned CrawlSummary
         (CrawlSummary.asset_completeness, None when skipped); also runs
         app/crawler/content_negotiation.py::probe_content_negotiation(),
         issue #103, same queue_empty-only gating: re-requests a bounded
         sample of already-crawled HTML pages with alternate Accept/
         X-Requested-With headers, attaching a ContentNegotiationReport
         (CrawlSummary.content_negotiation))
        │
        ├── UrlFrontier                          app/crawler/frontier.py
        │   (queue + visited-set seeded from the request URL; normalizes
        │    URLs, same-origin filter, dedupe prevents cyclic-link loops)
        │
        └── per URL popped: HttpFetcher.fetch(url)   app/crawler/fetcher.py
            (httpx.AsyncClient + Basic Auth, retry/backoff; extra_headers
             param added issue #103 for content-negotiation probing)
            └── FetchResult (content, content_type, status_code, headers,
                cookies, redirect_history -- issue #103, from httpx's own
                response.history, discarded before this issue since
                follow_redirects=True only surfaced the final response)
                │
                ├── RedirectExtractor.extract(redirect_history, url)
                │   app/extractors/redirect_chain.py -- issue #103, not
                │   routed through ExtractorRegistry (different input
                │   shape, same reason as HeaderCookieExtractor below)
                │
                ├── ExtractorRegistry.run_all(content, content_type, url)
                │   app/extractors/base.py -- dispatches to HtmlExtractor,
                │   CssJsExtractor, ImageExifExtractor,
                │   ImageStructuralExtractor, ImageOcrExtractor,
                │   ImageLsbExtractor, BinaryFallbackExtractor (each
                │   calls find_passwords() from app/matching.py;
                │   ImageOcrExtractor runs Tesseract over the decoded
                │   image and scans whatever text it recognizes -- reads
                │   a password drawn as pixels rather than present as
                │   parseable text/metadata; ImageStructuralExtractor,
                │   issue #101, hand-parses JPEG COM segments and PNG
                │   tEXt/zTXt/iTXt chunks -- zlib-decompresses a chunk
                │   before searching it; ImageLsbExtractor, issue #101
                │   Layer 3, reads the least-significant bit of every
                │   R/G/B/A pixel channel byte -- the only extractor
                │   that reads pixel *values* rather than pixel content
                │   or any text-shaped field at all)
                │
                ├── HeaderCookieExtractor.extract(headers, cookies, url)
                │   app/extractors/headers_cookies.py (not routed through
                │   ExtractorRegistry -- different input shape, see issue #11)
                │
                ├── if content_type is text/html:
                │   BrowserFetcher.fetch(url)     app/crawler/browser_fetcher.py
                │   └── BrowserFetchResult (dom_links, network_urls,
                │       cookies/local_storage/session_storage -- issue
                │       #103, a per-page page.evaluate() snapshot right
                │       after goto(); no persistent cross-page browser
                │       session exists in this design, see issue #103's
                │       own report section for why that scopes this to a
                │       per-page snapshot, not a whole-crawl diff)
                │       ├── ClientStorageExtractor.extract(cookies,
                │       │   local_storage, session_storage, url)
                │       │   app/extractors/client_storage.py -- issue #103,
                │       │   same not-routed-through-ExtractorRegistry
                │       │   reason as HeaderCookieExtractor/RedirectExtractor
                │       │
                │       └── UrlFrontier.add_many(...) -- feeds discovered
                │           links back into the queue (same-origin + dedup
                │           keeps the crawl bounded)
                │
                └── PageResult(url, status_code, fetched_at, matches)
                    app/models.py
                    │
                    ├── Repository.save_page(page, snapshot=content,
                    │   content_type=..., headers=..., cookies=...)
                    │   app/storage/ (SqliteRepository -- one *.db file per
                    │   crawl_id, durable, gitignored; content_type/headers/
                    │   cookies added in issue #72, alongside the snapshot,
                    │   specifically so a page can be re-extracted later
                    │   without a live re-fetch)
                    │   │
                    │   ├── get_snapshot(url)     raw bytes back out
                    │   │   │
                    │   │   └── GET /crawls/{id}/snapshot?url=...
                    │   │       app/api/routes.py (issue #21) -- decodes as
                    │   │       utf-8 (errors="replace") and returns the
                    │   │       full raw page/resource content to the UI's
                    │   │       "jump to location" snapshot viewer
                    │   │
                    │   ├── get_all_page_fetch_data()   every page's
                    │   │   │   content/content_type/headers/cookies
                    │   │   │   (issue #72) -- pages saved without a
                    │   │   │   content_type are excluded, never replayed
                    │   │   │   with a guessed one
                    │   │   │
                    │   │   └── POST /crawls/{id}/re-extract
                    │   │       app/api/routes.py -> app/crawler/replay.py
                    │   │       ::replay_extraction() -- re-runs a fresh
                    │   │       ExtractorRegistry + HeaderCookieExtractor
                    │   │       (built with the crawl's own context_chars,
                    │   │       _CrawlState.context_chars) against every
                    │   │       stored page, zero network/browser calls.
                    │   │       Read-only: returns fresh PasswordMatch
                    │   │       objects without calling save_match/
                    │   │       save_page, so it can't duplicate or corrupt
                    │   │       what a live crawl already recorded, and is
                    │   │       safe to call repeatedly. Response is the
                    │   │       same CrawlReportResponse{summary, matches}
                    │   │       shape as GET /report, computed fresh --
                    │   │       allowed at any crawl status (even RUNNING/
                    │   │       PAUSED) as long as the crawl has started
                    │   │
                    │   ├── get_matches()          every stored PasswordMatch,
                    │   │   app/storage/ (issue #20) -- routes.py groups
                    │   │   these by (source_url, value) into MatchTableRow
                    │   │   (page_url, source_type, value, context, count_in_page)
                    │   │
                    │   └── Orchestrator.run()'s return value -> CrawlSummary
                    │       for just this run, held in memory as
                    │       _crawls[crawl_id].report
                    │       │
                    │       └── GET /crawls/{id}/report   app/api/routes.py
                    │           (CrawlReportResponse{summary, matches} --
                    │           summary is the in-memory report; matches is
                    │           the MatchTableRow list built from
                    │           get_matches() above; 409 while running, 500
                    │           if the crawl failed -- NOT the same as
                    │           Repository.get_report(), a separate,
                    │           DB-wide aggregate CrawlSummary this endpoint
                    │           doesn't use)
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

## Issue #20: results table (page, source type, password, context, count)

- **Inputs:** the browser's `fetch GET /crawls/{id}/report`, triggered by
  the UI (issue #19) when it receives `crawl_finished`. No new user input.
- **Transformation:** `GET /crawls/{id}/report`'s response shape changed
  from a bare `CrawlSummary` to `CrawlReportResponse{summary, matches}`.
  `Repository` gained `get_matches() -> list[PasswordMatch]`
  (`SqliteRepository` implements it as a plain `SELECT ... FROM matches
  ORDER BY id`); `_CrawlState` now retains the crawl's `Repository`
  instance (`_build_orchestrator()` returns it alongside the orchestrator)
  so the report endpoint can read it back. `_build_match_rows()` groups
  raw matches by `(source_url, value)`, counting occurrences into
  `count_in_page` and deduping identical matches into one `MatchTableRow`
  (`page_url`, `source_type`, `value`, `context_before`/`context_after`,
  `count_in_page`). `app/static/index.html` renders one `<tr>` per row;
  the password cell is a `<button>` (not plain text) whose click handler
  dispatches a `password-cell-click` `CustomEvent` on `window` carrying
  the row -- the hook the snapshot viewer (issue #21) will attach to.
- **Outputs:** the full table of previously-persisted `PasswordMatch`
  values (plaintext secret + context) now travels from the per-crawl
  SQLite file, through this JSON response, onto the operator's own screen.
  **Data-flow note:** this is the intended end-of-pipeline destination for
  this data -- the tool exists so the operator who ran the crawl (with
  their own site's credentials) can see what it found -- but it's the
  first point actual secret *values* leave the database and reach an HTTP
  response body, not just aggregate counts (issue #17's report originally
  exposed counts only). Same trust assumption as issues #17-19 applies: no
  auth of its own, so this endpoint (and therefore every found password)
  is only as protected as the network this API runs on.

## Issue #21: snapshot viewer -- jump to match location

- **Inputs:** a `page_url` and `source_type` from a clicked `MatchTableRow`
  (issue #20), plus `MatchTableRow.locator` (new field this issue adds).
- **Transformation:** `MatchTableRow` gained a `locator` field, populated
  from the first grouped match's `.locator` in `_build_match_rows()`. A
  new `GET /crawls/{id}/snapshot?url=...` endpoint calls
  `Repository.get_snapshot(url)` and returns the decoded content as JSON.
  In `app/static/index.html`, clicking a password cell opens a modal:
  for `html_text`/`html_comment`/`css`/`js`/`http_header`/`cookie`, it
  fetches that snapshot, searches the decoded text for the exact match
  `value`, wraps the first occurrence in `<mark id="snapshot-mark">`, and
  scrolls it into view. If the value isn't found in the fetched text
  (expected for `http_header`/`cookie`, whose value lives in a header, not
  the page body -- there's nothing to search for) it falls back to a
  locator + context view instead of failing silently. For `image_metadata`
  and `binary`, the fallback view is used directly, skipping the snapshot
  fetch entirely -- per the issue's acceptance criteria, these aren't
  meaningfully searchable/scrollable as text.
- **Outputs:** the full raw content of a crawled page/resource -- not just
  the matched value and its N characters of context, but the *entire*
  snapshot -- now travels from the per-crawl SQLite file to the browser on
  demand. **Data-flow note:** per the project's data-flow watchlist,
  snapshot storage was flagged since issue #1 as potentially containing
  secrets beyond the one matched; this issue is where that risk becomes
  concrete over HTTP -- `GET /crawls/{id}/snapshot` will return the whole
  page (e.g. other passwords, tokens, or PII that happen to be on it) to
  anyone who can reach this endpoint with a valid `crawl_id` and `url`,
  not just the specific match being viewed. Same trust assumption as
  issues #17-20: no auth of its own, operator-trusted networks only -- but
  worth flagging explicitly since this endpoint's blast radius per request
  is larger than the report endpoint's (a whole page vs. one match's
  context).

## Issue #22: crawl completeness summary panel

- **Inputs:** `page_fetched`/`match_found` WebSocket messages (issues
  #16/#18) received during the crawl, and the final `CrawlSummary` from
  `GET /crawls/{id}/report` (issue #17/#20) once it finishes. No new
  backend endpoint or data -- purely a frontend consumer of data already
  reviewed in earlier issues.
- **Transformation:** `app/static/index.html` adds a summary panel with
  four stats (pages visited, resources checked, unique passwords found,
  queue empty). During the crawl, `resources_checked` increments on every
  `page_fetched` message and `unique_passwords_found` is the size of a
  client-side `Set` of `match_found` payload values -- both are things the
  client can track accurately from the event stream alone.
  `pages_visited`/`queue_empty` have no equivalent live signal (the
  client can't tell HTML pages from other resources from a `page_fetched`
  payload alone, and doesn't know the frontier's state), so they stay at
  placeholder values until `crawl_finished` triggers `GET
  .../report`, at which point `finalizeSummaryPanel()` overwrites all four
  stats with the authoritative `CrawlSummary` -- satisfying the issue's
  "updates live via the WebSocket, finalizes when the crawl completes"
  acceptance criterion honestly rather than faking numbers the client
  can't actually know yet.
- **Outputs:** aggregate counts only, rendered directly in the DOM --
  no new data leaves or enters the system beyond what issues #17/#18/#20
  already expose. **Data-flow note:** no new concerns; this panel doesn't
  touch `PasswordMatch` values, snapshots, or credentials.

## Issue #23: fixture-based unit test suite for extractors

- **Inputs:** none new at runtime -- this issue only adds test fixtures
  (`tests/fixtures/`) and a test file; no production code changed, no tree
  update needed.
- **Transformation:** one fixture per source type, each with a known
  synthetic (never-real) `VISUALPING{...}` password: `html_sample.html`
  (yields both `html_text` and `html_comment`), `css_sample.css`,
  `js_sample.js` (yields two `js` matches -- a comment and a string
  literal), `http_header_cookie_sample.json` (a `{headers, cookies}` dict
  pair for `HeaderCookieExtractor`, which doesn't take a body fixture),
  `image_sample.jpg` (a real 2x2 JPEG with an EXIF `UserComment`,
  generated once via Pillow), and `binary_sample.bin` (raw non-UTF8
  bytes). `tests/test_extractor_fixtures.py` parametrizes one assertion
  function over all 9 cases (8 source types, JS covered twice), running
  every extractor against its fixture and checking the match's `value`,
  `source_type`, and that `context_before`/`context_after` contain the
  expected surrounding text.
- **Outputs:** none -- test-only. **Data-flow note:** no new concerns.
  Every fixture password is a synthetic test value matching the project's
  own `VISUALPING{16 hex}` pattern, never a real credential; this
  consolidated pass builds on (and doesn't replace) the more granular
  per-extractor tests from issues #9-13, which also cover rejection/edge
  cases this pass doesn't re-test.

## Issue #24: frontier & orchestrator behavior tests

- **Inputs:** for resume, whatever URLs a `Repository` already has pages
  for (from a prior, crashed run against the same `*.db` file); for the
  redirect-loop case, a URL whose processing raises (in practice: the
  browser fetcher's `page.goto()` failing on a genuine HTTP redirect loop
  -- Chromium follows redirects as part of real navigation and errors out
  with something like `net::ERR_TOO_MANY_REDIRECTS` rather than hanging).
- **Transformation:** two small, deliberately minimal additions make both
  behaviors real rather than just test scaffolding. `UrlFrontier` gained
  `mark_visited(url)` (adds a normalized URL to the internal `seen` set
  without queuing it) and `Repository` gained `get_visited_urls()`
  (`SqliteRepository`: `SELECT url FROM pages`). `Orchestrator.run()` now
  calls the latter and feeds it into the former at the very start of every
  run, so a fresh `Orchestrator`/`UrlFrontier` pair pointed at a `*.db`
  from a previous run automatically skips everything already
  fetched -- this is "resume." Separately, each worker's call to
  `_process_url()` is now wrapped in `try/except Exception: continue`, so
  one URL's processing failing (redirect loop or otherwise) is skipped
  rather than propagating out of `asyncio.gather()` and aborting the
  entire crawl -- this is what actually prevents a redirect loop from
  taking down (or hanging) the whole run. **Scope note:** `HttpFetcher`
  itself was deliberately left unchanged -- it doesn't follow redirects
  (httpx's default), so a 3xx response is just returned as-is and there is
  nothing there that could chase a loop; enabling redirect-following there
  would be a new crawler capability this test-focused issue doesn't call
  for, not a fix this issue needs.
- **Outputs:** `get_visited_urls()` returns plain URLs (no credentials or
  extracted secrets) already public knowledge to anything with `*.db` file
  access. **Data-flow note:** no new sensitive data or sink -- both
  additions read/write only URL strings that were already being persisted
  since issue #14.

## Issue #25: integration tests for REST + WebSocket endpoints

- **Inputs:** none new at runtime -- no production code changed. The REST
  (`TestClient`, issues #17/#20/#21) and WebSocket (direct-coroutine +
  `TestClient.websocket_connect`, issue #18) integration tests already
  built incrementally across earlier issues already cover status codes,
  the mocked-orchestrator contract, and the event stream -- this issue's
  contribution is closing the one genuine gap: none of them exhaustively
  checked a response/message's *complete* shape, only spot-checked
  individual fields.
- **Transformation:** added
  `test_response_payload_shapes_match_the_full_contract`
  (`tests/test_api_routes.py`) asserting the exact key set of every REST
  response -- `POST /crawls`, `GET .../status`, `GET .../report`
  (top-level, `summary`, and one `matches` row), and `GET .../snapshot` --
  and `test_message_envelope_and_payload_shapes_match_the_full_contract`
  (`tests/test_websocket.py`) asserting the exact key set of the `{type,
  payload}` envelope and each of the three event payload shapes
  (`PageResult`, `PasswordMatch`, `CrawlSummary`). Both reuse the existing
  `FakeOrchestrator`/mocked-orchestrator pattern rather than introducing a
  new one.
- **Outputs:** none -- test-only. **Data-flow note:** no new concerns;
  no production code changed and no new data path was introduced.

## Issue #26: full crawl against a local fixture site (end-to-end)

- **Inputs:** a real local `http.server` (`tests/test_e2e_crawl.py`'s
  `FixtureSiteHandler`), requiring the same Basic Auth credentials the
  crawler is configured with, serving five resources -- an HTML page
  linking to a CSS file, a JS file, a JPEG, and a binary file -- with one
  synthetic `VISUALPING{...}` password planted per source type (8 total:
  `html_text`, `html_comment` in the page body; `css`; `js`;
  `http_header`/`cookie` on the page's own response; `image_metadata` in
  the JPEG's EXIF `UserComment`; `binary` in the raw file).
- **Transformation:** unlike every other test in this suite, nothing here
  is mocked except the target site itself. A real `Orchestrator` is wired
  to a real `httpx.AsyncClient` (`HttpFetcher`), a real Playwright
  `Browser` (`BrowserFetcher`), all four registered body extractors, a
  real `HeaderCookieExtractor`, and a real `SqliteRepository`
  (`:memory:`), then `run()` against the fixture server's base URL. This
  exercises the full, real pipeline end-to-end: credential flow (Basic
  Auth over real HTTP + a real browser context), link discovery
  (`BrowserFetcher` finds the four `<a href>` links, `UrlFrontier` queues
  them), every extractor's real content-type dispatch, and real
  persistence.
- **Outputs:** `repository.get_matches()` is compared against the exact
  expected `{value: source_type}` mapping with a single dict equality
  assertion -- this fails on either a missing password (a regression
  somewhere in the pipeline) or an extra one (a false positive), which is
  what "no false positives" actually requires proving, not just spot
  checks. **Data-flow note:** no new concerns -- every credential and
  password value here is synthetic and never leaves the local test
  process (the repository is `:memory:`, never written to disk). This is
  the first test that exercises credential flow and secret extraction
  together in one real pipeline run, which is the concrete proof behind
  every individual data-flow note from issues #1-25.

## Issue #27: GitHub Actions CI workflow (lint, type-check, test)

- **Inputs:** none at runtime -- CI-only infrastructure. On every push to
  `main` and every pull request into `main` or `staging` (the project's
  actual integration branch, per the repo's branch workflow), GitHub
  Actions checks out the repo and runs the pipeline below.
- **Transformation:** `.github/workflows/ci.yml` installs the project via
  `pip install -e ".[dev]"` (a new `dev` extra added to `pyproject.toml`:
  `pytest-cov`, `ruff`, `black`, `mypy`, pinned the same way every other
  dependency in this project is), then runs `ruff check`, `black --check`,
  `mypy app`, and finally `pytest --cov=app` (Playwright's Chromium is
  installed via `playwright install --with-deps chromium`, cached across
  runs alongside pip's own cache). Any step failing fails the whole job.
  **Scope note on rule selection:** `ruff` is configured with a
  deliberately practical rule set (`E`, `F`, `I` -- pycodestyle errors,
  pyflakes, import sorting) rather than an exhaustive one; this is the
  first time linting was introduced to a 25-issue-old codebase, and
  broader rule sets (pyupgrade's `datetime.UTC`-alias preference,
  blanket "no broad except" rules that would flag the project's few
  intentional, comment-documented `except Exception` clauses) would have
  meant either a large unrelated cleanup pass or immediately suppressing
  rules -- neither of which this CI-focused issue calls for. Fixed the
  handful of genuine findings the chosen rule set + `black` + `mypy`
  turned up (an unsorted import, an unused import, two long lines, one
  reformatting pass, and one real type gap in `get_crawl_report()` where
  `state.report` is `CrawlSummary | None` but is guaranteed non-`None`
  once `state.status` is `FINISHED` -- now asserted explicitly).
- **Outputs:** a pass/fail CI status on every push/PR; no runtime data
  flow. **Data-flow note:** no new concerns -- this is tooling
  infrastructure, touches no credentials, matches, or snapshots. Note for
  operators: CI runs `pytest` against the same codebase covered by issue
  #26's real end-to-end test, but that test only ever talks to a local
  fixture server with synthetic data -- no real target site or real
  credentials are ever involved in CI.

## Issue #28: README + architecture overview + finalize data-flow report

- **Inputs:** the finished, 27-issue codebase and this document's own
  27 prior sections -- reviewed end-to-end, not just appended to.
- **Transformation:** rewrote `README.md` in full: setup/run/test/lint
  instructions, a project-structure map, an Architecture section
  explaining *why* the Strategy+Registry (`extractors/`),
  Repository (`storage/`), and Observer (`events.py`) patterns exist (not
  just that they do -- `docs/DATA_FLOW_REPORT.md` already covers the
  latter in detail), and a Security Considerations section consolidating
  the recurring themes from this report's watchlist (no API auth, durable
  per-crawl `*.db` files, credential handling) into operator-facing
  guidance. While writing the "how to run it" instructions, found that
  nothing in `app/` ever imports `app.api.websocket` -- meaning `uvicorn
  app.api.routes:app` would serve the REST API and the UI, but
  `/ws/crawls/{id}` would never exist, silently breaking every UI feature
  downstream of it (issues #19, #21, #22). Added `app/main.py` as the real
  entrypoint (imports both `app.api.routes` and `app.api.websocket`,
  verified via a route listing and a live `uvicorn` smoke test that both
  REST and WebSocket routes register) and pointed the README at it instead
  of `app.api.routes:app`. Reviewed this report's tree and all 27 prior
  sections for stale `(planned)` markers or drift from current code --
  found none beyond the intentional, point-in-time historical narration in
  earlier sections (e.g. issue #13 correctly describing storage/API as
  still-planned *as of issue #13*) -- and added the new entrypoint as this
  tree's top node.
- **Outputs:** documentation only -- no runtime data flow, except that
  `app/main.py` is now the thing that makes every WebSocket-dependent data
  path described in issues #18/#19/#21/#22 actually reachable when the app
  is run the way the README instructs. **Data-flow note:** no new
  concerns. This closes out the original 28-issue backlog, this report and
  the README both reviewed end-to-end for consistency.

## Issue #61: false positive, header/attribute extractor fixes, query-param dedup, raise page limit to 1000

Filed after a diagnostic crawl against a real target surfaced five
concrete gaps. Fixed in the order listed.

- **Inputs/Transformation, per fix:**
  1. **False positive:** `app/matching.py` gained `KNOWN_EXAMPLE =
     "VISUALPING{0000deadbeef0000}"`; `find_passwords()` now skips any
     match equal to it before building a `RegexMatch`. Since every
     extractor funnels through `find_passwords()`, this is filtered once,
     centrally -- not left to each extractor's own regex hoping to be
     stricter.
  2. **HTTP header extractor:** audited `HeaderCookieExtractor` and
     `HttpFetcher`/`FetchResult` end-to-end. Found no allowlist bug (it
     already iterates every `headers.items()` unconditionally) and no
     header-merging bug (`dict(httpx.Response.headers)` already joins
     repeated header names with `", "` rather than silently dropping
     earlier values -- verified directly against httpx's actual
     behavior). No code change made here. The likely real cause of the
     "missing" header on the live site is fix #3/#5 below: the page
     carrying it was probably never reached because the crawl exploded on
     decorative query params and hit the old page cap first.
  3. **Query-param crawl explosion:** `UrlFrontier.normalize_url()` now
     strips a fixed set of decorative/tracking query params (`ref`,
     `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`,
     `utm_content`, `v`, `hl`, `fbclid`, `gclid`, `source`) from the
     dedupe key via `parse_qsl`/`urlencode`, while keeping any other query
     param untouched (so semantically meaningful params like `?id=5`
     still distinguish pages). Two links differing only by a stripped
     param now normalize to the same URL and dedupe in the frontier.
  4. **`html_text` missing attribute values:** `HtmlExtractor` no longer
     walks the DOM via `html.parser.HTMLParser` for visible text nodes
     only (which never sees attribute values at all, since they aren't
     text nodes). It now regex-scans the raw HTML source directly via the
     same `find_passwords()` every other extractor uses, tagging a match
     `HTML_COMMENT` if its offset falls inside a `<!-- -->` span (found
     via a separate regex pass) and `HTML_TEXT` otherwise. This is a
     deliberate broadening: attribute values, inline `data-*` attributes,
     and inline `<script>`/`<style>` content (previously skipped) are now
     all covered by the same pass and tagged `HTML_TEXT` -- per the
     issue's explicit instruction to cover "any other markup content" in
     one pass, not just attributes narrowly. Extracted the shared
     `line:N,col:M` locator math (previously duplicated in `css_js.py`)
     into `app.matching.locator_for_offset()`.
  5. **Raise page limit to 1000:** `Settings.max_pages`, `.env.example`'s
     `MAX_PAGES`, and `Orchestrator.__init__`'s `max_pages` parameter
     default all changed from `100` to `1000`. Also found and closed a
     real gap while doing this: `CrawlRequest` (the REST API's request
     body) had no `max_pages` field at all, so every API/UI-driven crawl
     silently used `Orchestrator`'s hardcoded default regardless of
     `Settings`/`.env` -- meaning bumping only `Settings.max_pages` would
     have had zero effect on the actual running app. Added
     `CrawlRequest.max_pages: int = 1000` and threaded it through
     `_build_orchestrator()` into `Orchestrator(...)`, so there is now
     one real default (1000) instead of several that could drift, and
     operators can still override it per crawl via the REST body if
     needed (no new UI field was added -- the UI's POST body simply omits
     the key and gets the schema default, which is what "the UI's
     assumed default" agreeing with everything else means here).
- **Outputs:** no new persisted data shape -- these are matching/dedup/
  config fixes, not new fields on `PasswordMatch` or `CrawlSummary`.
  **Data-flow note:** fix #4 means `HTML_TEXT` matches can now come from
  a wider slice of the raw page source (attributes, inline scripts) than
  before -- still the same sensitivity level as any other `HTML_TEXT`
  match, just more of them. Fix #5 means a real crawl can now visit up to
  10x more pages by default, which means proportionally more snapshot
  data and match rows accumulate in a crawl's `*.db` file -- the
  data-flow watchlist's existing snapshot-storage concern from issue #1
  scales with this change, though the storage model itself (one
  gitignored local file per crawl) is unchanged. **Not verified:**
  criterion 5's own acceptance criteria asks to re-run a full crawl
  against the real target with the new limit and record whether it
  actually finds more passwords or whether `queue_empty` was already
  reached well under 1000. This requires the real target's URL and Basic
  Auth credentials, which weren't available in this session -- flagged to
  the user to run and report back, rather than fabricated here.

## Issue #63: comprehensive fixes after #61 still only found 1/8 real passwords

Filed immediately after #61 merged, citing a second diagnostic crawl
against the same real target that still only surfaced 1 of 8 known
passwords, with 9 concrete items. This section covers the items fixable
without the real target itself; items requiring a live re-crawl are
called out explicitly, not fabricated.

- **Inputs/Transformation, per fix:**
  1. **[BLOCKING] `HttpFetcher` never followed redirects:** reversed a
     deliberate decision from issue #24 (made speculatively, "nothing
     here could chase a redirect loop") that #63 shows was wrong in
     practice -- most of the target's real content sits behind a 301/302,
     and redirects on URLs carrying a query string specifically were
     never being resolved at all. `app/crawler/fetcher.py`'s
     `HttpFetcher.fetch()` now calls `self._client.get(url, ...,
     follow_redirects=True)` and widened its retry-catch from
     `(httpx.TimeoutException, httpx.TransportError)` to the common base
     `httpx.RequestError`, which also covers `httpx.TooManyRedirects` --
     a redirect loop now surfaces as the same `TransientFetchError` any
     other transient failure does, after httpx's own internal hop limit,
     rather than hanging. Covered by three new tests in
     `tests/test_fetcher.py`: a clean-path redirect, a redirect on a URL
     with a query string (the exact reported bug), and a redirect loop
     proven to terminate via `asyncio.wait_for(..., timeout=5)`.
  5. **Query-param dedup switched from denylist to allowlist:** #61's
     `_TRACKING_PARAMS` denylist (11 named params) is replaced in
     `app/crawler/frontier.py` by `_IGNORED_QUERY_PARAMS = {"ref",
     "utm_source", "v", "hl"}` -- a much smaller explicit allowlist, per
     #63's instruction that guessing at a denylist is unsafe by default
     (a real, content-distinguishing param the denylist didn't know about
     gets silently collapsed and its content lost). Any query param not
     on the allowlist is now kept as URL-distinguishing by default
     (including `page`, and former denylist entries like `utm_campaign`/
     `fbclid`), and a `logging.getLogger(__name__).debug(...)` call
     records which unrecognized param triggered that decision -- silent
     by default, visible when an operator enables verbose logging to
     audit the choice. `tests/test_frontier.py` renamed/extended
     accordingly, including new tests proving `?page=1`/`?page=2` no
     longer collapse and formerly-denylisted params no longer strip.
  6. **Pagination trap defense:** the same diagnostic crawl found a
     `?page=N` family running past 500 pages against the real target with
     no end in sight -- indistinguishable from a deliberate crawl-budget
     trap, and now that fix #5 stops collapsing `page` values, such a
     family can enqueue unboundedly. New module
     `app/crawler/pagination_guard.py` adds `PaginationGuard`: it
     recognizes a URL as belonging to a pagination family only when it
     has exactly one, purely-numeric query param (`pagination_family_key`
     returns e.g. `"/report?page"`), tracks a per-family streak of
     consecutive pages that yielded neither a new frontier link nor a new
     password match, and stops enqueuing further URLs in that family once
     the streak hits `max_unproductive` (default 10, tunable via
     `Orchestrator(pagination_family_limit=...)`) -- logging why at
     `INFO`. Wired into `app/crawler/orchestrator.py`: `worker()` skips a
     URL the guard has already stopped, and `_process_url()` calls
     `guard.record(url, new_links=..., new_matches=...)` using the actual
     count of newly-enqueued links and matches found on that page. Covered
     by 9 unit tests in `tests/test_pagination_guard.py` plus an
     orchestrator-level integration test proving a runaway 50-page family
     is cut off after 3 unproductive pages while real content elsewhere
     in the same crawl is still found.
  7. **Cookie/session reuse across requests (confirmed, no code change):**
     `_build_orchestrator()` in `app/api/routes.py` already creates
     exactly one `httpx.AsyncClient()` per crawl and injects it into a
     single `HttpFetcher` instance reused for every `fetch()` call, so
     httpx's own client-level cookie jar already persists a `Set-Cookie`
     from an early response into the `Cookie` header of later requests on
     that same client -- no per-request client was ever being created.
     Added a regression test,
     `test_fetch_reuses_a_session_cookie_across_requests` in
     `tests/test_fetcher.py`, to lock this behavior in rather than leave
     it as an unverified assumption about httpx's defaults.
  8. **Browser fetcher networkidle/rendered-DOM/network-capture (confirmed,
     no code change):** re-read `app/crawler/browser_fetcher.py` against
     all three of #63's criteria: `page.goto(url, wait_until="networkidle")`
     already waits for network idle before extracting anything;
     `page.eval_on_selector_all("a[href]", ...)` already reads the
     rendered DOM post-JS-execution, not raw HTML source; and
     `page.on("request", ...)`/`page.on("response", ...)` listeners
     already capture every network request and response URL seen during
     the page load into `network_urls`, which the orchestrator already
     feeds into `UrlFrontier.add_many()` alongside `dom_links`. This
     confirms the behavior #63 asked for was already correct since issue
     #5 -- no gap found.
  9. **`MAX_PAGES` re-evaluation:** left at 1000 (already raised in #61).
     Not changed further here -- see Not verified below.
- **Outputs:** no new persisted data shape. `PasswordMatch`/`CrawlSummary`
  are unaffected; these are crawl-completeness and dedup-correctness
  fixes.
  **Data-flow/security note (per the data-flow watchlist):** fix #1
  means `HttpFetcher` now follows redirects while still attaching
  `httpx.BasicAuth` via the `auth=` parameter on every `.get()` call.
  httpx re-derives whether to (re-)send `Authorization` on each hop from
  the request/response pair rather than blindly repeating the original
  header, and strips it on a cross-origin redirect (a redirect to a
  different host) -- so Basic Auth creds are not leaked to a third-party
  host purely as a side effect of this change. They are still sent to
  same-host redirect targets, same as any other same-origin request this
  fetcher already made before #63. No new sensitive column, credential
  path, or outbound destination was introduced by fixes #5/#6/#7/#8.
  **Not verified against the real target (flagged, not fabricated):**
  - Item 1's own acceptance criterion, and item 9, both ask to re-run a
    full crawl against the real target post-fix and record how many of
    the 8 known passwords are now found, and whether `MAX_PAGES` needs
    further adjustment. Requires the real target's URL and Basic Auth
    credentials, not available in this session.
  - Item 6's guidance to "review real pagination content and decide if
    it's worth crawling fully" needs an actual sample of that content,
    which likewise requires the real target.
  - Item 8's confirmation is based on re-reading the existing
    implementation and its existing test coverage; it was not re-verified
    by driving a real browser against the real target's JS.

## Fix: image EXIF extractor missed the nested Exif sub-IFD (UserComment, etc.)

Found via code review after #63 still left the real crawl at 1/8 passwords
found -- `ImageExifExtractor.extract()` (`app/extractors/image_exif.py`)
only ever scanned `image.getexif().items()`, which is the base IFD0 only.
`UserComment` (tag `0x9286`), `DateTimeOriginal`, and most fields a person
would actually stash a note in live in the nested "Exif" sub-IFD (reached
via the IFD0 pointer tag `0x8769`), reachable only through
`exif.get_ifd(ExifTags.IFD.Exif)` -- never through the top-level mapping's
own `.items()`. Confirmed empirically with `piexif`: a `UserComment`
written the way any real tool writes it (piexif, exiftool, a camera) was
completely invisible to the old code. The project's own
`tests/test_image_exif_extractor.py` passed anyway because its fixture
helper set the tag directly on the top-level `Exif` object before saving,
which Pillow serializes flatly into IFD0 -- a shape no real-world tool
produces -- so the test gave false confidence.

- **Inputs/Transformation:** `extract()` now also scans
  `exif.get_ifd(ExifTags.IFD.Exif)` (tag names from `ExifTags.TAGS`, same
  namespace as IFD0) and `exif.get_ifd(ExifTags.IFD.GPSInfo)` (tag names
  from `ExifTags.GPSTAGS`), via a shared `_scan_ifd()` helper. Matches
  from the Exif sub-IFD share the `exif:<TagName>` locator prefix with
  IFD0 matches (both are conceptually "EXIF"); GPS IFD matches get a
  distinct `exif-gps:<TagName>` prefix. `exif.get_ifd(...)` returns an
  empty mapping (not an error) when an image has no sub-IFD, so this is
  safe against plain photos with only base EXIF or none at all.
- **Outputs:** no new `SourceType` -- still `IMAGE_METADATA`, just with a
  `locator` that can now read e.g. `exif:UserComment` or
  `exif-gps:GPSProcessingMethod` where before those fields were silently
  skipped. No new persisted column.
  **Data-flow/security note (per the data-flow watchlist):** no change in
  kind -- this surfaces the same class of sensitive text
  (`PasswordMatch.value`/context) through the same existing storage/API
  path, just from IFD locations the extractor previously missed.
- **Tests:** `piexif==1.1.3` added as a dev-only dependency
  (`pyproject.toml`) specifically to build realistic nested-IFD fixtures
  -- hand-rolling correct multi-IFD EXIF byte layout without it proved
  too fragile to trust as test fixture code. Two new tests in
  `tests/test_image_exif_extractor.py`:
  `test_extracts_password_from_user_comment_in_nested_exif_subifd` (the
  regression case) and `test_extracts_password_from_gps_ifd`. All 7
  tests in that file pass; full suite (148 tests, excluding the
  browser/UI/e2e tests that need a live Chromium) still passes; ruff,
  black, and mypy clean.
- **Not verified against the real target:** whether this specific fix
  recovers one of the 7 still-missing passwords can only be confirmed by
  re-running against the real target's URL and credentials, which this
  session does not have.

## Issue #68: pause, stop, and resume controls alongside Run

- **Inputs:** no new request body shape -- the three new endpoints take
  only the existing `crawl_id` path parameter, no request body.
- **Transformation:**
  1. `Orchestrator` (`app/crawler/orchestrator.py`) gains `pause()`/
     `resume()`/`stop()`, all synchronous (no `await`) since they just
     flip in-memory state an already-running `worker()` coroutine reads:
     an `asyncio.Event` (`pause()` clears it, `resume()` sets it,
     `worker()` awaits it before popping its next URL -- never
     interrupting an in-flight fetch) and a `_stop_requested` flag
     (checked right after the event, so a stopped worker exits before
     touching the frontier again). `stop()` also sets the event so an
     already-paused worker wakes up to see the stop rather than blocking
     forever. None of the three touch already-in-flight `_process_url()`
     work.
  2. `app/api/routes.py`: `_CrawlState` gains an `orchestrator` field
     (the built `Orchestrator` instance, stashed in `_run_crawl` right
     after `_build_orchestrator()` returns) so the new endpoints have a
     live handle to call into. `CrawlStatus` gains `PAUSED`, `STOPPING`,
     and `STOPPED`. Three new endpoints -- `POST /crawls/{id}/pause`,
     `/resume`, `/stop` -- validate the current status (409 on an invalid
     transition, e.g. pausing an already-paused crawl, or any control
     action before the crawl has actually started and `state.orchestrator`
     is still `None`), call the matching `Orchestrator` method, and move
     `_CrawlState.status` accordingly.
  3. **Race avoided deliberately:** `stop_crawl()` moves status to the
     transitional `STOPPING`, not the terminal `STOPPED`, because
     `orchestrator.run()` hasn't actually returned yet at that point (an
     in-flight fetch on another worker may still be running) -- and
     `GET /report` relies on "status is terminal implies `state.report`
     is set." `_run_crawl()` is the only place that ever finalizes
     `STOPPING` to `STOPPED`, one line after `state.report =
     await orchestrator.run()`, so the guarantee always holds. `GET
     /report` now 409s on `RUNNING`, `PAUSED`, *and* `STOPPING` (not just
     `RUNNING` as before); once truly `STOPPED` it behaves like
     `FINISHED` -- the report reflects whatever was actually processed
     before the stop.
  4. UI (`app/static/index.html`): three new buttons (Pause/Resume/Stop)
     next to Run, styled as a minimal outlined "secondary" variant of the
     existing button so they read as companions to Run rather than
     competing calls to action. A single `setControlsForState()` function
     is the one place that decides all four buttons' enabled/disabled
     state from one of `idle | running | paused | stopping`, called from
     the form submit handler, each control button's click handler, and
     the WebSocket's `crawl_finished` handler -- replacing the old
     `runButton.disabled = ...` toggles scattered across those spots.
- **Outputs:** no new persisted data shape -- `PasswordMatch`/
  `PageResult`/`CrawlSummary` are unaffected. A stopped crawl's
  `CrawlSummary` (from `Orchestrator.run()`'s normal return path, just
  triggered early) has `queue_empty=False` and lower `resources_checked`/
  `pages_visited` than a full run would, same shape as hitting `max_pages`
  early today -- nothing new for a consumer of `GET /report` to handle.
  **Data-flow/security note (per the data-flow watchlist):** no new
  credential or secret path. The three new endpoints carry no request
  body and return only `{crawl_id, status}` -- no `PasswordMatch`/context
  data, no Basic Auth credentials. Pausing/stopping doesn't change what
  gets persisted to the crawl's SQLite `*.db` or exposed via the existing
  `/report`/`/snapshot` endpoints, only when the crawl stops collecting
  more of it.
- **Tests:**
  - `tests/test_orchestrator.py`: three new tests against the real
    `Orchestrator` -- pause blocks every fetch until resumed
    (`test_pause_blocks_all_fetches_until_resumed`), stop before any work
    starts processes nothing (`test_stop_before_any_work_processes_nothing_and_leaves_queue_non_empty`),
    and stop mid-crawl ends early with a non-empty queue
    (`test_stop_mid_crawl_ends_early_with_a_non_empty_queue`, using a
    real per-fetch delay so the event loop actually yields between pages
    -- the file's existing fakes return instantly with no true
    suspension point, which would otherwise let a crawl this small run to
    completion in one scheduling turn and make "mid-crawl" unobservable).
  - `tests/test_api_routes.py`: a `SpyOrchestrator` double covering valid
    transitions (pause while running, resume while paused, stop while
    running or paused), invalid transitions (409s, and asserting the
    orchestrator method was *not* called), 404 for an unknown crawl, 409
    for a control action before the crawl has started, `GET /report`
    409ing on `PAUSED`/`STOPPING` and succeeding once `STOPPED`, and a
    direct test of `_run_crawl()`'s STOPPING-to-STOPPED finalization
    racing concern described above.
  - `tests/test_ui.py`: a new `_PausableFakeOrchestrator` (real
    `asyncio.Event`-gated pause/stop, not just a canned response) plus
    `test_pause_resume_stop_controls`, a full Playwright-driven run
    through every state transition -- idle -> running -> paused (with an
    explicit assertion that fetching genuinely stalls while paused, not
    just that the button looks disabled) -> running again -> stopping ->
    idle, ending with a partial `pages_visited` (0 < visited < 8) proving
    Stop actually cut the crawl short rather than letting it finish.
  - Full suite: 176 tests pass. ruff/black/mypy clean.
- **Not verified:** real-world behavior against the actual target site
  (only exercised against fakes/fixtures here, consistent with every
  other issue in this report that lacked real target URL/credentials).
  Also not covered: reconnecting a *new* WebSocket connection to a
  `PAUSED` crawl (e.g. after a page refresh) -- `app/api/websocket.py`'s
  connect-time check only special-cases `state.status is not RUNNING` as
  "already finished," which would incorrectly treat a paused crawl the
  same as a finished one for a reconnecting client. Out of scope here:
  the UI never reconnects mid-crawl (one WebSocket opens after `POST
  /crawls` and stays open through pause/resume/stop until
  `crawl_finished`), so this doesn't affect the shipped UI, but it's a
  real gap for any other client that might reconnect.

## Issue #69: stream found passwords into the results table live

- **Inputs:** no new backend input or endpoint -- purely a client-side
  change to how `app/static/index.html` consumes the `match_found`
  WebSocket messages it was already receiving (wired since issues
  #16/#18). Previously that payload was only used for a log line and the
  live password-count stat; the results table itself waited for the
  final `GET /crawls/{id}/report` after `crawl_finished`.
- **Transformation:** a new client-side `liveMatchesByKey` `Map`, keyed
  the same way the backend's `_build_match_rows()`
  (`app/api/routes.py`) already groups matches -- `(source_url, value)`.
  On each `match_found` message, `recordLiveMatch()` either increments
  the existing entry's `count_in_page` (a repeat of the same password on
  the same page) or adds a new one, then re-renders the results table
  from the map via the existing `renderResultsTable()` (already reused
  as-is, since it just takes a row-shaped array -- no change to it). The
  final `GET /report` fetch on `crawl_finished` is deliberately left in
  place as the authoritative reconciliation pass (per this issue's own
  suggested resolution) -- it replaces the live-built table wholesale
  with the backend-computed one, so a client-side grouping bug could
  never leave a wrong result on screen after the crawl actually finishes.
- **Outputs:** no new persisted data shape, no new REST/WebSocket
  contract -- same `match_found` payload shape, just consumed one more
  place in the browser.
  **Data-flow/security note (per the data-flow watchlist):** no new
  exposure surface. The WebSocket already carried the full
  `PasswordMatch` (plaintext secret + context) to the browser as of
  issue #16/#18; this issue doesn't transmit or persist anything new, it
  only makes the browser render data it was already receiving earlier
  instead of discarding it until the end.
- **Tests:** `tests/test_ui.py` gains `_MatchStreamingFakeOrchestrator` /
  `_MatchStreamingFakeRepository` (publishes `MATCH_FOUND` with a real
  delay between each, backs the final report with the same matches) and
  `test_results_table_populates_live_before_crawl_finishes` -- a
  Playwright-driven test proving: a row appears after the first match
  while the crawl is still running (`Crawl finished` not yet logged); a
  second match for the *same* `(source_url, value)` increments that
  row's count rather than adding a duplicate; a third, different match
  adds a second row, still pre-finish; and the post-`crawl_finished`
  `GET /report` reconciliation lands on the same two rows. Full suite:
  177 tests pass. ruff/black/mypy clean.
- **Not verified:** real-world behavior against the actual target site
  (only exercised against fakes/fixtures here, same caveat as every
  other UI-layer issue in this report).

## Issue #70: verify same password on multiple distinct pages lists once per page

Verification-only issue, filed against a suspicion that turned out not to
hold -- traced the full pipeline end-to-end and found no gap:

- **`SqliteRepository`** (`app/storage/sqlite.py`): the `matches` table's
  only primary key is an autoincrement `id` -- no `UNIQUE` constraint on
  `value` or `(value, source_url)`. `_insert_match()` is an unconditional
  `INSERT`, and `get_matches()` returns every row, ordered by `id`. Two
  `save_page()` calls for two different pages, each with a match sharing
  the same `value`, produce two independent rows.
- **`Orchestrator`** (`app/crawler/orchestrator.py`): the `unique_values:
  set[str]` accumulated across `worker()` iterations only feeds
  `CrawlSummary.unique_passwords_found` (a count) -- it's never consulted
  before calling `self._repository.save_page(...)`, so it can't
  short-circuit or suppress storage of a repeat value on a later page.
- **`_build_match_rows()`** (`app/api/routes.py`): already grouped by
  `(match.source_url, match.value)`, not `value` alone -- confirmed by
  reading the existing code, not just the issue's own quoted snippet.
  Only an *identical* `(page, value)` pair collapses into one row with an
  incremented `count_in_page`; a different page with the same value was
  already a separate row before this issue.
- **Live results table** (`app/static/index.html`, issue #69): the
  client-side `liveMatchesByKey` grouping key is `sourceUrl + " " +
  value` -- structurally the same `(source_url, value)` pairing, so it
  already produces one row per page for a repeated value too. Verified
  by code inspection (the same `recordLiveMatch()`/`matchKey()` code
  path issue #69 already tests for a *different*-value-per-page case);
  not given a dedicated new Playwright test here, since it would exercise
  the identical generic grouping logic issue #69's test already covers,
  not new logic.
- **Outputs:** no production code changed. Two new regression tests
  stand as the requested verification evidence:
  - `tests/test_sqlite_repository.py::test_get_matches_preserves_the_same_value_found_on_two_distinct_pages`
    -- storage layer.
  - `tests/test_api_routes.py::test_same_password_found_on_two_distinct_pages_lists_once_per_page`
    -- API/report layer, asserting each row carries its own page's
    `source_type`/`context`/`locator`, not the other page's.
  **Data-flow/security note (per the data-flow watchlist):** none -- no
  code changed, no new data path.
- **Tests:** full suite: 179 tests pass (2 new). ruff/black/mypy clean.
- **Conclusion:** already correct, as issue #70 itself suspected. Closed
  as verification-only, per the issue's own instructions.

## Issue #71: replace fixed max_pages cap with a real completion condition

User's own real-world evidence (a 100-page cap found 2/8 known
passwords, a 1000-page cap found 4/8) proved the fixed cap -- not an
actually-infinite frontier -- was what was cutting real crawls short,
and that no single number can be picked in advance for a site of unknown
size.

- **Inputs:** no new request field type, just changed defaults --
  `CrawlRequest.max_pages` goes from `int = 1000` to `int | None = None`,
  and a new `CrawlRequest.max_duration_seconds: float | None = None`.
  Same change mirrored in `Settings.max_pages`/new
  `Settings.max_duration_seconds` (`app/settings.py`) and `.env.example`
  (both commented out, `MAX_PAGES`/`MAX_DURATION_SECONDS`), for the
  future non-API entry point those settings exist for.
- **Transformation:** `Orchestrator.__init__` (`app/crawler/orchestrator.py`)
  takes both as `None`-defaulted, opt-in ceilings now instead of
  `max_pages` being a mandatory, always-active cap. The worker loop's
  stopping checks changed from an unconditional `state["resources_checked"]
  >= self._max_pages` to guarded checks (`if self._max_pages is not None
  and ...`), plus a new elapsed-time check against `started_at` when
  `max_duration_seconds` is set. `queue_empty` (i.e. the frontier
  actually emptying) is now genuinely the primary/intended completion
  signal for a normal, finite same-origin site -- unaffected by this
  change: `UrlFrontier`'s same-origin filter + once-only `_seen` dedup
  already make that site's URL space finite regardless of any page-count
  cap. `PaginationGuard` (issue #63) is unchanged and remains the
  specific defense against an unbounded `?page=N`-style family; this
  issue doesn't touch or replace it, since it guards a different failure
  mode (one URL family looping forever) than a blanket page-count cap
  did (routine crawls being truncated early).
- **Outputs:** no new persisted data shape. **Behavior change worth
  naming explicitly:** a crawl with no explicit `max_pages`/
  `max_duration_seconds` set can now legitimately fetch and persist far
  more pages/resources -- and therefore far more `PasswordMatch` rows and
  raw snapshots -- than the old 1000-page ceiling ever allowed, for a
  large real site. That's the intended fix (finding all 8 passwords
  requires it), not a regression, but it does mean a crawl against an
  unexpectedly large or slow site can now run substantially longer and
  produce a substantially larger `*.db` file than before, with no cap
  unless the operator opts into one.
  **Data-flow/security note (per the data-flow watchlist):** no new kind
  of sensitive data or new sink -- same `PasswordMatch`/snapshot storage
  path as always, just potentially much more volume per crawl by
  default. Flagging per the watchlist's instruction to note the volume
  change, not because a new exposure surface was introduced.
- **Tests:**
  - `tests/test_orchestrator.py`: `test_max_pages_defaults_to_none_so_a_large_crawl_is_not_capped`
    (a ~1200-URL fake frontier -- more than the old 1000 default --
    fully completes with `queue_empty=True` when `max_pages` is
    omitted) and `test_max_duration_seconds_stops_the_crawl_early` (a
    real per-fetch delay, `max_duration_seconds` set short, crawl ends
    early with `queue_empty=False`, mirroring the `stop()`-mid-crawl
    test's timing approach from issue #68).
  - `tests/test_api_routes.py`: `CrawlRequest`'s new `None` defaults,
    both overridable, and `_build_orchestrator` threading both through
    to `Orchestrator` correctly (including the `None`-by-default case,
    not just the overridden case).
  - `tests/test_settings.py`: `Settings.max_pages`/`max_duration_seconds`
    default to `None` when their env vars are absent, and load correctly
    when set.
  - Full suite: 182 tests pass (7 new). ruff/black/mypy clean.
- **Not verified against the real target:** whether this actually
  recovers the remaining missing passwords (the issue's own stated goal)
  can only be confirmed by re-running against the real target's
  URL/credentials, not available in this session -- same recurring
  caveat as every fix in this report since issue #61.

## Issue #72: cache/replay fetched site data instead of re-crawling live on every query

User's question -- "store the entire site in memory... make a faster
query" -- was answered as its own request: fetching (HTTP + Playwright
navigation) is the real bottleneck, not extraction (fast, pure-CPU
regex), and the crawl already durably stores every page's raw bytes.
The gap was that content_type/headers/cookies -- everything extraction
actually needs besides the bytes -- weren't persisted alongside the
snapshot, so there was no way to correctly re-run extraction against
stored data without them.

- **Inputs:** `Repository.save_page()` (`app/storage/repository.py`)
  gains three new optional parameters: `content_type`, `headers`,
  `cookies`. `Orchestrator._process_url()` now passes all three from the
  `FetchResult` it already had (`fetch_result.content_type`/`.headers`/
  `.cookies`) -- previously computed and used for extraction in that same
  request, then discarded rather than persisted.
- **Transformation:**
  1. `SqliteRepository`'s `pages` table gains `content_type`, `headers`,
     `cookies` columns (the latter two JSON-encoded dicts). New
     `get_all_page_fetch_data()` reads them back out as a
     `list[PageFetchData]` (new model, `app/models.py`) -- joined against
     `snapshots` for the content bytes. A page saved without a
     `content_type` (the parameter is optional, for any caller that only
     cares about the snapshot) is excluded from this list rather than
     replayed with a guessed or empty one, which could misroute an
     extractor (e.g. treat an image as HTML).
  2. New `app/crawler/replay.py::replay_extraction()`: given a
     `Repository`, an `ExtractorRegistry`, and a `HeaderCookieExtractor`,
     re-runs both against every page's stored fetch data and returns a
     `CrawlSummary` (same shape a live crawl produces) plus the full
     match list. Deliberately **read-only** -- never calls
     `save_page`/`save_match` -- so replaying can't duplicate or corrupt
     what a real crawl already persisted, and is safe to call repeatedly
     (e.g. once per extractor-tuning iteration).
  3. New `POST /crawls/{id}/re-extract` (`app/api/routes.py`): builds a
     fresh `ExtractorRegistry`/`HeaderCookieExtractor` using the crawl's
     own `context_chars` (now kept on `_CrawlState.context_chars`, set at
     crawl start, so replay produces context strings consistent with the
     original crawl rather than silently reverting to a default), calls
     `replay_extraction()`, and returns the same `CrawlReportResponse`
     shape `GET /report` does. Allowed at any crawl status past "started"
     (`state.repository is not None`) -- including `RUNNING`/`PAUSED` --
     since it's read-only and safe to call against a still-in-progress
     crawl's data so far, not just a finished one.
  4. `_build_match_rows()` refactored to take a plain `list[PasswordMatch]`
     instead of a `_CrawlState`, so both `GET /report` (backed by
     `Repository.get_matches()`, the durably-persisted set) and the new
     endpoint (backed by `replay_extraction()`'s freshly-computed,
     unpersisted set) can share the same grouping/dedup logic.
  5. **Deliberately not implemented** (per the issue's own stated
     priority): an in-process `HttpFetcher` cache keyed by URL. Lower
     priority because `UrlFrontier`'s existing dedup already means no URL
     is fetched twice within one live crawl today -- it would only start
     mattering if a future feature re-visits URLs (e.g. a retry-on-failure
     path). Not built speculatively.
- **Outputs:** `PageFetchData` (new) carries the same class of sensitive
  data a snapshot already does -- raw response bytes -- plus something
  new: **the target site's raw response headers and cookies, now
  persisted verbatim**, not just whichever substring happened to match
  the password regex.
  **Data-flow/security note (per the data-flow watchlist -- flagged
  explicitly, not silently added):** this is a genuinely new class of
  data at rest. A response header or `Set-Cookie` value can carry a
  session token, CSRF token, or other secret that was never a password
  match and is now stored in the crawl's `*.db` regardless. This is the
  same trust boundary the report already applies to snapshot content
  (**"Crawl snapshots... can contain secrets beyond the one matched
  password -- treat snapshot storage itself as sensitive data at
  rest"**) extended to a new column, not a new category of risk: the
  `*.db` file was already fully sensitive, gitignored, and never
  auto-cleaned before this issue. There was no way to enable replay of
  header/cookie-embedded password matches without persisting the
  header/cookie values replay needs to scan -- an inherent tradeoff of
  the feature, not an oversight.
- **Tests:**
  - `tests/test_sqlite_repository.py`: `save_page()`'s new params
    persist and read back correctly via `get_all_page_fetch_data()`; a
    page saved without `content_type` is excluded; missing headers/
    cookies default to `{}`; the upsert path updates all three fields on
    a repeat `save_page()` for the same URL.
  - `tests/test_orchestrator.py`:
    `test_orchestrator_persists_content_type_headers_cookies_for_replay`
    -- confirms the full live pipeline (fetch -> orchestrator ->
    storage) actually threads these through, not just the storage layer
    in isolation.
  - `tests/test_replay.py` (new file): `replay_extraction()` finds
    matches from stored HTML and from stored headers/cookies; routes to
    the extractor matching the *stored* content_type (not every
    extractor); is read-only (a full `Repository` double asserts
    `save_page`/`save_match` are never called); handles zero stored
    pages without error; a repeated value across two pages counts once
    in `unique_passwords_found` but still produces two match rows.
  - `tests/test_api_routes.py`: `POST /crawls/{id}/re-extract` -- 404
    unknown crawl, 409 before the crawl has started, a successful replay
    against a `FakeRepository`'s stored fetch data, allowed while the
    crawl status is still `RUNNING`, and idempotent across two
    consecutive calls (same result both times, proving the endpoint
    itself doesn't accumulate state across calls).
  - Full suite: 198 tests pass (16 new). ruff/black/mypy clean.
- **Not implemented / explicitly out of scope:** no UI button wired up
  for `/re-extract` -- the issue's own proposed design only asked for
  "a CLI/script entry point, or a new API action," and the API action is
  what got built. Adding a UI trigger is a natural, separate follow-up
  if wanted, not folded in here to avoid scope creep into UI-layer
  design decisions (button placement, what "re-extract" should be called
  to an operator, whether it needs its own confirmation) that weren't
  part of this issue's request.

## Issue #78: PaginationGuard doesn't stop an adversarial pagination trap that always looks productive

Found via live testing against the real target (URL deliberately not
recorded here or anywhere else in this repo, per the challenge's own
"don't share the site" instruction): a `?page=N` family serving
randomized content on every page specifically so it always looks "new,"
which defeated `PaginationGuard`'s existing streak logic and let that
family run unbounded -- especially dangerous combined with issue #71's
now-`None`-by-default `max_pages`, which removed the one thing that used
to eventually cut off *any* runaway family, trap or legitimate.

- **Root cause:** `PaginationGuard.record()` reset its unproductive
  streak whenever a page yielded `new_links` **or** `new_matches`. For
  *any* ordinary sequential pagination, discovering the link to the next
  page is essentially always "new" the first time it's seen -- so
  `new_links` was never actually a reliable signal that a family was
  "still worth crawling," trap or not. A trap that randomizes content so
  every page plausibly contains something link-like exploits this
  directly: the streak counter never accumulates, `is_stopped()` never
  returns `True`, and the crawl never terminates on that family.
- **Transformation, two layers:**
  1. `PaginationGuard.record(url, new_matches)` -- `new_links` removed
     from the signature entirely (not just ignored: a parameter accepted
     but silently unused would be misleading). The unproductive streak
     now resets **only** on an actual new password match. A family that
     keeps discovering "new" links/content but never a real match now
     correctly accumulates an unproductive streak and stops after
     `max_unproductive` (still 10 by default) pages, regardless of how
     much it superficially varies page to page.
  2. New unconditional hard ceiling: `max_family_pages` (default 50,
     `PaginationGuard.__init__`, threaded through
     `Orchestrator(pagination_family_page_cap=...)`) stops any one family
     at that many total pages, independent of the streak logic --
     protects against a family engineered to occasionally look
     "productive" enough to keep resetting the streak (e.g. a genuine
     match sprinkled in periodically) from running unbounded regardless.
     Deliberately **on by default**, unlike `max_pages`/
     `max_duration_seconds` (issue #71): those bound an entire crawl of
     an unknown-sized real site where no fixed number is ever safely
     guessable up front; a single pagination family is a narrower,
     inherently guard-worthy shape this class already treats as
     suspicious by existing, so a sane default here doesn't carry that
     same risk of truncating a legitimate crawl.
  3. `Orchestrator._process_url()` no longer computes/threads a
     `new_links` count at all -- it existed purely to feed the old
     signal. `UrlFrontier.add_many()` calls for `dom_links`/
     `network_urls`/`interaction_urls` are unchanged, just no longer have
     their return values summed for this purpose.
- **Outputs:** no new persisted data shape, no new endpoint. Behavior
  change only: a pagination family that never yields a real match now
  terminates predictably instead of running indefinitely.
  **Data-flow/security note (per the data-flow watchlist):** none --
  no new credential path, no new sensitive data, no new sink. This is a
  crawl-completeness/termination fix.
  **Trade-off, named rather than assumed away:** an index-style
  pagination family that never itself contains a password but links out
  to real content pages elsewhere could, in principle, be cut off by
  `max_family_pages` before discovering everything past page 50 of that
  index. Any such links already discovered on earlier pages are still
  crawled normally as their own, independent URLs (`PaginationGuard`
  only ever gates the family's *own* URLs, never downstream ones already
  added to the frontier) -- only additional links first appearing beyond
  the cap would be missed. Judged acceptable for a tool that must
  terminate; flagged here rather than silently decided.
- **Tests:**
  - `tests/test_pagination_guard.py`: rewritten for the new
    `record(url, new_matches)` signature. New cases:
    `test_guard_is_not_defeated_by_a_family_that_always_reports_zero_matches_but_never_zero_links`
    (the core issue #78 regression, at the unit level), plus hard-ceiling
    coverage (`max_family_pages` stops a family that never goes
    unproductive at all, doesn't trip early, is disabled by `None`, and
    is on by default).
  - `tests/test_orchestrator.py`:
    `test_pagination_guard_terminates_an_adversarial_trap_that_always_looks_productive`
    -- an end-to-end simulation of the real finding (every one of 100
    fake pagination pages links only to the next page in the chain, no
    extractor registered so `new_matches` is always 0), with `max_pages`
    explicitly `None` (issue #71's default, no whole-crawl cap in play).
    Asserts the crawl processes exactly the 10 pagination pages before
    the guard trips and ends with `queue_empty=True` -- not just "fewer
    than 100," a precise, deterministic proof of termination.
  - Full suite: 204 tests pass (10 new). ruff/black/mypy clean.
- **Not verified against the real target:** re-running against the
  actual trap to confirm it now terminates (and how many of the 8
  passwords are found once it does) needs the real target's
  URL/credentials, not available in this session -- same recurring
  caveat as every fix in this report since issue #61.

## Issue #80: clickable page-fetched log links, opening in a new tab

- **Inputs:** no new backend input or endpoint -- purely a client-side
  change to how `app/static/index.html` renders the `page_fetched`
  WebSocket payload it was already receiving (the URL and status code,
  unchanged since issue #16/#18).
- **Transformation:** new `appendPageLink(url, statusCode)` builds a real
  `<a href="{url}" target="_blank" rel="noopener noreferrer">` inside the
  log line, replacing the plain-text `appendLog(...)` call previously
  used for `page_fetched` entries specifically (every other log message
  type -- crawl started/paused/resumed/stopping/finished, errors, match
  found -- is unchanged, still plain text via `appendLog`). A real anchor
  rather than a styled button or a JS-only click handler, per the
  request, so it supports the browser's native affordances too (hover to
  see the URL, right-click "open in new tab," middle-click, etc.), not
  just a left-click.
- **Outputs:** no new persisted data shape, no new response payload --
  same `page_fetched` data, just rendered differently.
  **Data-flow/security note (per the data-flow watchlist):** no new
  concern. The link's `href` is only the target URL, never credentials
  (the target requires HTTP Basic Auth; embedding `user:pass@host` in a
  link is both bad practice and blocked by modern browsers, so this was
  never on the table). Clicking a link makes the operator's own browser
  navigate directly to the live target site in a new tab -- the operator
  already possesses those credentials (they typed them into the crawl
  form) and is knowingly testing that site, so this isn't a new exposure,
  just a convenience for browsing what was actually visited. First click
  in a browser session hits the target's native Basic Auth prompt (no
  way to carry credentials over automatically); the browser caches that
  per-origin afterward, so it's a one-time prompt per session, not per
  click -- disclosed as an accepted UX wrinkle in the issue, not treated
  as a defect.
- **Tests:** extended the existing
  `test_run_button_disables_during_crawl_and_log_updates_live`
  (`tests/test_ui.py`) rather than adding a new fixture/test, since it
  already exercises a live `page_fetched` event -- new assertions check
  the rendered `<a>`'s `href`/`target`/`rel` attributes and visible text
  directly. Full suite: 204 tests pass (no new test *count* -- coverage
  added to an existing test). ruff/black/mypy clean.
- **Not implemented / explicitly out of scope:** routing the link through
  the app's own already-persisted snapshot (issue #72) instead of the
  live target, to avoid the Basic Auth re-prompt entirely, would work but
  is a larger, separate change (a new endpoint serving raw bytes with the
  stored `content_type` so a browser tab renders it) not requested here
  -- noted as a natural future enhancement if the auth-prompt friction
  turns out to matter in practice.

## Feature: read passwords drawn as image pixels (OCR)

- **Inputs:** same fetched image bytes `ExtractorRegistry.run_all()`
  already passes to every registered extractor -- no new input, no new
  endpoint. Targets a distinct obfuscation technique from every prior
  extractor in this file: the password isn't present as parseable
  text or metadata anywhere in the file at all -- it's drawn as pixels
  (a photographed whiteboard, a screenshot of a sticky note, hand- or
  machine-lettered text baked into the image), readable only by eye or
  by actually recognizing the glyphs. `ImageExifExtractor` reads
  metadata *about* the image; nothing in this codebase previously read
  what the image *shows*.
- **Transformation:** new `app/extractors/image_ocr.py` ->
  `ImageOcrExtractor`, registered into `ExtractorRegistry` alongside
  `ImageExifExtractor` (both call sites: `_build_orchestrator` for a
  live crawl and the `/re-extract` replay path in `app/api/routes.py`).
  Opens the image with Pillow (same `Image.open()` + `OSError`/
  `DecompressionBombError` guard as `ImageExifExtractor`, for the same
  malformed-input reason) and runs `pytesseract.image_to_string()` --
  a thin wrapper around the Tesseract OCR engine binary -- over the
  decoded image, then hands whatever text it recognizes to the same
  `find_passwords()` every other extractor uses, so the password format
  and the `KNOWN_EXAMPLE` exclusion (`app/matching.py`) stay identical.
  A new `SourceType.IMAGE_OCR` (`app/models.py`) marks these matches.
  Tesseract is a system binary, not a pip package -- `pytesseract` (added
  to `pyproject.toml`) just shells out to it; if it's missing or errors
  on a given image (`TesseractNotFoundError`/`TesseractError`), `extract()`
  degrades to no matches rather than raising, so one broken/exotic image
  never aborts a crawl. CI (`.github/workflows/ci.yml`) now installs it
  via `apt-get install tesseract-ocr` before running tests; local Setup
  in `README.md` documents the equivalent for macOS/Windows.
- **Outputs:** `PasswordMatch` rows shaped like any other extractor's,
  with `locator` as `ocr:offset:N` -- an offset into the *OCR'd text*,
  not a pixel position in the image (getting an actual bounding box
  would mean requesting Tesseract's per-word layout data via
  `image_to_data` instead of plain `image_to_string`, not done here since
  nothing consumes it yet). The UI's snapshot viewer
  (`app/static/index.html`) already has a fallback path for source types
  whose match value can't be found via `indexOf` in the raw fetched body
  (previously `image_metadata`/`binary`); `image_ocr` is added to that
  `NON_TEXT_SOURCE_TYPES` set for the same reason -- the raw image bytes
  never literally contain the password as text to search for.
  **Data-flow/security note (per the data-flow watchlist):** no new
  exposure surface -- same `PasswordMatch`/context/locator shape, same
  SQLite storage, same REST/WebSocket delivery as every other extractor;
  worth flagging because it's the strongest example yet of why this
  tool's extractor set must keep being treated as partial: a site could
  reasonably assume a password baked into an image as *pixels* is safe
  from any automated scanner, and until now that assumption held.
- **Tests:** new `tests/test_image_ocr_extractor.py` (4 tests) -- draws a
  password onto a blank image with Pillow's bundled default font (no
  system-font dependency, so the fixture renders identically on any OS)
  and confirms OCR reads it back byte-for-byte, plus ignores non-image
  content types, returns no matches for a genuinely blank image, and
  degrades gracefully on unparseable image bytes. The whole file is
  `pytest.mark.skipif`-guarded on Tesseract actually being importable/
  runnable (`pytesseract.get_tesseract_version()`), so a dev machine
  without the binary installed sees 4 skips, not 4 failures, rather than
  silently passing on a broken assumption. Full suite (with Tesseract
  installed): 208 tests pass (204 existing + 4 new). ruff/black/mypy
  clean.
- **Not implemented / explicitly out of scope:** no image preprocessing
  (deskew, threshold/contrast boost, upscaling) before handing the image
  to Tesseract -- real-world low-contrast or angled photos may OCR worse
  than this feature's clean synthetic test fixture; if that turns out to
  matter, preprocessing would slot into `ImageOcrExtractor.extract()`
  right before the `image_to_string()` call. Bounding-box locators
  (`image_to_data` instead of `image_to_string`) are also left for later,
  noted above.

## Issue #86: search/filter input above the results table

- **Inputs:** no new backend input or endpoint -- purely a client-side
  filter over data `app/static/index.html` already holds: `match_found`
  WebSocket rows during a live crawl, and `report.matches` from
  `GET /crawls/{id}/report` after `crawl_finished`.
- **Transformation:** new `#results-filter` `<input type="search">`
  above `#results-table`. `renderResultsTable(matches)` -- the one
  function every row producer already calls -- now stashes its argument
  in `currentMatches` and calls `applyResultsFilter()` instead of
  rendering directly; that function filters `currentMatches` through
  `rowMatchesFilter()` (case-insensitive `String.includes()` against
  `row.page_url` only) and hands the result to the renamed
  `renderMatchRows()`. An `input` listener re-runs the same filter on
  every keystroke. Because filtering always re-derives from
  `currentMatches` rather than mutating the DOM in place, a live match
  arriving mid-filter is still subject to whatever term is currently
  typed -- the filter doesn't need separate live/reconciled code paths.
  Starting a new crawl clears `currentMatches` and the input value, so a
  stale filter term never silently hides a new crawl's rows.
- **Outputs:** no new persisted data shape, no new response payload --
  same match rows, just a subset shown in the DOM based on browser-local
  input.
  **Data-flow/security note (per the data-flow watchlist):** no new
  concern. Nothing new leaves the browser or gets persisted; the filter
  term itself never leaves the client (no request carries it).
- **Tests:** new `test_results_filter_narrows_and_restores_rows`
  (`tests/test_ui.py`), reusing the existing `live_server_match_streaming`
  fixture/two-distinct-pages fixture data -- types a substring matching
  only one row's `page_url`, confirms the table narrows to one row, types
  a non-matching term and confirms the table shows zero rows, then
  clears the input and confirms both rows return.
- **Implementation note:** the functional UI change described above was
  already present on `staging` (commit `15d292f`, merged via PR #82)
  before this issue's branch was cut from it -- this issue's own work
  was verifying that implementation against these acceptance criteria
  and adding the still-missing pieces the standing workflow requires:
  this report section, the README entry, and dedicated test coverage
  (PR #82 shipped with none of the three).

## Issue #91: keep the results filter search bar always visible

- **Inputs:** none -- pure follow-up to issue #86's UI, no new data.
- **Transformation:** `#results-filter-container` previously started
  `style="display: none;"` and `applyResultsFilter()` toggled it between
  `"block"`/`"none"` based on whether any matches existed at all
  (`hasAnyMatches`), with a further reset to `"none"` on every new crawl
  start. All three of those toggles are removed -- the container has no
  inline `display` at all now (defaults to its normal block layout) and
  is never hidden by JS. The results table's own visibility (hidden
  until there's at least one match) is untouched -- still driven by
  `currentMatches.length` in `applyResultsFilter()`, just no longer
  bundled with the search bar's visibility. The now-unused
  `resultsFilterContainer` DOM reference was removed along with its
  toggling code.
- **Outputs:** none -- still no new request/response payload, still
  entirely client-side.
  **Data-flow/security note (per the data-flow watchlist):** no new
  concern, same as issue #86 -- this only changes when an already-inert
  input element is shown, not what it does.
- **Tests:** new `test_results_filter_bar_always_visible`
  (`tests/test_ui.py`, reuses the `live_server_pausable` fixture) --
  asserts the search bar is visible before any crawl has run, while a
  crawl is running with zero matches found so far (table still hidden),
  and after the crawl finishes. Existing filter test
  (`test_results_filter_narrows_and_restores_rows`) still passes
  unchanged, confirming filtering behavior itself wasn't affected.

## Issue #87: re-verification of the cache/replay path (issue #72)

- **Inputs:** none new -- re-checks the existing cache/replay data path
  (`Repository.get_all_page_fetch_data()`, `app/crawler/replay.py`,
  `POST /crawls/{id}/re-extract`) against its own issue #72 acceptance
  criteria, from scratch, after several later PRs (#78, #80, #86, #91,
  and the direct-to-staging #83/#84/#85) touched adjacent code.
- **Finding -- a real regression, not just confirmation:** `/re-extract`
  and `_build_orchestrator()` (the live-crawl path too) were both
  completely broken by the pre-existing bug tracked as issue #93:
  `app/api/routes.py` called `JsCharCodeExtractor(...)` in both
  registry-construction blocks without importing it, so *every* call to
  either path raised `NameError` immediately -- not a subtle behavioral
  drift, a hard crash. Confirmed via the 5 tests already failing on
  `staging` (`test_re_extract_*` x3, `test_build_orchestrator_*` x2).
  Fixed here (single missing import line) since #87's own acceptance
  criteria can't be verified against code that doesn't run; this also
  resolves #93 as a side effect -- see PR description for the
  dedicated-issue bookkeeping.
- **Verified, with the fix in place:**
  - Stored snapshots (content, content_type, headers, cookies) round-trip
    correctly through `replay_extraction()` with zero network/browser
    calls -- already covered by existing `tests/test_replay.py` and
    `tests/test_sqlite_repository.py` (headers/cookies persistence +
    replay-data exclusion tests), all passing.
  - `/re-extract`'s registry construction (`app/api/routes.py`) mirrors
    `_build_orchestrator()`'s exactly -- same six extractors
    (`Html`, `CssJs`, `JsCharCode`, `ImageExif`, `ImageOcr`,
    `BinaryFallback`), same `context_chars` source (the crawl's own
    `state.context_chars` / `request.context_chars`) -- so results are
    structurally consistent with a fresh live crawl of the same data.
  - No regressions from `Repository`/`Orchestrator` changes in #78/#80/
    #86/#91 -- none of them touch the replay/re-extract code paths;
    confirmed by the full suite passing (215 passed, 4 skipped -- OCR
    tests skip without a local Tesseract install).
- **New coverage added:** `test_re_extract_finds_js_charcode_obfuscated_password`
  (`tests/test_api_routes.py`) -- a password only present as a JS
  char-code array/`fromCharCode` call, findable exclusively via
  `JsCharCodeExtractor`, run through the *real* `/re-extract` endpoint
  (not a hand-built registry). This is the specific coverage gap that
  let #93 ship unnoticed: every existing re-extract test used plain HTML
  text, which every other extractor already covers, so none of them
  actually proved `JsCharCodeExtractor` was reachable through the
  route's registry construction.
- **Outputs / data-flow:** unchanged from #72's original section --
  same at-rest trust boundary for headers/cookies, no new exposure.

## Issue #88: re-verification of PaginationGuard (issue #78) -- a real coverage regression

*(Restored 2026-08-30: this section was lost from `staging` when PR #97
merged less than a minute after PR #95, apparently overwriting it rather
than appending alongside it -- the code fix itself was never affected,
confirmed intact in `app/crawler/orchestrator.py`/`pagination_guard.py`
throughout. Content below is the original section, unchanged.)*

- **Inputs:** none new -- re-checks `PaginationGuard` (`app/crawler/
  pagination_guard.py`) and its wiring into `Orchestrator._process_url`
  (`app/crawler/orchestrator.py`) against issue #78's own acceptance
  criteria, from scratch.
- **Finding -- a real regression, reported directly by the user:** a
  real-target crawl's coverage dropped from roughly 680 pages to roughly
  480 after #78 shipped. Root cause: #78's fix keyed a pagination
  family's productivity streak on `new_matches` alone (deliberately, to
  defeat an adversarial family that always discovers a "new" next-page
  link) -- but that wrongly treats an index/listing family as
  unproductive whenever the index pages themselves carry no password
  directly, which is the overwhelmingly common real shape: a listing
  page links out to individual content pages, and only those carry the
  secret. `PaginationGuard.is_stopped()` gates every future fetch in
  that family once tripped (`Orchestrator`'s worker `continue`s past it
  without ever fetching), so once a legitimate index family got marked
  unproductive after just `pagination_family_limit` (10 by default)
  pages, every link its later pages would have surfaced was silently
  and permanently lost -- not delayed, dropped.
- **Fix:** `PaginationGuard.record()` gains a second, independent
  productivity signal, `new_external_links` -- links discovered on this
  page leading somewhere *other than* this same pagination family. The
  streak now resets on `new_matches or new_external_links`, not matches
  alone. Crucially, a same-family link (e.g. this page's own "next page"
  link) is excluded from that count entirely, so the original #78 defeat
  (ordinary sequential pagination always "discovers" its own next link,
  trap or not) still holds -- `Orchestrator._process_url` now partitions
  every discovered link (DOM + network + click-interaction) by
  `pagination_family_key()` before adding to the frontier, feeding only
  the cross-family count into the guard. `max_family_pages` (the
  unconditional hard ceiling, independent of the streak) is also raised
  from 50 to 200, extra headroom for a legitimate large index now that
  the streak logic itself is no longer the primary driver of premature
  cutoff.
- **Verified:** existing #78 regression tests
  (`test_pagination_guard_terminates_an_adversarial_trap_that_always_looks_productive`,
  `test_pagination_guard_stops_a_runaway_family_but_still_finds_real_content`)
  still pass unchanged -- their fixtures never produce cross-family
  links, so the adversarial-trap defense is intact. New test
  `test_pagination_guard_does_not_cut_off_a_legitimate_index_with_no_direct_matches`
  (`tests/test_orchestrator.py`) simulates the reported shape directly:
  20 index pages (more than the default `pagination_family_limit` of
  10), each with zero direct matches, each linking to one brand-new
  content page that does have a match, using the orchestrator's actual
  shipped defaults (no test-only override) -- confirmed this test fails
  against the pre-fix code (`10 == 20` -- exactly the class of loss
  reported) and passes with the fix. Two new `PaginationGuard`-level
  unit tests in `tests/test_pagination_guard.py` cover the
  `new_external_links` signal in isolation.
- **Not verified against the real target:** as with every crawler-level
  fix in this project's history, the exact 680-vs-480 numbers can't be
  reproduced in this session (no target URL/credentials available) --
  the fix is verified against a synthetic fixture that reproduces the
  same *mechanism* (an index family with no direct matches linking out
  to real content), not the real site itself.
- **Outputs / data-flow:** no new data captured or persisted -- purely a
  crawl-completeness fix. No data-flow/security concerns.

## Issue #96: remove the "Context length" field

- **Inputs:** removes one -- `context_chars` is no longer accepted on
  `POST /crawls` (`CrawlRequest`) or read from the UI form. Direct user
  request: the per-crawl setting wasn't useful in practice.
- **Transformation:** every extractor-construction call site in
  `app/api/routes.py` (`_build_orchestrator()` and `/re-extract`, six
  `registry.register(...)` calls plus `HeaderCookieExtractor` each) now
  passes a fixed module constant `_CONTEXT_CHARS = 80` (the field's old
  default) instead of a per-request value. `_CrawlState` no longer
  carries a `context_chars` attribute -- there's only one value now, so
  `/re-extract` doesn't need to remember what the original crawl used.
  `Settings.context_chars`/`CONTEXT_CHARS` removed too (already the last
  consumer, per the existing note that the REST API path doesn't read
  `Settings` for url/credentials either). The extractors' own
  `context_chars` constructor parameter is unchanged -- that's an
  internal implementation detail every extractor already had, not the
  user-facing setting being removed here.
- **Outputs:** `PasswordMatch.context_before`/`context_after` are
  produced exactly as before, just always at the one fixed width instead
  of a caller-chosen one. No data-flow/security change -- if anything,
  one fewer user-controlled value reaching the backend.
- **Removed:** the "Context length" label/input from
  `app/static/index.html`'s form, and the corresponding
  `context_chars: Number(...)` line building the `POST /crawls` body.

## Issue #99: static-asset completeness & verification scanner

- **Inputs:** none new from the caller -- reuses data the primary crawl
  already produces: every stored page's raw content/content_type
  (`Repository.get_all_page_fetch_data()`, issue #72) and the set of
  URLs already fetched (`Repository.get_visited_urls()`).
- **Why:** structural link discovery (`BrowserFetcher`'s rendered-DOM
  links, captured network requests, and click-interaction discovery,
  issues #5/#67) can still miss a `/static/...` asset that's only ever
  referenced as a string literal -- built up in JS and never actually
  requested during the crawl's own page loads (a conditional/lazy code
  path, a URL only used by a feature the crawl's automated clicks never
  triggered). This is a safety net specifically for that gap, not a
  replacement for structural discovery.
- **Transformation:** new `app/crawler/asset_scanner.py`:
  - `find_static_asset_references(text)` -- combines tag-attribute
    extraction (`<script src>`, `<link href>`, `<img src>`,
    `<source src>`, `<embed src>`) with a fallback whole-text regex
    (`/static/[a-zA-Z0-9_\-./]+(?:\?...)?`) that also catches a
    reference used purely in JS (e.g. `fetch('/static/x.json')`), never
    present as a real tag attribute at all. This project has no HTML
    parser dependency (BeautifulSoup/lxml) -- follows the existing
    lightweight regex-over-raw-source style already used by
    `HtmlExtractor`/`CssJsExtractor` rather than adding one.
  - `MasterAssetRegistry` -- tracks every discovered asset URL with
    `status` (fetched/pending/failed), `content_type`, `origin_page`.
    An `asyncio.Lock`, not a threading lock, matching `Orchestrator`'s
    own shared-state pattern (this codebase has no real threads in the
    crawl path).
  - `audit_static_assets()` -- scans every stored page whose
    content_type looks text-like, resolves each raw reference against
    its origin page's URL (`urljoin`), diffs the resulting set against
    `get_visited_urls()`, and fetches whatever's missing through the
    crawl's own `HttpFetcher` (Basic Auth included, same as any other
    page), `ExtractorRegistry`, and `HeaderCookieExtractor` -- so any
    extractor registered today (or added later, e.g. issue #98's
    deobfuscation processors once built) is automatically applied to a
    recovered asset, no special-casing. A failed fetch is marked
    `failed` and skipped, same per-URL isolation as the primary crawl's
    own failure handling -- never aborts the audit.
  - Wired into `Orchestrator.run()` as an automatic final phase, but
    *only* when the frontier genuinely emptied (`queue_empty`) -- an
    operator-bounded crawl (`stop()`, `max_pages`, `max_duration_seconds`)
    deliberately limited how much gets fetched, and this audit's own
    fetches would silently defeat that bound if it ran anyway.
- **Outputs:** new `StaticAssetCompletenessReport` (`app/models.py`):
  `total_pages_scanned`, `total_static_references_found`,
  `missing_assets_count`, `completeness_percentage`, and the full list
  of `AssetRecord`s. Attached to `CrawlSummary.asset_completeness`
  (`None` when the audit was skipped), so it flows through the existing
  `GET /crawls/{id}/report` and `CRAWL_FINISHED` WebSocket payload with
  no new endpoint needed. Any password found on a recovered asset is
  persisted via the same `Repository.save_page()` call every other page
  uses, and folded into the crawl's own `unique_passwords_found` count.
  Also logs `"Static-asset completeness scan: audited X pages for
  '/static/...' references -- found N missing assets."`
  **Data-flow/security note (per the data-flow watchlist):** no new
  exposure surface -- a recovered asset's content/matches go through the
  exact same storage and extractor pipeline as any other page, same
  trust boundary. `missing_assets_count`/`completeness_percentage`
  describe the *primary* crawl's coverage specifically (the gap before
  this audit's own remediation), not whether the remediation fetch
  itself succeeded -- a deliberate choice so the metric answers "how
  complete was structural discovery," not "how complete is the crawl
  after this safety net ran," which would always trend toward 100%
  regardless of how much the primary crawl actually missed.
- **Tests:** new `tests/test_asset_scanner.py` (7 tests) -- the
  tag-attribute and fallback-regex extraction patterns in isolation,
  including the case a real `<script src>`-only pattern would miss (a
  reference built inside a JS string). New end-to-end orchestrator test
  `test_static_asset_audit_finds_a_password_on_an_asset_never_
  structurally_discovered` (`tests/test_orchestrator.py`) -- an asset
  present only as a JS string in SEED's body, never in `dom_links`/
  `network_urls`/`interaction_urls`, is still fetched by the audit and
  its password recovered and persisted. A second new test confirms the
  audit is skipped when `max_pages` ends the crawl early. Full suite:
  227 passed, 4 skipped (OCR/Tesseract). ruff/black/mypy clean.
- **Not implemented / explicitly out of scope:** concurrent fetching of
  missing assets during the audit (sequential, like the rest of this
  function) -- the audit is expected to be a small tail-end gap-fill,
  not the bulk of crawl work, so the added complexity of a bounded
  semaphore wasn't judged worth it here; revisit if a real target turns
  out to have a large number of missed assets. `resources_checked`/
  `pages_visited` on `CrawlSummary` are unchanged by the audit (left
  scoped to the primary worker loop, as before) -- audit-specific counts
  live entirely in `StaticAssetCompletenessReport` instead, to avoid
  redefining what those two existing fields have always meant.

## Issue #101: multi-layer image analysis pipeline (metadata + structural + visual OCR + LSB)

*(Issue #101 was expanded mid-implementation from its original scope --
structural JPEG/PNG parsing only -- into a unified multi-layer pipeline,
reusing this issue/branch/PR rather than forking a new one, per explicit
user instruction to check for and reuse related open work first. What
follows is Layer 1 (this issue's original content, unchanged) followed
by a Layer 2/3 addendum.)*

### Layer 1: structural metadata (this issue's original scope)

- **Already covered, deliberately not re-touched:** `ImageExifExtractor`
  (issue #12 + PR #65/#84) already reads JPEG APP1/EXIF via Pillow --
  IFD0, the nested Exif sub-IFD, GPS IFD, and UTF-16LE/BE decoding
  specifically for `UserComment`'s charset-prefixed value.
  `BinaryFallbackExtractor` (issue #66) already runs a raw `latin-1`
  scan over every image's bytes. Re-implementing JPEG APP1/TIFF-IFD
  parsing by hand for this issue would duplicate complex,
  already-correct logic for no new coverage -- this extractor
  deliberately skips APP1 entirely.
- **Real, previously-unreachable gap:** a JPEG `COM` segment encoded in
  something other than plain ASCII (a raw `latin-1` scan can't see a
  UTF-16 value -- the interleaved null bytes break the password regex's
  contiguous-character match even though the bytes are right there), and
  a PNG `zTXt` chunk (or an `iTXt` chunk with its compression flag set)
  -- these are **zlib-compressed**, completely invisible to any
  plain-text scan, raw fallback included, until actually decompressed.
- **Transformation:** new `app/extractors/image_structural.py` ->
  `ImageStructuralExtractor`, registered into `ExtractorRegistry`
  alongside `ImageExifExtractor` (both call sites: `_build_orchestrator`
  for a live crawl and the `/re-extract` replay path in
  `app/api/routes.py` -- issue #93 was exactly a registration gap
  between these two call sites).
  - `_parse_jpeg_com_segments()` walks JPEG markers from SOI, extracting
    every `COM` (`0xFFFE`) segment's payload; stops at SOS/EOI (COM
    always precedes SOS) rather than attempting to walk entropy-coded
    scan data. A malformed/truncated marker stream just stops parsing
    early instead of reading garbage.
  - `_parse_png_text_chunks()` walks the chunk stream after the 8-byte
    signature (4-byte length, 4-byte type, data, 4-byte CRC -- CRC not
    verified, not needed for extraction); `tEXt` splits on the first NUL
    for keyword/text, `zTXt` additionally `zlib.decompress()`s the
    payload after its compression-method byte, `iTXt` parses
    keyword/compression-flag/compression-method/language-tag/
    translated-keyword/text and decompresses `text` when the
    compression flag is set.
  - `_decode_all_encodings()` tries UTF-16LE, UTF-16BE, UTF-8, and ASCII
    (the four named in the request) against every extracted payload,
    stripping a leading BOM first if present -- same "try every
    plausible encoding, let `find_passwords()` sort out which one
    actually matches" approach `ImageExifExtractor`'s own
    `_decode_exif_candidates()` already uses for `UserComment`.
  - Reuses `SourceType.IMAGE_METADATA` (already used by
    `ImageExifExtractor`) rather than adding a new enum value -- same
    category (container-format metadata, not pixel content), so the
    UI's snapshot-viewer fallback path (`NON_TEXT_SOURCE_TYPES` in
    `app/static/index.html`) already handles it with zero frontend
    changes needed. `locator` values: `jpeg:COM`,
    `png:{tEXt,zTXt,iTXt}:{keyword}`.
  - Any parse failure (malformed image, truncated chunk) is caught and
    logged at debug level, degrading to whatever matches were already
    found (often none) rather than raising -- same defensive pattern as
    every existing extractor. A structural hit logs a summary line:
    chunk types found, encodings tried, flags extracted.
- **Outputs:** `PasswordMatch` rows shaped like any other extractor's --
  same storage, REST/WebSocket delivery, snapshot-viewer handling.
  **Data-flow/security note (per the data-flow watchlist):** no new
  exposure surface -- same trust boundary as the existing EXIF/binary
  extraction this complements; worth noting as another concrete example
  (alongside OCR, issue #85) of why the extractor set must keep being
  treated as partial, since a zlib-compressed metadata chunk is a
  reasonable thing for a site to assume is safe from a naive scanner.
- **Tests:** new `tests/test_image_structural_extractor.py` (11 tests)
  -- real JPEG/PNG fixtures built via Pillow (`Image.save(...,
  comment=...)` for JPEG COM, `PngInfo`/`add_text`/`add_itxt` with
  `zip=True` for PNG chunks, confirming the `zTXt`/compressed-`iTXt`
  fixtures are genuinely compressed -- the plaintext password bytes are
  asserted absent from the raw file before extraction). A UTF-16-encoded
  `tEXt` chunk (adversarial vs. the PNG spec's own Latin-1 mandate,
  which Pillow's own writer can't produce) is built by hand to prove the
  decode-trial actually matters. Malformed-input tests confirm graceful
  degradation. Full suite: 238 passed, 4 skipped (OCR/Tesseract).
  ruff/black/mypy clean.
- **Not implemented / explicitly out of scope:** PNG `iCCP` (compressed
  ICC profile) and other non-text ancillary chunks -- not text-shaped,
  a password hidden there would need a different search strategy
  entirely (the profile's own binary structure, not free text) and
  wasn't part of the request. JPEG APP1/EXIF, as noted above, is
  entirely out of scope here by design, not oversight.

### Layer 2: visual OCR -- already shipped, no new work

`ImageOcrExtractor` (`app/extractors/image_ocr.py`, a direct-to-staging
feature predating a tracked issue number) already runs Tesseract OCR
over every `image/*` asset -- PNG, JPEG, WebP, anything Pillow can open,
not format-restricted -- and searches whatever text it recognizes.
Grayscale/threshold/contrast preprocessing before OCR is a known,
explicitly-deferred future enhancement (noted in that extractor's own
code comments) -- not added here since nothing in this request
demonstrated an actual need for it against a real image.

### Layer 3: LSB steganography -- new work

- **Inputs:** same as every image extractor -- raw image bytes +
  content_type + url. No new data captured from the caller.
- **Why:** every other extractor -- EXIF, structural chunks, OCR --
  reads text the image *carries*: metadata fields or rendered pixels.
  Classic LSB steganography hides a message by overwriting only the
  least-significant bit of each color-channel byte, a change too small
  to see -- the message exists nowhere as text at all, only in the
  low-order bits of the raw pixel data itself. None of the other layers
  can find this.
- **Transformation:** new `app/extractors/image_lsb.py` ->
  `ImageLsbExtractor`, registered alongside the other image extractors
  in both `_build_orchestrator()` and `/re-extract`
  (`app/api/routes.py`, the #93 lesson again). Extraction order is
  fixed and documented so results are reproducible: the image is
  converted to RGBA (so every pixel always has all four channels, even
  if the source had none -- a synthesized alpha channel is uniformly
  255, contributing no real signal but not corrupting the R/G/B bits
  either), `Image.tobytes()` gives a flat byte string in row-major,
  per-pixel R,G,B,A order, and the LSB of each byte -- in that same
  order -- becomes one bit of the reconstructed message, packed
  MSB-first into bytes 8 at a time. Reassembled bytes are decoded as
  `latin-1` (never raises) and run through the same shared
  `find_passwords()` every other extractor uses.
  **False-positive risk:** for an ordinary image, the low-order bits of
  real pixel data are effectively noise -- decoding noise as text and
  matching a specific 16-hex-character pattern is astronomically
  unlikely, so no extra heuristics were needed to keep this safe. JPEG's
  lossy compression would generally destroy a real LSB-embedded message
  before this extractor ever saw it -- not specifically guarded against
  (harmless either way: it just finds nothing on a lossy-recompressed
  carrier, the correct outcome), consistent with not format-restricting
  any extractor in this codebase.
  New `SourceType.IMAGE_LSB` (`app/models.py`) marks these matches --
  a distinct pixel-level technique, not container metadata, so it gets
  its own value the same way `IMAGE_OCR`/`JS_CHARCODE` did rather than
  folding into `IMAGE_METADATA`. Added to `NON_TEXT_SOURCE_TYPES` in
  `app/static/index.html` so the UI's snapshot viewer falls back to a
  locator+context display instead of trying to search a non-text body,
  same as the other three non-text source types.
- **Outputs:** `PasswordMatch` rows shaped like any other extractor's --
  `locator` is `lsb:offset:N`, the byte offset into the *reassembled*
  message, not a pixel coordinate (recovering an actual pixel position
  would mean tracking which channel/pixel each bit came from during
  reconstruction, not done here since nothing consumes it yet -- noted
  as a possible future enhancement, not a defect).
  **Data-flow/security note (per the data-flow watchlist):** no new
  exposure surface -- same storage/REST/WebSocket path as every other
  extractor. Worth flagging as another concrete argument for why the
  extractor set must stay "assumed partial" (alongside OCR, structural
  chunks): a site could reasonably believe pixel data itself is safe
  from a text-based scanner, and until this layer, that assumption held.
  Log line per image: `"LSB steganography DETECTED in <url> -- N
  flag(s) found."` at INFO on a hit, `"LSB steganography ruled out for
  <url>."` at DEBUG otherwise -- DEBUG specifically because "ruled out"
  is the outcome for nearly every image processed, and logging that at
  INFO for every single image would flood the log for no operational
  value.
- **Tests:** new `tests/test_image_lsb_extractor.py` (5 tests) -- a
  reference encoder (matching the extractor's exact bit order) embeds a
  real flag into a 100x100 image's pixel LSBs via Pillow, saved as a
  lossless PNG, and the extractor recovers it byte-for-byte; a plain
  unmodified image proves no false positive; a source image with no
  alpha channel of its own still round-trips correctly through the
  RGBA conversion both sides use; malformed/non-image input degrades to
  no matches without raising. Full suite: 243 passed, 4 skipped
  (OCR/Tesseract). ruff/black/mypy clean.
- **Not implemented / explicitly out of scope:** bit-plane analysis
  beyond the single least-significant bit (some stego tools use 2+ bits
  per channel, or only certain channels, or a non-sequential pixel
  order/key-derived permutation) -- the spec asked specifically for LSB
  across R/G/B/A in sequence, which is what's implemented; a more
  exhaustive multi-bit-plane/permutation search would be a much larger,
  separate feature. Recovering a pixel-coordinate locator (vs. a
  message-byte offset) is also deferred, noted above.

## Issue #103: cookie, redirect, and content-negotiation probing module

- **Confirmed gaps, checked before filing:** `HttpFetcher`
  (`app/crawler/fetcher.py`) sets `follow_redirects=True` and returned
  only the *final* response -- every intermediate redirect hop's
  `Location` header/status was silently discarded
  (`httpx.Response.history` existed but was never read).
  `BrowserFetcher` never evaluated `document.cookie`/`localStorage`/
  `sessionStorage` in-page. No content-negotiation probing (re-requesting
  with alternate `Accept`/`X-Requested-With` headers) existed anywhere.
- **Architectural note that shaped scope:** `BrowserFetcher` creates a
  fresh, isolated browser context per page fetch
  (`_new_authenticated_context()`), deliberately, for isolation/
  simplicity -- there is no persistent cross-page browser session in
  this design. A whole-crawl "storage state before vs. after" diff
  doesn't fit that without a much larger context-lifecycle change, so
  this is scoped to a **per-page storage snapshot** instead (captured at
  each page's own load, same lifecycle as its DOM links) -- still audits
  every page's client-side storage, just not as one before/after diff.
- **Transformation, three independent additions:**
  1. **Redirects** -- `FetchResult` (`app/crawler/fetcher.py`) gains
     `redirect_history: list[RedirectHop]`, built from
     `httpx.Response.history` (url/status_code/location per hop). New
     `RedirectExtractor` (`app/extractors/redirect_chain.py`, same
     differently-shaped-input pattern as `HeaderCookieExtractor`, not
     routed through `ExtractorRegistry`) scans each hop's `Location`
     header (its query string is part of the same string, so no
     separate parsing needed) against the flag regex. Wired directly in
     `Orchestrator._process_url`, folded into that page's own `matches`.
     Also logs a line per URL with a non-empty redirect chain: hop count.
  2. **Client-side storage** -- `BrowserFetchResult` gains `cookies:
     str`, `local_storage: dict[str, str]`, `session_storage: dict[str,
     str]`, captured via a new `page.evaluate()` call right after
     `goto()` in `BrowserFetcher._load()` (degrades to an empty snapshot
     on any evaluation error, e.g. a sandboxed frame -- never fails the
     page fetch). New `ClientStorageExtractor`
     (`app/extractors/client_storage.py`, same pattern) scans all three
     against the flag regex, wired the same way, into the `is_html`
     branch of `_process_url` right after the browser fetch.
  3. **Content negotiation** -- new
     `app/crawler/content_negotiation.py::probe_content_negotiation()`,
     a post-crawl audit phase mirroring issue #99's `audit_static_assets`
     pattern exactly: only runs when `queue_empty` (an operator-bounded
     early stop must not be silently defeated by extra probing fetches).
     Re-requests a bounded, representative sample (default 5, `sample_
     size` param) of already-crawled HTML pages via `HttpFetcher.fetch()`
     (which gained an `extra_headers` parameter for this) with three
     fixed header sets (`Accept: application/json`, `Accept: text/
     plain`, `X-Requested-With: XMLHttpRequest`), scanning both the
     response body and every response header value. Any match found has
     no natural "page" to attach a stored snapshot to -- it's a probe of
     an *existing* URL, and reusing that URL for `save_page()` would
     clobber the primary crawl's own stored snapshot for it -- so it's
     persisted via `Repository.save_match()`, the standalone-match path
     that method was built for but, until this issue, nothing in
     production code actually called. Logs per probe (hit or miss) and
     a final summary line.
- **Outputs:** new `SourceType.CLIENT_STORAGE`/`REDIRECT`/
  `CONTENT_NEGOTIATION` values (each a genuinely distinct vantage point,
  matching how `IMAGE_OCR`/`IMAGE_LSB` each got their own value), added
  to `NON_TEXT_SOURCE_TYPES` in `app/static/index.html` -- none of these
  values are present in a crawled page's own stored snapshot body, so
  the snapshot viewer's locator+context fallback applies to all three.
  New `ContentNegotiationReport` (`app/models.py`, mirroring
  `StaticAssetCompletenessReport`'s shape) attached to
  `CrawlSummary.content_negotiation` -- flows through the existing
  `GET /report` and WebSocket payload, no new endpoint.
  **Data-flow/security note (per the data-flow watchlist):** two
  genuinely new categories of captured data, flagged explicitly per the
  issue's own acceptance criteria: client-side storage (cookies/
  localStorage/sessionStorage values) and redirect `Location` headers
  are now read and, if they contain a match, persisted -- same trust
  boundary as existing snapshot/header/cookie storage (issue #72's
  headers/cookies note already established this precedent), not a new
  risk category, but a new *source* of at-rest data worth naming
  explicitly. Redirect/storage/probe data itself (the full snapshot, not
  just an extracted match) is never persisted wholesale -- only
  `PasswordMatch` rows are, same as every other extractor.
- **`/re-extract` (issue #72) wiring -- deliberately excluded, not an
  oversight:** none of these three mechanisms are wired into the replay
  path. `PageFetchData` (what `/re-extract` replays from) has no
  redirect-history or client-storage field -- that data was never
  persisted in the first place, so there's nothing to replay it from
  without a much larger storage-schema change. The content-negotiation
  probe is fundamentally a *live* re-fetch with different headers, which
  would violate `/re-extract`'s whole design principle (issue #72:
  "zero network/browser calls"). All three require live network/browser
  access every time, unlike the passive body-content extractors that
  are.
- **Tests:** new `tests/test_redirect_chain_extractor.py` (5),
  `tests/test_client_storage_extractor.py` (6),
  `tests/test_content_negotiation.py` (7) -- unit-level for each
  mechanism in isolation, including a failed-probe-doesn't-abort-the-
  rest case and a sample-size-bounds-page-count case. The existing
  end-to-end test (`tests/test_e2e_crawl.py`, a real Orchestrator/
  HttpFetcher/BrowserFetcher/repository against a real local server) now
  also exercises all three live -- its `EXPECTED_SOURCE_TYPES` needed
  updating since the fixture server has no real negotiation logic (same
  body regardless of `Accept`), so the probe legitimately, if
  incidentally, re-finds several already-planted passwords under
  `CONTENT_NEGOTIATION` too, and the fixture's non-`HttpOnly`
  Set-Cookie-planted password is also readable via `document.cookie` in
  the browser (`CLIENT_STORAGE`) -- both are correct, expected discovery
  via an additional vantage point, same accepted pattern as the existing
  EXIF+BINARY dual-reporting case in that same test. Full suite: 261
  passed, 4 skipped (OCR/Tesseract). ruff/black/mypy clean.

