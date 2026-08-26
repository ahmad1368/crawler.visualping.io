# crawler.visualping.io

Crawler + API + UI for detecting exposed passwords on crawled sites.

_Full documentation will be filled in by the docs issue._

## Implemented so far

- **#1** Project scaffold: `app/` package layout, pinned dependencies, `.gitignore`.
- **#2** `Settings` (pydantic-settings) loading `TARGET_URL`, Basic Auth credentials, and crawl tuning from `.env`.
- **#3** Core data models: `PasswordMatch`, `PageResult`, `CrawlSummary`.
- **#4** `HttpFetcher`: async HTTP fetch with Basic Auth and retry/backoff (httpx).
- **#5** `BrowserFetcher`: Playwright-based fetch with Basic Auth and network capture, for JS-rendered pages.
- **#6** `UrlFrontier`: URL queue + visited-set with normalization, same-origin filter, and cycle-safe dedupe.
- **#7** `find_passwords()`: `VISUALPING{16 hex}` regex matcher with before/after context extraction.
- **#8** `ExtractorRegistry`: strategy interface + registry for running all extractors against a fetch result.
- **#9** `HtmlExtractor`: finds passwords in HTML visible text and comments, tagged `html_text`/`html_comment`.
- **#10** `CssJsExtractor`: finds passwords in downloaded CSS/JS file bodies.
- **#11** `HeaderCookieExtractor`: finds passwords in HTTP response headers (including custom `X-*`) and cookies.
- **#12** `ImageExifExtractor`: finds passwords in image EXIF fields (e.g. `UserComment`) via Pillow.
- **#13** `BinaryFallbackExtractor`: catch-all scanner for any other content type, safe on arbitrary binary bytes.
- **#14** `SqliteRepository`: persists pages, matches, and raw content snapshots; `get_report()`/`get_snapshot()` read them back.
- **#15** `Orchestrator`: wires the frontier, fetchers, extractors, and repository into a concurrency-limited crawl loop.
- **#16** `EventBus`: in-process `subscribe()`/`publish()` pub/sub for `page_fetched`/`match_found`/`crawl_finished` events.
- **#17** REST API: `POST /crawls`, `GET /crawls/{id}/status`, `GET /crawls/{id}/report` -- runs each crawl as a background task.
- **#18** `WS /ws/crawls/{id}`: streams live crawl progress events as JSON, closing cleanly when the crawl finishes.
- **#19** Web UI (`GET /`): Visualping-branded input form + Run button + live progress log, wired to the REST/WebSocket API.
- **#20** Results table: renders `GET /crawls/{id}/report`'s matches (page, source type, password, context, count), with a clickable password cell.
- **#21** Snapshot viewer: clicking a password opens the raw page/resource with the match highlighted and scrolled into view (locator fallback for EXIF/binary).
- **#22** Completeness summary panel: pages visited, resources checked, unique passwords found, queue empty -- live during the crawl, finalized from the report.
