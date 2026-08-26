from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import CrawlSummary, PageResult, PasswordMatch, SourceType


def _match(**overrides):
    fields = dict(
        value="hunter2",
        source_type=SourceType.HTML,
        source_url="https://example.com/page",
        context_before="password: ",
        context_after=" </p>",
        locator="line:42",
    )
    fields.update(overrides)
    return PasswordMatch(**fields)


def test_password_match_valid():
    match = _match()
    assert match.source_type == SourceType.HTML


def test_password_match_missing_required_field_raises():
    with pytest.raises(ValidationError):
        PasswordMatch(
            source_type=SourceType.HTML,
            source_url="https://example.com/page",
            context_before="",
            context_after="",
            locator="line:1",
        )


def test_password_match_invalid_source_type_raises():
    with pytest.raises(ValidationError):
        _match(source_type="not_a_real_source_type")


def test_page_result_defaults_to_empty_matches():
    result = PageResult(
        url="https://example.com/page",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
    )
    assert result.matches == []


def test_page_result_holds_matches():
    result = PageResult(
        url="https://example.com/page",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
        matches=[_match()],
    )
    assert len(result.matches) == 1


def test_page_result_missing_required_field_raises():
    with pytest.raises(ValidationError):
        PageResult(status_code=200, fetched_at=datetime.now(timezone.utc))


def test_crawl_summary_finished_at_optional():
    summary = CrawlSummary(
        pages_visited=1,
        resources_checked=2,
        unique_passwords_found=0,
        queue_empty=False,
        started_at=datetime.now(timezone.utc),
    )
    assert summary.finished_at is None


def test_crawl_summary_missing_required_field_raises():
    with pytest.raises(ValidationError):
        CrawlSummary(
            resources_checked=2,
            unique_passwords_found=0,
            queue_empty=False,
            started_at=datetime.now(timezone.utc),
        )
