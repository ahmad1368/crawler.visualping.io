import asyncio

from app.crawler.content_negotiation import probe_content_negotiation
from app.crawler.fetcher import FetchResult
from app.models import PageFetchData, PasswordMatch, SourceType

PASSWORD = "VISUALPING{abcdef1234567890}"
HTML_PAGE = "https://example.com/"
JSON_PAGE = "https://example.com/api"


def run(coro):
    return asyncio.run(coro)


class FakeHttpFetcher:
    def __init__(self, responses: dict[tuple[str, str], FetchResult]) -> None:
        """`responses` keyed by (url, accept-or-x-requested-with-label) --
        the test controls exactly what each negotiated variant returns."""
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def fetch(self, url: str, extra_headers: dict | None = None) -> FetchResult:
        self.calls.append((url, extra_headers or {}))
        key = (url, _label(extra_headers or {}))
        if key in self._responses:
            return self._responses[key]
        return FetchResult(
            content=b"<html>nothing here</html>",
            content_type="text/html",
            status_code=200,
            headers={},
            cookies={},
        )


def _label(headers: dict) -> str:
    return ", ".join(f"{k}: {v}" for k, v in headers.items())


class FakeRepository:
    def __init__(self, page_fetch_data: list[PageFetchData]) -> None:
        self._page_fetch_data = page_fetch_data
        self.saved_matches: list[PasswordMatch] = []

    def get_all_page_fetch_data(self):
        return self._page_fetch_data

    def save_match(self, match: PasswordMatch) -> None:
        self.saved_matches.append(match)


def test_finds_password_only_returned_under_json_negotiation():
    page_data = [PageFetchData(url=HTML_PAGE, content=b"<html></html>", content_type="text/html")]
    responses = {
        (HTML_PAGE, "Accept: application/json"): FetchResult(
            content=f'{{"debug": "{PASSWORD}"}}'.encode(),
            content_type="application/json",
            status_code=200,
            headers={},
            cookies={},
        ),
    }
    fetcher = FakeHttpFetcher(responses)
    repository = FakeRepository(page_data)

    report = run(probe_content_negotiation(repository, fetcher))

    assert report.pages_probed == 1
    assert report.matches_found == 1
    assert "Accept: application/json" in report.headers_tested
    assert len(repository.saved_matches) == 1
    assert repository.saved_matches[0].value == PASSWORD
    assert repository.saved_matches[0].source_type == SourceType.CONTENT_NEGOTIATION


def test_finds_password_in_a_response_header_not_the_body():
    page_data = [PageFetchData(url=HTML_PAGE, content=b"<html></html>", content_type="text/html")]
    responses = {
        (HTML_PAGE, "X-Requested-With: XMLHttpRequest"): FetchResult(
            content=b"ok",
            content_type="text/plain",
            status_code=200,
            headers={"X-Debug-Flag": PASSWORD},
            cookies={},
        ),
    }
    fetcher = FakeHttpFetcher(responses)
    repository = FakeRepository(page_data)

    report = run(probe_content_negotiation(repository, fetcher))

    assert report.matches_found == 1
    assert repository.saved_matches[0].locator.endswith(":headers")


def test_probes_every_header_set_against_each_sampled_page():
    page_data = [PageFetchData(url=HTML_PAGE, content=b"<html></html>", content_type="text/html")]
    fetcher = FakeHttpFetcher({})
    repository = FakeRepository(page_data)

    run(probe_content_negotiation(repository, fetcher))

    assert len(fetcher.calls) == 3  # one per header set in _PROBE_HEADER_SETS
    assert all(url == HTML_PAGE for url, _ in fetcher.calls)


def test_sample_size_bounds_how_many_pages_are_probed():
    page_data = [
        PageFetchData(
            url=f"https://example.com/p{i}", content=b"<html></html>", content_type="text/html"
        )
        for i in range(10)
    ]
    fetcher = FakeHttpFetcher({})
    repository = FakeRepository(page_data)

    report = run(probe_content_negotiation(repository, fetcher, sample_size=3))

    assert report.pages_probed == 3
    assert len(fetcher.calls) == 9  # 3 pages * 3 header sets


def test_ignores_non_html_pages():
    page_data = [
        PageFetchData(url=JSON_PAGE, content=b"{}", content_type="application/json"),
    ]
    fetcher = FakeHttpFetcher({})
    repository = FakeRepository(page_data)

    report = run(probe_content_negotiation(repository, fetcher))

    assert report.pages_probed == 0
    assert fetcher.calls == []


def test_a_failed_probe_does_not_abort_the_rest():
    class FailingFetcher(FakeHttpFetcher):
        async def fetch(self, url, extra_headers=None):
            if (extra_headers or {}).get("Accept") == "application/json":
                raise ConnectionError("boom")
            return await super().fetch(url, extra_headers)

    page_data = [PageFetchData(url=HTML_PAGE, content=b"<html></html>", content_type="text/html")]
    fetcher = FailingFetcher({})
    repository = FakeRepository(page_data)

    report = run(probe_content_negotiation(repository, fetcher))

    assert report.pages_probed == 1
    assert report.matches_found == 0  # no exception propagated


def test_unique_values_is_updated_in_place_when_provided():
    page_data = [PageFetchData(url=HTML_PAGE, content=b"<html></html>", content_type="text/html")]
    responses = {
        (HTML_PAGE, "Accept: text/plain"): FetchResult(
            content=PASSWORD.encode(),
            content_type="text/plain",
            status_code=200,
            headers={},
            cookies={},
        ),
    }
    fetcher = FakeHttpFetcher(responses)
    repository = FakeRepository(page_data)
    unique_values: set[str] = set()

    run(probe_content_negotiation(repository, fetcher, unique_values=unique_values))

    assert unique_values == {PASSWORD}
