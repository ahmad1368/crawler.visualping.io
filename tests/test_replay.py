from app.crawler.replay import replay_extraction
from app.extractors.base import ExtractorRegistry
from app.extractors.binary_fallback import BinaryFallbackExtractor
from app.extractors.css_js import CssJsExtractor
from app.extractors.headers_cookies import HeaderCookieExtractor
from app.extractors.html import HtmlExtractor
from app.extractors.image_exif import ImageExifExtractor
from app.models import CrawlSummary, PageFetchData, PasswordMatch
from app.storage.repository import Repository

HTML_PASSWORD = "VISUALPING{aaaaaaaaaaaaaaaa}"
HEADER_PASSWORD = "VISUALPING{bbbbbbbbbbbbbbbb}"
COOKIE_PASSWORD = "VISUALPING{cccccccccccccccc}"


class FakeReplayRepository(Repository):
    """A full Repository double -- not just a get_all_page_fetch_data()
    stub -- so a test can assert replay never calls save_page()/
    save_match() (it must be read-only)."""

    def __init__(self, pages: list[PageFetchData]) -> None:
        self._pages = pages
        self.save_page_calls = 0
        self.save_match_calls = 0

    def save_page(self, page, snapshot, content_type=None, headers=None, cookies=None) -> None:
        self.save_page_calls += 1

    def save_match(self, match: PasswordMatch) -> None:
        self.save_match_calls += 1

    def get_report(self) -> CrawlSummary:
        raise NotImplementedError("not exercised by replay_extraction")

    def get_snapshot(self, url: str) -> bytes | None:
        raise NotImplementedError("not exercised by replay_extraction")

    def get_matches(self) -> list[PasswordMatch]:
        raise NotImplementedError("not exercised by replay_extraction")

    def get_visited_urls(self) -> list[str]:
        raise NotImplementedError("not exercised by replay_extraction")

    def get_all_page_fetch_data(self) -> list[PageFetchData]:
        return self._pages


def _registry() -> ExtractorRegistry:
    registry = ExtractorRegistry()
    registry.register(HtmlExtractor())
    registry.register(CssJsExtractor())
    registry.register(ImageExifExtractor())
    registry.register(BinaryFallbackExtractor())
    return registry


def test_replay_finds_matches_from_stored_html_and_headers():
    pages = [
        PageFetchData(
            url="https://example.com/page",
            content=f"<html>secret: {HTML_PASSWORD}</html>".encode(),
            content_type="text/html",
            headers={"X-Debug-Password": f"leaked={HEADER_PASSWORD}"},
            cookies={"session": f"token={COOKIE_PASSWORD}"},
        )
    ]
    repo = FakeReplayRepository(pages)

    summary, matches = replay_extraction(repo, _registry(), HeaderCookieExtractor())

    values = {m.value for m in matches}
    assert values == {HTML_PASSWORD, HEADER_PASSWORD, COOKIE_PASSWORD}
    assert summary.unique_passwords_found == 3
    assert summary.pages_visited == 1
    assert summary.resources_checked == 1
    assert summary.queue_empty is True


def test_replay_routes_by_stored_content_type():
    """A password embedded via CSS content: must only be found through
    the content_type that was actually stored -- proves replay routes to
    the same extractor a live fetch would have, not every extractor."""
    pages = [
        PageFetchData(
            url="https://example.com/style.css",
            content=f'.x::before {{ content: "{HTML_PASSWORD}"; }}'.encode(),
            content_type="text/css",
        )
    ]
    repo = FakeReplayRepository(pages)

    _summary, matches = replay_extraction(repo, _registry(), HeaderCookieExtractor())

    assert [m.value for m in matches] == [HTML_PASSWORD]
    assert matches[0].source_type.value == "css"


def test_replay_is_read_only():
    pages = [
        PageFetchData(
            url="https://example.com/page",
            content=f"<html>{HTML_PASSWORD}</html>".encode(),
            content_type="text/html",
        )
    ]
    repo = FakeReplayRepository(pages)

    replay_extraction(repo, _registry(), HeaderCookieExtractor())

    assert repo.save_page_calls == 0
    assert repo.save_match_calls == 0


def test_replay_over_no_pages_returns_empty_summary_and_no_matches():
    repo = FakeReplayRepository([])

    summary, matches = replay_extraction(repo, _registry(), HeaderCookieExtractor())

    assert matches == []
    assert summary.pages_visited == 0
    assert summary.resources_checked == 0
    assert summary.unique_passwords_found == 0
    assert summary.queue_empty is True


def test_replay_repeated_password_across_pages_counts_once_in_unique_passwords():
    pages = [
        PageFetchData(
            url="https://example.com/a",
            content=f"<html>{HTML_PASSWORD}</html>".encode(),
            content_type="text/html",
        ),
        PageFetchData(
            url="https://example.com/b",
            content=f"<html>{HTML_PASSWORD}</html>".encode(),
            content_type="text/html",
        ),
    ]
    repo = FakeReplayRepository(pages)

    summary, matches = replay_extraction(repo, _registry(), HeaderCookieExtractor())

    assert len(matches) == 2  # one per page -- not deduped away
    assert summary.unique_passwords_found == 1  # but the same value, so 1 unique
