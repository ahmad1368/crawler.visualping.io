from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.routes import CrawlStatus, _CrawlState, app
from app.models import CrawlSummary, PasswordMatch, SourceType

VALID_BODY = {
    "url": "https://example.com",
    "username": "alice",
    "password": "s3cret",
    "context_chars": 40,
}

CANNED_SUMMARY = CrawlSummary(
    pages_visited=2,
    resources_checked=3,
    unique_passwords_found=1,
    queue_empty=True,
    started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    finished_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
)


class FakeOrchestrator:
    def __init__(self, summary=None, error=None):
        self._summary = summary
        self._error = error

    async def run(self):
        if self._error is not None:
            raise self._error
        return self._summary


class FakeRepository:
    def __init__(self, snapshots=None):
        self._snapshots = snapshots or {}

    def get_matches(self):
        return []

    def get_snapshot(self, url):
        return self._snapshots.get(url)


def _patch_orchestrator(monkeypatch, summary=None, error=None):
    async def fake_build_orchestrator(request, event_bus):
        async def cleanup():
            return None

        return FakeOrchestrator(summary=summary, error=error), FakeRepository(), cleanup

    monkeypatch.setattr(routes, "_build_orchestrator", fake_build_orchestrator)


@pytest.fixture(autouse=True)
def _clear_crawls():
    routes._crawls.clear()
    yield
    routes._crawls.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_start_crawl_returns_id_and_completes_via_background_task(monkeypatch, client):
    _patch_orchestrator(monkeypatch, summary=CANNED_SUMMARY)

    response = client.post("/crawls", json=VALID_BODY)

    assert response.status_code == 202
    crawl_id = response.json()["crawl_id"]
    assert crawl_id

    status_response = client.get(f"/crawls/{crawl_id}/status")
    assert status_response.json() == {"crawl_id": crawl_id, "status": "finished"}

    report_response = client.get(f"/crawls/{crawl_id}/report")
    assert report_response.status_code == 200
    body = report_response.json()
    assert body["summary"]["unique_passwords_found"] == 1
    assert body["matches"] == []


def test_invalid_url_is_rejected():
    client_ = TestClient(app)
    response = client_.post("/crawls", json={**VALID_BODY, "url": "not-a-url"})

    assert response.status_code == 422


def test_empty_username_is_rejected():
    client_ = TestClient(app)
    response = client_.post("/crawls", json={**VALID_BODY, "username": ""})

    assert response.status_code == 422


def test_empty_password_is_rejected():
    client_ = TestClient(app)
    response = client_.post("/crawls", json={**VALID_BODY, "password": ""})

    assert response.status_code == 422


def test_status_for_unknown_crawl_returns_404(client):
    response = client.get("/crawls/does-not-exist/status")

    assert response.status_code == 404


def test_report_for_unknown_crawl_returns_404(client):
    response = client.get("/crawls/does-not-exist/report")

    assert response.status_code == 404


def test_report_before_finished_returns_409(client):
    routes._crawls["still-running"] = _CrawlState()

    response = client.get("/crawls/still-running/report")

    assert response.status_code == 409


def test_report_matches_table_dedupes_and_counts_repeated_values(monkeypatch, client):
    def _match(**overrides):
        fields = dict(
            value="VISUALPING{abcdef1234567890}",
            source_type=SourceType.HTML_TEXT,
            source_url="https://example.com/page",
            context_before="before",
            context_after="after",
            locator="line:1,col:0",
        )
        fields.update(overrides)
        return PasswordMatch(**fields)

    repeated = _match()
    other_page = _match(source_url="https://example.com/other", locator="line:2,col:0")

    class RepositoryWithMatches:
        def get_matches(self):
            return [repeated, repeated, other_page]

    async def fake_build_orchestrator(request, event_bus):
        async def cleanup():
            return None

        return FakeOrchestrator(summary=CANNED_SUMMARY), RepositoryWithMatches(), cleanup

    monkeypatch.setattr(routes, "_build_orchestrator", fake_build_orchestrator)

    response = client.post("/crawls", json=VALID_BODY)
    crawl_id = response.json()["crawl_id"]

    report = client.get(f"/crawls/{crawl_id}/report").json()

    assert len(report["matches"]) == 2
    row = next(r for r in report["matches"] if r["page_url"] == "https://example.com/page")
    assert row["count_in_page"] == 2
    assert row["source_type"] == "html_text"
    assert row["context_before"] == "before"
    other_row = next(r for r in report["matches"] if r["page_url"] == "https://example.com/other")
    assert other_row["count_in_page"] == 1


def test_failed_crawl_status_and_report(monkeypatch, client):
    _patch_orchestrator(monkeypatch, error=RuntimeError("target unreachable"))

    response = client.post("/crawls", json=VALID_BODY)
    crawl_id = response.json()["crawl_id"]

    status_response = client.get(f"/crawls/{crawl_id}/status")
    assert status_response.json()["status"] == "failed"

    report_response = client.get(f"/crawls/{crawl_id}/report")
    assert report_response.status_code == 500


def test_get_snapshot_returns_decoded_content(monkeypatch, client):
    async def fake_build_orchestrator(request, event_bus):
        async def cleanup():
            return None

        repository = FakeRepository(snapshots={"https://example.com/page": b"<html>hi</html>"})
        return FakeOrchestrator(summary=CANNED_SUMMARY), repository, cleanup

    monkeypatch.setattr(routes, "_build_orchestrator", fake_build_orchestrator)

    response = client.post("/crawls", json=VALID_BODY)
    crawl_id = response.json()["crawl_id"]

    snapshot_response = client.get(
        f"/crawls/{crawl_id}/snapshot", params={"url": "https://example.com/page"}
    )

    assert snapshot_response.status_code == 200
    assert snapshot_response.json() == {
        "url": "https://example.com/page",
        "content": "<html>hi</html>",
    }


def test_get_snapshot_returns_404_when_not_found(monkeypatch, client):
    _patch_orchestrator(monkeypatch, summary=CANNED_SUMMARY)

    response = client.post("/crawls", json=VALID_BODY)
    crawl_id = response.json()["crawl_id"]

    snapshot_response = client.get(
        f"/crawls/{crawl_id}/snapshot", params={"url": "https://example.com/missing"}
    )

    assert snapshot_response.status_code == 404


def test_get_snapshot_for_unknown_crawl_returns_404(client):
    response = client.get("/crawls/does-not-exist/snapshot", params={"url": "https://example.com"})

    assert response.status_code == 404


def test_response_payload_shapes_match_the_full_contract(monkeypatch, client):
    """Exhaustive key-set checks for every REST response shape -- the
    other tests in this file spot-check individual fields; this one
    confirms nothing is missing or unexpectedly added."""
    match = PasswordMatch(
        value="VISUALPING{abcdef1234567890}",
        source_type=SourceType.HTML_TEXT,
        source_url="https://example.com/page",
        context_before="before",
        context_after="after",
        locator="line:1,col:0",
    )

    class RepositoryWithOneMatch:
        def get_matches(self):
            return [match]

        def get_snapshot(self, url):
            return b"<html>content</html>"

    async def fake_build_orchestrator(request, event_bus):
        async def cleanup():
            return None

        return FakeOrchestrator(summary=CANNED_SUMMARY), RepositoryWithOneMatch(), cleanup

    monkeypatch.setattr(routes, "_build_orchestrator", fake_build_orchestrator)

    start_response = client.post("/crawls", json=VALID_BODY)
    assert set(start_response.json().keys()) == {"crawl_id"}
    crawl_id = start_response.json()["crawl_id"]

    status_response = client.get(f"/crawls/{crawl_id}/status")
    assert set(status_response.json().keys()) == {"crawl_id", "status"}

    report = client.get(f"/crawls/{crawl_id}/report").json()
    assert set(report.keys()) == {"summary", "matches"}
    assert set(report["summary"].keys()) == {
        "pages_visited",
        "resources_checked",
        "unique_passwords_found",
        "queue_empty",
        "started_at",
        "finished_at",
    }
    assert len(report["matches"]) == 1
    assert set(report["matches"][0].keys()) == {
        "page_url",
        "source_type",
        "value",
        "context_before",
        "context_after",
        "count_in_page",
        "locator",
    }

    snapshot_response = client.get(
        f"/crawls/{crawl_id}/snapshot", params={"url": "https://example.com/page"}
    )
    assert set(snapshot_response.json().keys()) == {"url", "content"}
