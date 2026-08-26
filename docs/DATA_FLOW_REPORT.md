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
├── HeaderCookieExtractor                        app/extractors/headers_cookies.py
│   (takes FetchResult.headers/.cookies directly -- not routed through
│    ExtractorRegistry.run_all(), since its input isn't a body blob; also
│    calls find_passwords() internally; tags matches http_header / cookie,
│    locator is "header:<name>"/"cookie:<name>")
│
└── ExtractorRegistry                            app/extractors/base.py
    (register() + run_all(content, content_type, url); dispatches to
     every registered body-content Extractor)
    │
    ├── HtmlExtractor                            app/extractors/html.py
    │   (text/html only; html.parser walks text nodes + <!-- --> comments,
    │    skips <script>/<style>; tags matches html_text / html_comment)
    │
    ├── CssJsExtractor                            app/extractors/css_js.py
    │   (text/css, application|text/javascript; scans the whole body as
    │    plain text -- content:, string literals, // and /* */ comments)
    │
    ├── ImageExifExtractor                        app/extractors/image_exif.py
    │   (image/* only; reads EXIF fields via Pillow -- UserComment,
    │    ImageDescription, etc.; locator is "exif:<field name>")
    │
    ├── BinaryFallbackExtractor                   app/extractors/binary_fallback.py
    │   (any content type NOT handled above; decodes as latin-1 -- never
    │    raises on non-UTF8 bytes -- and scans like `strings`;
    │    locator is "offset:<byte offset>")
    │
    └── find_passwords()                        app/matching.py
        (regex: VISUALPING\{16 lowercase hex\}; slices before/after context)
        └── RegexMatch
            (value, context_before, context_after, start, end)
            │
            └── PasswordMatch                   app/models.py
                (RegexMatch's value/context + source_type, source_url, locator)
                │
                └── PageResult                  app/models.py
                    (url, status_code, fetched_at, matches[])
                    │
                    └── CrawlSummary            app/models.py
                        (pages_visited, resources_checked, unique_passwords_found, ...)
                        │
                        ├── Storage (planned)    app/storage/
                        │   (SQLite repository -- durable store, gitignored *.db)
                        │
                        └── API (planned)        app/api/
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
