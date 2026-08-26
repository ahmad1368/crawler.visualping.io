"""Async orchestrator wiring the frontier, fetchers, extractors, and
repository into a full crawl.

For each URL popped from the frontier: fetch it over HTTP, run the
body-content extractors and the header/cookie extractor against the
result, persist the page and its matches, and -- for HTML pages -- also
fetch it with the browser fetcher to discover JS-driven links and enqueue
them. Runs with a bounded concurrency (`asyncio.Semaphore`) and stops once
`max_pages` URLs have been processed or the frontier is empty, whichever
comes first.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.crawler.browser_fetcher import BrowserFetcher
from app.crawler.fetcher import HttpFetcher
from app.crawler.frontier import UrlFrontier
from app.extractors.base import ExtractorRegistry
from app.extractors.headers_cookies import HeaderCookieExtractor
from app.models import CrawlSummary, PageResult
from app.storage.repository import Repository


class Orchestrator:
    def __init__(
        self,
        frontier: UrlFrontier,
        http_fetcher: HttpFetcher,
        browser_fetcher: BrowserFetcher,
        extractor_registry: ExtractorRegistry,
        header_cookie_extractor: HeaderCookieExtractor,
        repository: Repository,
        concurrency: int = 4,
        max_pages: int = 100,
    ) -> None:
        self._frontier = frontier
        self._http_fetcher = http_fetcher
        self._browser_fetcher = browser_fetcher
        self._extractor_registry = extractor_registry
        self._header_cookie_extractor = header_cookie_extractor
        self._repository = repository
        self._concurrency = concurrency
        self._max_pages = max_pages

    async def run(self) -> CrawlSummary:
        started_at = datetime.now(timezone.utc)
        semaphore = asyncio.Semaphore(self._concurrency)
        lock = asyncio.Lock()
        state = {"resources_checked": 0, "pages_visited": 0}
        unique_values: set[str] = set()

        async def worker() -> None:
            while True:
                async with lock:
                    if state["resources_checked"] >= self._max_pages:
                        return
                    if not self._frontier.has_next():
                        return
                    url = self._frontier.next()

                async with semaphore:
                    is_html, match_values = await self._process_url(url)

                async with lock:
                    state["resources_checked"] += 1
                    if is_html:
                        state["pages_visited"] += 1
                    unique_values.update(match_values)

        workers = [asyncio.create_task(worker()) for _ in range(self._concurrency)]
        await asyncio.gather(*workers)

        return CrawlSummary(
            pages_visited=state["pages_visited"],
            resources_checked=state["resources_checked"],
            unique_passwords_found=len(unique_values),
            queue_empty=not self._frontier.has_next(),
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )

    async def _process_url(self, url: str) -> tuple[bool, list[str]]:
        fetch_result = await self._http_fetcher.fetch(url)
        content_type = fetch_result.content_type or ""

        matches = list(
            self._extractor_registry.run_all(fetch_result.content, content_type, url)
        )
        matches.extend(
            self._header_cookie_extractor.extract(
                fetch_result.headers, fetch_result.cookies, url
            )
        )

        is_html = content_type.startswith("text/html")
        if is_html:
            browser_result = await self._browser_fetcher.fetch(url)
            self._frontier.add_many(browser_result.dom_links)
            self._frontier.add_many(browser_result.network_urls)

        page = PageResult(
            url=url,
            status_code=fetch_result.status_code,
            fetched_at=datetime.now(timezone.utc),
            matches=matches,
        )
        self._repository.save_page(page, snapshot=fetch_result.content)

        return is_html, [match.value for match in matches]
