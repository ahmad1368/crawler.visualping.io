import sqlite3
from datetime import datetime, timezone

import pytest

from app.models import PageResult, PasswordMatch, SourceType
from app.storage.repository import Repository
from app.storage.sqlite import SqliteRepository


def _repo() -> SqliteRepository:
    return SqliteRepository(sqlite3.connect(":memory:"))


def _match(**overrides) -> PasswordMatch:
    fields = dict(
        value="VISUALPING{abcdef1234567890}",
        source_type=SourceType.HTML_TEXT,
        source_url="https://example.com/page",
        context_before="",
        context_after="",
        locator="line:1,col:0",
    )
    fields.update(overrides)
    return PasswordMatch(**fields)


def test_repository_is_abstract():
    with pytest.raises(TypeError):
        Repository()


def test_save_page_persists_snapshot():
    repo = _repo()
    page = PageResult(
        url="https://example.com/page",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
    )

    repo.save_page(page, snapshot=b"<html>raw content</html>")

    assert repo.get_snapshot("https://example.com/page") == b"<html>raw content</html>"


def test_get_snapshot_returns_none_when_not_found():
    repo = _repo()

    assert repo.get_snapshot("https://example.com/missing") is None


def test_save_page_persists_its_matches():
    repo = _repo()
    page = PageResult(
        url="https://example.com/page",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
        matches=[_match()],
    )

    repo.save_page(page, snapshot=b"content")

    assert repo.get_report().unique_passwords_found == 1


def test_save_match_persists_standalone_match_without_a_page():
    repo = _repo()
    match = _match(source_type=SourceType.HTTP_HEADER, locator="header:X-Debug")

    repo.save_match(match)

    assert repo.get_report().unique_passwords_found == 1


def test_get_report_counts_pages_and_dedupes_password_values():
    repo = _repo()
    page1 = PageResult(
        url="https://example.com/a",
        status_code=200,
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        matches=[_match(value="VISUALPING{aaaaaaaaaaaaaaaa}", source_url="https://example.com/a")],
    )
    page2 = PageResult(
        url="https://example.com/b",
        status_code=200,
        fetched_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        matches=[_match(value="VISUALPING{aaaaaaaaaaaaaaaa}", source_url="https://example.com/b")],
    )

    repo.save_page(page1, snapshot=b"a")
    repo.save_page(page2, snapshot=b"b")
    report = repo.get_report()

    assert report.pages_visited == 2
    assert report.unique_passwords_found == 1
    assert report.started_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert report.finished_at == datetime(2026, 1, 2, tzinfo=timezone.utc)


def test_get_report_on_empty_database():
    repo = _repo()

    report = repo.get_report()

    assert report.pages_visited == 0
    assert report.unique_passwords_found == 0
    assert report.finished_at is None


def test_save_page_upserts_on_repeated_url():
    repo = _repo()
    page_v1 = PageResult(
        url="https://example.com/a",
        status_code=500,
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    page_v2 = PageResult(
        url="https://example.com/a",
        status_code=200,
        fetched_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    repo.save_page(page_v1, snapshot=b"old")
    repo.save_page(page_v2, snapshot=b"new")

    assert repo.get_snapshot("https://example.com/a") == b"new"
    assert repo.get_report().pages_visited == 1


def test_get_matches_returns_empty_list_when_none_stored():
    repo = _repo()

    assert repo.get_matches() == []


def test_get_matches_returns_matches_from_pages_and_standalone_in_order():
    repo = _repo()
    page_match = _match(value="VISUALPING{aaaaaaaaaaaaaaaa}", locator="line:1,col:0")
    page = PageResult(
        url="https://example.com/page",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
        matches=[page_match],
    )
    repo.save_page(page, snapshot=b"content")

    standalone_match = _match(
        value="VISUALPING{bbbbbbbbbbbbbbbb}",
        source_type=SourceType.HTTP_HEADER,
        locator="header:X-Debug",
    )
    repo.save_match(standalone_match)

    matches = repo.get_matches()

    assert [m.value for m in matches] == [page_match.value, standalone_match.value]
    assert matches[0].source_type == SourceType.HTML_TEXT
    assert matches[1].source_type == SourceType.HTTP_HEADER
    assert matches[1].locator == "header:X-Debug"


def test_get_matches_includes_duplicate_values_for_count_aggregation():
    repo = _repo()
    match = _match()
    page = PageResult(
        url="https://example.com/page",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
        matches=[match, match],
    )

    repo.save_page(page, snapshot=b"content")

    assert len(repo.get_matches()) == 2


def test_get_matches_preserves_the_same_value_found_on_two_distinct_pages():
    """Regression test for issue #70: no uniqueness constraint on
    `value` -- only `id` is a primary key (see the `matches` table
    schema) -- so the same password found on two separate pages is two
    separate rows, not silently collapsed into one at the storage layer.
    Cross-page grouping-for-display happens one layer up, in
    app/api/routes.py::_build_match_rows(), not here."""
    repo = _repo()
    match_on_page_y = _match(source_url="https://example.com/page-y")
    match_on_page_b = _match(source_url="https://example.com/page-b")

    repo.save_page(
        PageResult(
            url="https://example.com/page-y",
            status_code=200,
            fetched_at=datetime.now(timezone.utc),
            matches=[match_on_page_y],
        ),
        snapshot=b"content-y",
    )
    repo.save_page(
        PageResult(
            url="https://example.com/page-b",
            status_code=200,
            fetched_at=datetime.now(timezone.utc),
            matches=[match_on_page_b],
        ),
        snapshot=b"content-b",
    )

    matches = repo.get_matches()

    assert len(matches) == 2
    assert {m.source_url for m in matches} == {
        "https://example.com/page-y",
        "https://example.com/page-b",
    }
    assert all(m.value == match_on_page_y.value for m in matches)


def test_get_all_page_fetch_data_returns_empty_list_when_none_stored():
    repo = _repo()

    assert repo.get_all_page_fetch_data() == []


def test_save_page_persists_content_type_headers_cookies_for_replay():
    """issue #72: content_type/headers/cookies, once saved, come back out
    via get_all_page_fetch_data() -- the input a replay pass needs to
    re-run extraction without a live re-fetch."""
    repo = _repo()
    page = PageResult(
        url="https://example.com/page",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
    )

    repo.save_page(
        page,
        snapshot=b"<html>content</html>",
        content_type="text/html",
        headers={"X-Debug-Password": "leaked=VISUALPING{abcdef1234567890}"},
        cookies={"session": "abc123"},
    )

    data = repo.get_all_page_fetch_data()

    assert len(data) == 1
    assert data[0].url == "https://example.com/page"
    assert data[0].content == b"<html>content</html>"
    assert data[0].content_type == "text/html"
    assert data[0].headers == {"X-Debug-Password": "leaked=VISUALPING{abcdef1234567890}"}
    assert data[0].cookies == {"session": "abc123"}


def test_save_page_without_content_type_is_excluded_from_replay_data():
    """A page saved without content_type/headers/cookies (the params are
    optional, for callers that only care about the snapshot) can't be
    replayed with any confidence about which extractor should have run --
    excluded from get_all_page_fetch_data() rather than replayed with a
    guessed or empty content_type."""
    repo = _repo()
    page = PageResult(
        url="https://example.com/page",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
    )

    repo.save_page(page, snapshot=b"content")  # no content_type/headers/cookies

    assert repo.get_all_page_fetch_data() == []


def test_get_all_page_fetch_data_defaults_missing_headers_cookies_to_empty_dict():
    repo = _repo()
    page = PageResult(
        url="https://example.com/page",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
    )

    repo.save_page(page, snapshot=b"content", content_type="text/plain")

    data = repo.get_all_page_fetch_data()

    assert data[0].headers == {}
    assert data[0].cookies == {}


def test_save_page_upsert_updates_content_type_headers_cookies_too():
    repo = _repo()
    page = PageResult(
        url="https://example.com/a",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
    )

    repo.save_page(page, snapshot=b"old", content_type="text/plain", headers={"A": "1"}, cookies={})
    repo.save_page(
        page, snapshot=b"new", content_type="text/html", headers={"B": "2"}, cookies={"c": "3"}
    )

    data = repo.get_all_page_fetch_data()

    assert len(data) == 1
    assert data[0].content == b"new"
    assert data[0].content_type == "text/html"
    assert data[0].headers == {"B": "2"}
    assert data[0].cookies == {"c": "3"}


def test_get_visited_urls_returns_empty_list_when_none_stored():
    repo = _repo()

    assert repo.get_visited_urls() == []


def test_get_visited_urls_returns_every_saved_page_url():
    repo = _repo()
    page1 = PageResult(
        url="https://example.com/a", status_code=200, fetched_at=datetime.now(timezone.utc)
    )
    page2 = PageResult(
        url="https://example.com/b", status_code=200, fetched_at=datetime.now(timezone.utc)
    )

    repo.save_page(page1, snapshot=b"a")
    repo.save_page(page2, snapshot=b"b")

    assert sorted(repo.get_visited_urls()) == [
        "https://example.com/a",
        "https://example.com/b",
    ]
