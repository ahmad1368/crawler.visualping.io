import asyncio
import sqlite3

from app.crawler.frontier import UrlFrontier
from app.crawler.orchestrator import Orchestrator
from app.crawler.fetcher import FetchResult
from app.crawler.browser_fetcher import BrowserFetchResult
from app.extractors.base import ExtractorRegistry
from app.models import PasswordMatch, SourceType
from app.storage.sqlite import SqliteRepository

SEED = "https://example.com/"
PAGE_A = "https://example.com/a"
PAGE_B = "https://example.com/b"


def run(coro):
    return asyncio.run(coro)


class FakeHttpFetcher:
    def __init__(self, responses: dict[str, FetchResult]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        return self._responses[url]


class FakeBrowserFetcher:
    def __init__(self, responses: dict[str, BrowserFetchResult]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def fetch(self, url: str) -> BrowserFetchResult:
        self.calls.append(url)
        return self._responses.get(url, BrowserFetchResult(html="", dom_links=[], network_urls=[]))


class ConstantValueExtractor:
    """Dummy Extractor (issue #8 Protocol) returning one fixed-value match per call."""

    def __init__(self, value: str) -> None:
        self._value = value

    def extract(self, content: bytes, content_type: str, url: str):
        return [
            PasswordMatch(
                value=self._value,
                source_type=SourceType.HTML_TEXT,
                source_url=url,
                context_before="",
                context_after="",
                locator="x",
            )
        ]


class NoOpHeaderCookieExtractor:
    def extract(self, headers, cookies, url):
        return []


def _html_response() -> FetchResult:
    return FetchResult(
        content=b"<html></html>",
        content_type="text/html",
        status_code=200,
        headers={},
        cookies={},
    )


def _build_orchestrator(
    fetch_responses,
    browser_responses,
    match_value="VISUALPING{abcdef1234567890}",
    concurrency=2,
    max_pages=100,
):
    frontier = UrlFrontier(SEED)
    http_fetcher = FakeHttpFetcher(fetch_responses)
    browser_fetcher = FakeBrowserFetcher(browser_responses)
    registry = ExtractorRegistry()
    registry.register(ConstantValueExtractor(match_value))
    repository = SqliteRepository(sqlite3.connect(":memory:"))

    orchestrator = Orchestrator(
        frontier=frontier,
        http_fetcher=http_fetcher,
        browser_fetcher=browser_fetcher,
        extractor_registry=registry,
        header_cookie_extractor=NoOpHeaderCookieExtractor(),
        repository=repository,
        concurrency=concurrency,
        max_pages=max_pages,
    )
    return orchestrator, http_fetcher, browser_fetcher, repository


def test_crawl_terminates_and_visits_all_linked_pages():
    fetch_responses = {SEED: _html_response(), PAGE_A: _html_response(), PAGE_B: _html_response()}
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=[PAGE_A, PAGE_B], network_urls=[]),
        PAGE_A: BrowserFetchResult(html="", dom_links=[], network_urls=[]),
        PAGE_B: BrowserFetchResult(html="", dom_links=[], network_urls=[]),
    }
    orchestrator, http_fetcher, _, _ = _build_orchestrator(fetch_responses, browser_responses)

    summary = run(orchestrator.run())

    assert sorted(http_fetcher.calls) == sorted([SEED, PAGE_A, PAGE_B])
    assert summary.pages_visited == 3
    assert summary.resources_checked == 3
    assert summary.queue_empty is True


def test_crawl_terminates_on_cyclic_links():
    fetch_responses = {SEED: _html_response(), PAGE_A: _html_response()}
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=[PAGE_A], network_urls=[]),
        PAGE_A: BrowserFetchResult(html="", dom_links=[SEED, PAGE_A], network_urls=[]),
    }
    orchestrator, http_fetcher, _, _ = _build_orchestrator(fetch_responses, browser_responses)

    summary = run(orchestrator.run())

    assert sorted(http_fetcher.calls) == sorted([SEED, PAGE_A])
    assert summary.queue_empty is True


def test_crawl_respects_max_pages_limit():
    urls = [SEED] + [f"https://example.com/p{i}" for i in range(10)]
    fetch_responses = {url: _html_response() for url in urls}
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=urls[1:], network_urls=[]),
        **{url: BrowserFetchResult(html="", dom_links=[], network_urls=[]) for url in urls[1:]},
    }
    orchestrator, http_fetcher, _, _ = _build_orchestrator(
        fetch_responses, browser_responses, concurrency=1, max_pages=3
    )

    summary = run(orchestrator.run())

    assert len(http_fetcher.calls) == 3
    assert summary.resources_checked == 3
    assert summary.queue_empty is False


def test_summary_deduplicates_unique_passwords_found():
    fetch_responses = {SEED: _html_response(), PAGE_A: _html_response()}
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=[PAGE_A], network_urls=[]),
        PAGE_A: BrowserFetchResult(html="", dom_links=[], network_urls=[]),
    }
    orchestrator, _, _, _ = _build_orchestrator(
        fetch_responses, browser_responses, match_value="VISUALPING{abcdef1234567890}"
    )

    summary = run(orchestrator.run())

    assert summary.unique_passwords_found == 1


def test_pages_are_persisted_to_repository():
    fetch_responses = {SEED: _html_response()}
    browser_responses = {SEED: BrowserFetchResult(html="", dom_links=[], network_urls=[])}
    orchestrator, _, _, repository = _build_orchestrator(fetch_responses, browser_responses)

    run(orchestrator.run())

    assert repository.get_snapshot(SEED) == b"<html></html>"
    assert repository.get_report().unique_passwords_found == 1
