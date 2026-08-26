from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.routes import CrawlStatus, _CrawlState, app
from app.models import CrawlSummary

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


def _patch_orchestrator(monkeypatch, summary=None, error=None):
    async def fake_build_orchestrator(request, event_bus):
        async def cleanup():
            return None

        return FakeOrchestrator(summary=summary, error=error), cleanup

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
    assert report_response.json()["unique_passwords_found"] == 1


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


def test_failed_crawl_status_and_report(monkeypatch, client):
    _patch_orchestrator(monkeypatch, error=RuntimeError("target unreachable"))

    response = client.post("/crawls", json=VALID_BODY)
    crawl_id = response.json()["crawl_id"]

    status_response = client.get(f"/crawls/{crawl_id}/status")
    assert status_response.json()["status"] == "failed"

    report_response = client.get(f"/crawls/{crawl_id}/report")
    assert report_response.status_code == 500
