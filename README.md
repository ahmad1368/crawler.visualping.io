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
