import asyncio
import sqlite3
from datetime import datetime, timezone

from app.crawler.browser_fetcher import BrowserFetchResult
from app.crawler.fetcher import FetchResult
from app.crawler.frontier import UrlFrontier
from app.crawler.orchestrator import Orchestrator
from app.extractors.base import ExtractorRegistry
from app.models import PageResult, PasswordMatch, SourceType
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
        response = self._responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class FakeBrowserFetcher:
    def __init__(self, responses: dict[str, BrowserFetchResult]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def fetch(self, url: str) -> BrowserFetchResult:
        self.calls.append(url)
        default = BrowserFetchResult(html="", dom_links=[], network_urls=[])
        response = self._responses.get(url, default)
        if isinstance(response, Exception):
            raise response
        return response


class SelectiveExtractor:
    """Returns a fixed-value match only for the URLs listed, [] otherwise --
    for tests that need some pages to be "productive" and others not."""

    def __init__(self, value: str, urls_with_match: set[str]) -> None:
        self._value = value
        self._urls_with_match = urls_with_match

    def extract(self, content: bytes, content_type: str, url: str):
        if url not in self._urls_with_match:
            return []
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
    max_duration_seconds=None,
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
        max_duration_seconds=max_duration_seconds,
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


def test_max_pages_defaults_to_none_so_a_large_crawl_is_not_capped():
    """Regression test for issue #71: the old default of 1000 would have
    truncated this ~1200-page frontier before it ever emptied. With
    max_pages omitted entirely (the new None default), the crawl must
    run to genuine completion instead."""
    urls = [SEED] + [f"https://example.com/p{i}" for i in range(1200)]
    fetch_responses = {url: _html_response() for url in urls}
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=urls[1:], network_urls=[]),
        **{url: BrowserFetchResult(html="", dom_links=[], network_urls=[]) for url in urls[1:]},
    }
    frontier = UrlFrontier(SEED)
    http_fetcher = FakeHttpFetcher(fetch_responses)
    browser_fetcher = FakeBrowserFetcher(browser_responses)
    registry = ExtractorRegistry()
    registry.register(ConstantValueExtractor("VISUALPING{abcdef1234567890}"))
    repository = SqliteRepository(sqlite3.connect(":memory:"))

    orchestrator = Orchestrator(
        frontier=frontier,
        http_fetcher=http_fetcher,
        browser_fetcher=browser_fetcher,
        extractor_registry=registry,
        header_cookie_extractor=NoOpHeaderCookieExtractor(),
        repository=repository,
        concurrency=8,
        # max_pages intentionally omitted -- exercises the class's own
        # None default, not the test helper's separate default of 100.
    )

    summary = run(orchestrator.run())

    assert len(http_fetcher.calls) == len(urls)
    assert summary.resources_checked == len(urls)
    assert summary.queue_empty is True


def test_max_duration_seconds_stops_the_crawl_early():
    urls = [SEED] + [f"https://example.com/p{i}" for i in range(20)]
    fetch_responses = {url: _html_response() for url in urls}
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=urls[1:], network_urls=[]),
        **{url: BrowserFetchResult(html="", dom_links=[], network_urls=[]) for url in urls[1:]},
    }
    orchestrator, http_fetcher, _, _ = _build_orchestrator(
        fetch_responses,
        browser_responses,
        concurrency=1,
        max_pages=None,
        max_duration_seconds=0.15,
    )

    # A real delay per fetch, same reasoning as the stop()-mid-crawl test:
    # the fakes elsewhere in this file return instantly with no true
    # suspension point, which would make a time-based cutoff unobservable.
    original_fetch = http_fetcher.fetch

    async def delayed_fetch(url):
        await asyncio.sleep(0.05)
        return await original_fetch(url)

    http_fetcher.fetch = delayed_fetch

    summary = run(orchestrator.run())

    assert 0 < len(http_fetcher.calls) < len(urls)
    assert summary.resources_checked == len(http_fetcher.calls)
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


def test_orchestrator_persists_content_type_headers_cookies_for_replay():
    """issue #72: content_type/headers/cookies must flow from the fetch
    result through to storage, not just the raw snapshot bytes -- that's
    what makes get_all_page_fetch_data()/replay_extraction() possible."""
    fetch_responses = {
        SEED: FetchResult(
            content=b"<html></html>",
            content_type="text/html",
            status_code=200,
            headers={"X-Debug": "value"},
            cookies={"session": "abc"},
        )
    }
    browser_responses = {SEED: BrowserFetchResult(html="", dom_links=[], network_urls=[])}
    orchestrator, _, _, repository = _build_orchestrator(fetch_responses, browser_responses)

    run(orchestrator.run())

    data = repository.get_all_page_fetch_data()

    assert len(data) == 1
    assert data[0].url == SEED
    assert data[0].content == b"<html></html>"
    assert data[0].content_type == "text/html"
    assert data[0].headers == {"X-Debug": "value"}
    assert data[0].cookies == {"session": "abc"}


def test_orchestrator_resumes_from_repository_and_skips_already_visited_urls():
    repository = SqliteRepository(sqlite3.connect(":memory:"))
    # Simulate a previous, crashed run that already fetched PAGE_A.
    repository.save_page(
        PageResult(url=PAGE_A, status_code=200, fetched_at=datetime.now(timezone.utc)),
        snapshot=b"<html>already done</html>",
    )

    frontier = UrlFrontier(SEED)
    # Deliberately no entry for PAGE_A -- if the orchestrator tried to
    # re-fetch it, this raises KeyError and fails the test.
    fetch_responses = {SEED: _html_response(), PAGE_B: _html_response()}
    http_fetcher = FakeHttpFetcher(fetch_responses)
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=[PAGE_A, PAGE_B], network_urls=[]),
        PAGE_B: BrowserFetchResult(html="", dom_links=[], network_urls=[]),
    }
    browser_fetcher = FakeBrowserFetcher(browser_responses)
    registry = ExtractorRegistry()
    registry.register(ConstantValueExtractor("VISUALPING{abcdef1234567890}"))

    orchestrator = Orchestrator(
        frontier=frontier,
        http_fetcher=http_fetcher,
        browser_fetcher=browser_fetcher,
        extractor_registry=registry,
        header_cookie_extractor=NoOpHeaderCookieExtractor(),
        repository=repository,
    )

    summary = run(orchestrator.run())

    assert PAGE_A not in http_fetcher.calls
    assert sorted(http_fetcher.calls) == sorted([SEED, PAGE_B])
    assert summary.resources_checked == 2
    assert summary.queue_empty is True


def test_redirect_loop_does_not_hang_or_abort_the_crawl():
    # PAGE_A is a genuine HTTP redirect loop that survives HttpFetcher's
    # own redirect-following (issue #63) and reaches the browser fetcher's
    # page.goto() -- a real navigation that also follows redirects and
    # fails on a loop (e.g. net::ERR_TOO_MANY_REDIRECTS) rather than
    # hanging forever.
    fetch_responses = {
        SEED: _html_response(),
        PAGE_A: _html_response(),
        PAGE_B: _html_response(),
    }
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=[PAGE_A, PAGE_B], network_urls=[]),
        PAGE_A: RuntimeError("net::ERR_TOO_MANY_REDIRECTS"),
        PAGE_B: BrowserFetchResult(html="", dom_links=[], network_urls=[]),
    }
    orchestrator, http_fetcher, browser_fetcher, _ = _build_orchestrator(
        fetch_responses, browser_responses
    )

    summary = run(asyncio.wait_for(orchestrator.run(), timeout=2))

    assert sorted(http_fetcher.calls) == sorted([SEED, PAGE_A, PAGE_B])
    assert PAGE_A in browser_fetcher.calls
    assert summary.resources_checked == 2
    assert summary.queue_empty is True


def test_pause_blocks_all_fetches_until_resumed():
    fetch_responses = {SEED: _html_response(), PAGE_A: _html_response()}
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=[PAGE_A], network_urls=[]),
        PAGE_A: BrowserFetchResult(html="", dom_links=[], network_urls=[]),
    }
    orchestrator, http_fetcher, _, _ = _build_orchestrator(
        fetch_responses, browser_responses, concurrency=1
    )

    async def scenario():
        orchestrator.pause()
        task = asyncio.create_task(orchestrator.run())
        await asyncio.sleep(0.05)  # let the worker reach and block on the pause point
        assert http_fetcher.calls == [], "no fetch should happen while paused"

        orchestrator.resume()
        return await asyncio.wait_for(task, timeout=2)

    summary = run(scenario())

    assert sorted(http_fetcher.calls) == sorted([SEED, PAGE_A])
    assert summary.queue_empty is True


def test_stop_before_any_work_processes_nothing_and_leaves_queue_non_empty():
    urls = [SEED] + [f"https://example.com/p{i}" for i in range(5)]
    fetch_responses = {url: _html_response() for url in urls}
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=urls[1:], network_urls=[]),
        **{url: BrowserFetchResult(html="", dom_links=[], network_urls=[]) for url in urls[1:]},
    }
    orchestrator, http_fetcher, _, _ = _build_orchestrator(
        fetch_responses, browser_responses, concurrency=1
    )

    async def scenario():
        orchestrator.pause()  # hold it before it starts so stop() lands deterministically
        task = asyncio.create_task(orchestrator.run())
        await asyncio.sleep(0.05)
        orchestrator.stop()
        return await asyncio.wait_for(task, timeout=2)

    summary = run(scenario())

    assert http_fetcher.calls == []
    assert summary.resources_checked == 0
    assert summary.queue_empty is False


def test_stop_mid_crawl_ends_early_with_a_non_empty_queue():
    urls = [SEED] + [f"https://example.com/p{i}" for i in range(5)]
    fetch_responses = {url: _html_response() for url in urls}
    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=urls[1:], network_urls=[]),
        **{url: BrowserFetchResult(html="", dom_links=[], network_urls=[]) for url in urls[1:]},
    }
    orchestrator, http_fetcher, _, _ = _build_orchestrator(
        fetch_responses, browser_responses, concurrency=1
    )

    # A real delay per fetch, so the event loop actually yields between
    # pages -- the fakes elsewhere in this file return instantly with no
    # true suspension point, which would let a crawl this small run to
    # completion in a single scheduling turn and make "stop mid-crawl"
    # unobservable (stop() would always land after run() already finished).
    original_fetch = http_fetcher.fetch

    async def delayed_fetch(url):
        await asyncio.sleep(0.05)
        return await original_fetch(url)

    http_fetcher.fetch = delayed_fetch

    async def scenario():
        task = asyncio.create_task(orchestrator.run())
        await asyncio.sleep(0.12)  # ~2 fetches worth, well before all 6 finish
        orchestrator.stop()
        return await asyncio.wait_for(task, timeout=2)

    summary = run(scenario())

    assert 0 < len(http_fetcher.calls) < len(urls)
    assert summary.resources_checked == len(http_fetcher.calls)
    assert summary.queue_empty is False


def test_pagination_guard_stops_a_runaway_family_but_still_finds_real_content():
    real_content_url = "https://example.com/real-content"
    pagination_urls = [f"https://example.com/report?page={i}" for i in range(1, 51)]

    fetch_responses = {SEED: _html_response(), real_content_url: _html_response()}
    fetch_responses.update({url: _html_response() for url in pagination_urls})

    browser_responses = {
        SEED: BrowserFetchResult(
            html="", dom_links=[real_content_url, *pagination_urls], network_urls=[]
        ),
        real_content_url: BrowserFetchResult(html="", dom_links=[], network_urls=[]),
        **{
            url: BrowserFetchResult(html="", dom_links=[], network_urls=[])
            for url in pagination_urls
        },
    }

    frontier = UrlFrontier(SEED)
    http_fetcher = FakeHttpFetcher(fetch_responses)
    browser_fetcher = FakeBrowserFetcher(browser_responses)
    registry = ExtractorRegistry()
    registry.register(
        SelectiveExtractor(value="VISUALPING{abcdef1234567890}", urls_with_match={real_content_url})
    )
    repository = SqliteRepository(sqlite3.connect(":memory:"))

    orchestrator = Orchestrator(
        frontier=frontier,
        http_fetcher=http_fetcher,
        browser_fetcher=browser_fetcher,
        extractor_registry=registry,
        header_cookie_extractor=NoOpHeaderCookieExtractor(),
        repository=repository,
        concurrency=1,
        max_pages=100,
        pagination_family_limit=3,
    )

    summary = run(orchestrator.run())

    assert real_content_url in http_fetcher.calls
    assert summary.unique_passwords_found == 1
    # SEED + real content + at most 3 unproductive pagination pages before
    # the guard stops the family -- not all 50.
    assert len(http_fetcher.calls) <= 5
    assert len([u for u in http_fetcher.calls if u in pagination_urls]) == 3


def test_pagination_guard_terminates_an_adversarial_trap_that_always_looks_productive():
    """Regression test for issue #78: a real target was found serving a
    ?page=N family where every page has randomized content, so it always
    discovers a "new" link (to the next page) even though it never
    contains a password -- which used to reset the old new_links-based
    streak forever and never terminate. Simulated here the same way:
    every pagination page links only to the next page in the chain (a
    "new" link every time) and no extractor is registered at all, so
    new_matches is always 0. Must still terminate well short of the
    full, deliberately oversized 100-page trap."""
    pagination_urls = [f"https://example.com/report?page={i}" for i in range(1, 101)]
    fetch_responses = {SEED: _html_response()}
    fetch_responses.update({url: _html_response() for url in pagination_urls})

    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=[pagination_urls[0]], network_urls=[])
    }
    for i, url in enumerate(pagination_urls):
        next_link = [pagination_urls[i + 1]] if i + 1 < len(pagination_urls) else []
        browser_responses[url] = BrowserFetchResult(html="", dom_links=next_link, network_urls=[])

    frontier = UrlFrontier(SEED)
    http_fetcher = FakeHttpFetcher(fetch_responses)
    browser_fetcher = FakeBrowserFetcher(browser_responses)
    registry = ExtractorRegistry()  # no extractor registered -- never a match, ever
    repository = SqliteRepository(sqlite3.connect(":memory:"))

    orchestrator = Orchestrator(
        frontier=frontier,
        http_fetcher=http_fetcher,
        browser_fetcher=browser_fetcher,
        extractor_registry=registry,
        header_cookie_extractor=NoOpHeaderCookieExtractor(),
        repository=repository,
        concurrency=1,
        max_pages=None,  # exactly issue #71's default -- no whole-crawl cap in play
        pagination_family_limit=10,
    )

    summary = run(asyncio.wait_for(orchestrator.run(), timeout=5))

    # SEED + exactly the 10 pagination pages processed before the
    # unproductive streak trips -- not all 100. The next-discovered page
    # (11) is skipped once its family is stopped, so the frontier is
    # actually empty, not just abandoned mid-queue.
    assert len([u for u in http_fetcher.calls if u in pagination_urls]) == 10
    assert summary.resources_checked == 11
    assert summary.queue_empty is True


def test_pagination_guard_does_not_cut_off_a_legitimate_index_with_no_direct_matches():
    """Regression test for issue #88: a real crawl's coverage dropped
    from ~680 pages to ~480 because PaginationGuard's old new_matches-only
    productivity signal (issue #78) wrongly treated an index/listing
    family as unproductive whenever the index pages themselves never
    contain a password directly -- the overwhelmingly common real shape,
    where a listing page links out to individual content pages and only
    those carry the secret. Simulated here: 20 index pages
    (/catalog?page=1..20 -- deliberately more than the default
    pagination_family_limit of 10, so the old bug would have cut this
    off with 10 items undiscovered), each with zero direct matches, each
    linking to (a) the next index page in the same family, and (b) one
    brand-new individual content page that DOES have a match. Uses the
    orchestrator's actual default pagination settings -- no override --
    to prove the fix holds with real-world configuration, not just a
    generous test-only limit."""
    index_urls = [f"https://example.com/catalog?page={i}" for i in range(1, 21)]
    item_urls = [f"https://example.com/item-{i}" for i in range(1, 21)]

    fetch_responses = {SEED: _html_response()}
    fetch_responses.update({url: _html_response() for url in index_urls})
    fetch_responses.update({url: _html_response() for url in item_urls})

    browser_responses = {
        SEED: BrowserFetchResult(html="", dom_links=[index_urls[0]], network_urls=[])
    }
    for i, index_url in enumerate(index_urls):
        next_index = [index_urls[i + 1]] if i + 1 < len(index_urls) else []
        browser_responses[index_url] = BrowserFetchResult(
            html="", dom_links=[*next_index, item_urls[i]], network_urls=[]
        )
        browser_responses[item_urls[i]] = BrowserFetchResult(html="", dom_links=[], network_urls=[])

    frontier = UrlFrontier(SEED)
    http_fetcher = FakeHttpFetcher(fetch_responses)
    browser_fetcher = FakeBrowserFetcher(browser_responses)

    # A distinct password value per item page (not SelectiveExtractor's
    # single fixed value) proves all 20 are actually found individually,
    # not deduped down to "the same password."
    class _PerItemExtractor:
        def extract(self, content: bytes, content_type: str, url: str):
            if url not in item_urls:
                return []
            return [
                PasswordMatch(
                    value=f"VISUALPING{{item{item_urls.index(url):04d}deadbeef00}}",
                    source_type=SourceType.HTML_TEXT,
                    source_url=url,
                    context_before="",
                    context_after="",
                    locator="x",
                )
            ]

    registry = ExtractorRegistry()
    registry.register(_PerItemExtractor())
    repository = SqliteRepository(sqlite3.connect(":memory:"))

    orchestrator = Orchestrator(
        frontier=frontier,
        http_fetcher=http_fetcher,
        browser_fetcher=browser_fetcher,
        extractor_registry=registry,
        header_cookie_extractor=NoOpHeaderCookieExtractor(),
        repository=repository,
        concurrency=1,
        # Deliberately no pagination_family_limit/pagination_family_page_cap
        # override -- proves the fix holds with the orchestrator's actual
        # shipped defaults.
    )

    summary = run(asyncio.wait_for(orchestrator.run(), timeout=5))

    # All 20 index pages were followed (not cut off after 10), and all 20
    # item pages they link to were discovered and fetched.
    assert len([u for u in http_fetcher.calls if u in index_urls]) == 20
    assert len([u for u in http_fetcher.calls if u in item_urls]) == 20
    assert summary.unique_passwords_found == 20
    assert summary.queue_empty is True
