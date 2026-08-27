"""Async orchestrator wiring the frontier, fetchers, extractors, and
repository into a full crawl.

For each URL popped from the frontier: fetch it over HTTP, run the
body-content extractors and the header/cookie extractor against the
result, persist the page and its matches, and -- for HTML pages -- also
fetch it with the browser fetcher to discover JS-driven links (rendered
DOM `<a href>`s, URLs seen in network traffic during load, and URLs only
revealed by actually clicking a non-anchor interactive control) and
enqueue them. Runs with a bounded concurrency (`asyncio.Semaphore`).

Completion is the frontier actually emptying (`queue_empty` on the
returned `CrawlSummary`), not an arbitrary page count -- `max_pages`
defaults to `None` (issue #71: a fixed cap was silently truncating real
crawls well before the frontier emptied, and there's no way to know the
right number for an unknown site up front, so guessing one is no longer
the default). `max_pages` still exists, opt-in, as a hard ceiling for an
operator who wants one; `max_duration_seconds` (also opt-in, `None` by
default) is a wall-clock alternative that bounds total run cost
independent of how many distinct URLs a pathological site can generate --
neither is needed for a normal, finite same-origin site to terminate
correctly, since `UrlFrontier`'s same-origin filter and once-only dedup
already make the URL space finite on its own.

If an `EventBus` is supplied, publishes `PAGE_FETCHED` after each page is
saved, `MATCH_FOUND` for each match found on it, and `CRAWL_FINISHED` with
the final `CrawlSummary` once the run completes -- see issue #16.

Resumes automatically: at the start of `run()`, every URL the `Repository`
already has a page for (from a previous, crashed run against the same
database) is marked visited on the frontier so it isn't re-fetched. A
single URL's processing failing (e.g. a genuine HTTP redirect loop that
makes the browser fetcher's navigation fail) is caught and skipped rather
than aborting or hanging the whole crawl. A `PaginationGuard` stops
following a `?page=N`-style URL family once it's gone several consecutive
pages with no new links or matches, so a runaway/trap pagination family
can't run forever on its own even with no `max_pages` cap in play.

`pause()`/`resume()`/`stop()` (issue #68) give external control over an
in-flight `run()`: `pause()` blocks every worker before it pops its next
URL (an in-flight fetch is never interrupted mid-request); `resume()`
un-blocks them; `stop()` makes every worker exit its loop the next time it
would otherwise pop a URL, so `run()` returns early with a summary over
whatever was actually processed -- the same `CRAWL_FINISHED` event/return
path as a normal completion, just early. None of the three touch
already-in-flight work.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.crawler.browser_fetcher import BrowserFetcher
from app.crawler.fetcher import HttpFetcher
from app.crawler.frontier import UrlFrontier
from app.crawler.pagination_guard import PaginationGuard
from app.events import CRAWL_FINISHED, MATCH_FOUND, PAGE_FETCHED, EventBus
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
        max_pages: int | None = None,
        max_duration_seconds: float | None = None,
        pagination_family_limit: int = 10,
        event_bus: EventBus | None = None,
    ) -> None:
        self._frontier = frontier
        self._http_fetcher = http_fetcher
        self._browser_fetcher = browser_fetcher
        self._extractor_registry = extractor_registry
        self._header_cookie_extractor = header_cookie_extractor
        self._repository = repository
        self._concurrency = concurrency
        self._max_pages = max_pages
        self._max_duration_seconds = max_duration_seconds
        self._pagination_guard = PaginationGuard(max_unproductive=pagination_family_limit)
        self._event_bus = event_bus
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # not paused by default
        self._stop_requested = False

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def stop(self) -> None:
        self._stop_requested = True
        self._pause_event.set()  # wake any paused worker so it can see the stop and exit

    async def run(self) -> CrawlSummary:
        started_at = datetime.now(timezone.utc)
        semaphore = asyncio.Semaphore(self._concurrency)
        lock = asyncio.Lock()
        state = {"resources_checked": 0, "pages_visited": 0}
        unique_values: set[str] = set()

        for visited_url in self._repository.get_visited_urls():
            self._frontier.mark_visited(visited_url)

        async def worker() -> None:
            while True:
                await self._pause_event.wait()

                async with lock:
                    if self._stop_requested:
                        return
                    if (
                        self._max_pages is not None
                        and state["resources_checked"] >= self._max_pages
                    ):
                        return
                    if self._max_duration_seconds is not None:
                        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
                        if elapsed >= self._max_duration_seconds:
                            return
                    if not self._frontier.has_next():
                        return
                    url = self._frontier.next()
                    if self._pagination_guard.is_stopped(url):
                        continue

                async with semaphore:
                    try:
                        is_html, match_values = await self._process_url(url)
                    except Exception:
                        # A single URL failing (e.g. the browser fetcher's
                        # navigation erroring out on a genuine HTTP
                        # redirect loop) must not abort or hang the crawl.
                        continue

                async with lock:
                    state["resources_checked"] += 1
                    if is_html:
                        state["pages_visited"] += 1
                    unique_values.update(match_values)

        workers = [asyncio.create_task(worker()) for _ in range(self._concurrency)]
        await asyncio.gather(*workers)

        summary = CrawlSummary(
            pages_visited=state["pages_visited"],
            resources_checked=state["resources_checked"],
            unique_passwords_found=len(unique_values),
            queue_empty=not self._frontier.has_next(),
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )
        if self._event_bus is not None:
            self._event_bus.publish(CRAWL_FINISHED, summary)
        return summary

    async def _process_url(self, url: str) -> tuple[bool, list[str]]:
        fetch_result = await self._http_fetcher.fetch(url)
        content_type = fetch_result.content_type or ""

        matches = list(self._extractor_registry.run_all(fetch_result.content, content_type, url))
        matches.extend(
            self._header_cookie_extractor.extract(fetch_result.headers, fetch_result.cookies, url)
        )

        is_html = content_type.startswith("text/html")
        new_links = 0
        if is_html:
            browser_result = await self._browser_fetcher.fetch(url)
            new_links += self._frontier.add_many(browser_result.dom_links)
            new_links += self._frontier.add_many(browser_result.network_urls)
            new_links += self._frontier.add_many(browser_result.interaction_urls)

        self._pagination_guard.record(url, new_links=new_links, new_matches=len(matches))

        page = PageResult(
            url=url,
            status_code=fetch_result.status_code,
            fetched_at=datetime.now(timezone.utc),
            matches=matches,
        )
        self._repository.save_page(
            page,
            snapshot=fetch_result.content,
            content_type=fetch_result.content_type,
            headers=fetch_result.headers,
            cookies=fetch_result.cookies,
        )

        if self._event_bus is not None:
            self._event_bus.publish(PAGE_FETCHED, page)
            for match in matches:
                self._event_bus.publish(MATCH_FOUND, match)

        return is_html, [match.value for match in matches]
