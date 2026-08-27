import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.routes import CrawlRequest, _CrawlState, app
from app.events import EventBus
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


def test_same_password_found_on_two_distinct_pages_lists_once_per_page(monkeypatch, client):
    """Regression test for issue #70: the same password value found on
    two distinct pages must produce two separate report rows -- each
    carrying that page's own source_type/context/locator -- not collapse
    into one row just because the value repeats. Verified end-to-end:
    SqliteRepository has no uniqueness constraint on `value` (only an
    autoincrement id), Orchestrator's unique_values set only feeds
    CrawlSummary.unique_passwords_found and never filters what gets
    saved, and _build_match_rows() groups by (source_url, value), not
    value alone -- only an identical (page, value) pair collapses, into
    a higher count_in_page, which is the separate scenario the test
    above already covers."""
    same_value = "VISUALPING{abcdef1234567890}"
    match_on_page_y = PasswordMatch(
        value=same_value,
        source_type=SourceType.HTML_TEXT,
        source_url="https://example.com/page-y",
        context_before="found on Y: ",
        context_after=" here",
        locator="line:1,col:0",
    )
    match_on_page_b = PasswordMatch(
        value=same_value,
        source_type=SourceType.HTML_COMMENT,
        source_url="https://example.com/page-b",
        context_before="also on B: ",
        context_after=" there",
        locator="line:5,col:2",
    )

    class RepositoryWithSameValueOnTwoPages:
        def get_matches(self):
            return [match_on_page_y, match_on_page_b]

    async def fake_build_orchestrator(request, event_bus):
        async def cleanup():
            return None

        return (
            FakeOrchestrator(summary=CANNED_SUMMARY),
            RepositoryWithSameValueOnTwoPages(),
            cleanup,
        )

    monkeypatch.setattr(routes, "_build_orchestrator", fake_build_orchestrator)

    response = client.post("/crawls", json=VALID_BODY)
    crawl_id = response.json()["crawl_id"]

    report = client.get(f"/crawls/{crawl_id}/report").json()

    assert len(report["matches"]) == 2, "same password on two distinct pages must not collapse"

    row_y = next(r for r in report["matches"] if r["page_url"] == "https://example.com/page-y")
    row_b = next(r for r in report["matches"] if r["page_url"] == "https://example.com/page-b")

    assert row_y["value"] == row_b["value"] == same_value
    assert row_y["count_in_page"] == 1
    assert row_b["count_in_page"] == 1
    # Each row carries its own page's details, not the other page's.
    assert row_y["source_type"] == "html_text"
    assert row_y["context_before"] == "found on Y: "
    assert row_y["locator"] == "line:1,col:0"
    assert row_b["source_type"] == "html_comment"
    assert row_b["context_before"] == "also on B: "
    assert row_b["locator"] == "line:5,col:2"


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


class SpyOrchestrator:
    """A minimal orchestrator double that just records whether
    pause()/resume()/stop() were called -- for testing the API layer's
    wiring to those methods without a real crawl running."""

    def __init__(self):
        self.paused = False
        self.resumed = False
        self.stopped = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.resumed = True

    def stop(self):
        self.stopped = True


def _running_state(orchestrator=None) -> _CrawlState:
    state = _CrawlState()
    state.status = routes.CrawlStatus.RUNNING
    state.orchestrator = orchestrator if orchestrator is not None else SpyOrchestrator()
    return state


def test_pause_running_crawl_calls_orchestrator_and_updates_status(client):
    orchestrator = SpyOrchestrator()
    routes._crawls["c1"] = _running_state(orchestrator)

    response = client.post("/crawls/c1/pause")

    assert response.status_code == 200
    assert response.json() == {"crawl_id": "c1", "status": "paused"}
    assert orchestrator.paused is True


def test_resume_paused_crawl_calls_orchestrator_and_updates_status(client):
    orchestrator = SpyOrchestrator()
    state = _running_state(orchestrator)
    state.status = routes.CrawlStatus.PAUSED
    routes._crawls["c1"] = state

    response = client.post("/crawls/c1/resume")

    assert response.status_code == 200
    assert response.json() == {"crawl_id": "c1", "status": "running"}
    assert orchestrator.resumed is True


def test_stop_running_crawl_calls_orchestrator_and_moves_to_stopping(client):
    orchestrator = SpyOrchestrator()
    routes._crawls["c1"] = _running_state(orchestrator)

    response = client.post("/crawls/c1/stop")

    assert response.status_code == 200
    assert response.json() == {"crawl_id": "c1", "status": "stopping"}
    assert orchestrator.stopped is True


def test_stop_paused_crawl_is_allowed(client):
    orchestrator = SpyOrchestrator()
    state = _running_state(orchestrator)
    state.status = routes.CrawlStatus.PAUSED
    routes._crawls["c1"] = state

    response = client.post("/crawls/c1/stop")

    assert response.status_code == 200
    assert orchestrator.stopped is True


@pytest.mark.parametrize("action", ["pause", "resume", "stop"])
def test_control_action_for_unknown_crawl_returns_404(client, action):
    response = client.post(f"/crawls/does-not-exist/{action}")

    assert response.status_code == 404


@pytest.mark.parametrize("action", ["pause", "resume", "stop"])
def test_control_action_before_crawl_started_returns_409(client, action):
    routes._crawls["not-started"] = _CrawlState()  # orchestrator is still None

    response = client.post(f"/crawls/not-started/{action}")

    assert response.status_code == 409


def test_pause_already_paused_crawl_returns_409_and_does_not_call_orchestrator(client):
    orchestrator = SpyOrchestrator()
    state = _running_state(orchestrator)
    state.status = routes.CrawlStatus.PAUSED
    routes._crawls["c1"] = state

    response = client.post("/crawls/c1/pause")

    assert response.status_code == 409
    assert orchestrator.paused is False


def test_resume_running_crawl_returns_409_and_does_not_call_orchestrator(client):
    orchestrator = SpyOrchestrator()
    routes._crawls["c1"] = _running_state(orchestrator)

    response = client.post("/crawls/c1/resume")

    assert response.status_code == 409
    assert orchestrator.resumed is False


def test_stop_finished_crawl_returns_409_and_does_not_call_orchestrator(client):
    orchestrator = SpyOrchestrator()
    state = _running_state(orchestrator)
    state.status = routes.CrawlStatus.FINISHED
    routes._crawls["c1"] = state

    response = client.post("/crawls/c1/stop")

    assert response.status_code == 409
    assert orchestrator.stopped is False


def test_report_returns_409_while_paused(client):
    state = _running_state()
    state.status = routes.CrawlStatus.PAUSED
    routes._crawls["c1"] = state

    response = client.get("/crawls/c1/report")

    assert response.status_code == 409


def test_report_returns_409_while_stopping(client):
    state = _running_state()
    state.status = routes.CrawlStatus.STOPPING
    routes._crawls["c1"] = state

    response = client.get("/crawls/c1/report")

    assert response.status_code == 409


def test_report_available_once_stopped(client):
    state = _running_state()
    state.status = routes.CrawlStatus.STOPPED
    state.report = CANNED_SUMMARY
    routes._crawls["c1"] = state

    response = client.get("/crawls/c1/report")

    assert response.status_code == 200
    assert response.json()["summary"]["unique_passwords_found"] == 1


def test_run_crawl_finalizes_to_stopped_not_finished_when_stop_was_requested(monkeypatch):
    """Regression test for the race this design deliberately avoids: a
    stop request moves status to the transitional STOPPING (not the
    terminal STOPPED) precisely so `_run_crawl` -- not the stop endpoint
    -- is what finalizes to STOPPED, always after `state.report` is
    already set. Simulates stop_crawl() having flipped the status to
    STOPPING while orchestrator.run() was still in flight."""
    crawl_id = "c1"
    state = _CrawlState()
    routes._crawls[crawl_id] = state

    class StoppingOrchestrator:
        async def run(self):
            state.status = routes.CrawlStatus.STOPPING
            return CANNED_SUMMARY

    async def fake_build_orchestrator(request, event_bus):
        async def cleanup():
            return None

        return StoppingOrchestrator(), FakeRepository(), cleanup

    monkeypatch.setattr(routes, "_build_orchestrator", fake_build_orchestrator)

    request = CrawlRequest(**VALID_BODY)
    asyncio.run(routes._run_crawl(crawl_id, request))

    assert state.status == routes.CrawlStatus.STOPPED
    assert state.report == CANNED_SUMMARY


def test_crawl_request_max_pages_defaults_to_1000():
    request = CrawlRequest(url="https://example.com", username="alice", password="s3cret")

    assert request.max_pages == 1000


def test_crawl_request_max_pages_is_overridable():
    request = CrawlRequest(
        url="https://example.com", username="alice", password="s3cret", max_pages=5
    )

    assert request.max_pages == 5


def test_build_orchestrator_passes_request_max_pages_through(monkeypatch):
    captured_kwargs = {}

    class CapturingOrchestrator:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(routes, "Orchestrator", CapturingOrchestrator)

    request = CrawlRequest(
        url="https://example.com", username="alice", password="s3cret", max_pages=42
    )

    async def scenario():
        _orchestrator, _repository, cleanup = await routes._build_orchestrator(request, EventBus())
        await cleanup()

    asyncio.run(scenario())

    assert captured_kwargs["max_pages"] == 42
