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
