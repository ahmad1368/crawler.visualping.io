# Data Flow Report

This document tracks how data moves through the system, issue by issue. Each
issue that touches the crawl, extraction, storage, or API layers appends its
own section below describing: inputs -> transformation -> outputs, with a
focus on where credentials or extracted secrets travel.

## Data flow tree (overview)

Updated as each issue lands. Nodes marked `(planned)` don't exist in code
yet -- they show where today's outputs are headed.

```
.env / environment variables
└── Settings                                    app/settings.py
    (TARGET_URL, AUTH_USERNAME, AUTH_PASSWORD, CONTEXT_CHARS, CONCURRENCY, MAX_PAGES)
    │
    └── UrlFrontier                              app/crawler/frontier.py
        (queue + visited-set seeded from TARGET_URL; normalizes URLs,
         same-origin filter, dedupe prevents cyclic-link loops)
        │
        ├── HttpFetcher                          app/crawler/fetcher.py
        │   (httpx.AsyncClient + Basic Auth, retry/backoff)
        │   └── FetchResult
        │       (content, content_type, status_code, headers, cookies)
        │
        └── BrowserFetcher                       app/crawler/browser_fetcher.py
            (Playwright Chromium + http_credentials, network capture)
            └── BrowserFetchResult
                (html, dom_links, network_urls)

FetchResult / BrowserFetchResult
├── UrlFrontier.add_many(...)                    discovered links (dom_links,
│                                                 network_urls) feed back into the
│                                                 frontier's queue -- same-origin +
│                                                 dedup keeps the crawl bounded
│
└── Extractors (planned)                        app/extractors/
    (one strategy class per password-hiding technique: html, css, js,
     http_header, cookie, image_metadata, binary)
    └── PasswordMatch                           app/models.py
        (value, source_type, source_url, context_before, context_after, locator)
        │
        └── PageResult                          app/models.py
            (url, status_code, fetched_at, matches[])
            │
            └── CrawlSummary                    app/models.py
                (pages_visited, resources_checked, unique_passwords_found, ...)
                │
                ├── Storage (planned)            app/storage/
                │   (SQLite repository -- durable store, gitignored *.db)
                │
                └── API (planned)                app/api/
                    (REST + WebSocket -- surfaces results to the UI)
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
