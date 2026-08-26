import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api import routes
from app.api.routes import _CrawlState, app
from app.api.websocket import crawl_progress_websocket
from app.events import CRAWL_FINISHED, MATCH_FOUND, PAGE_FETCHED
from app.models import CrawlSummary, PageResult, PasswordMatch, SourceType

CANNED_SUMMARY = CrawlSummary(
    pages_visited=1,
    resources_checked=1,
    unique_passwords_found=1,
    queue_empty=True,
    started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    finished_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code


def run(coro):
    return asyncio.run(coro)


def test_sends_events_in_order_and_closes_on_crawl_finished():
    routes._crawls.clear()
    routes._crawls["c1"] = _CrawlState()
    page = PageResult(
        url="https://example.com/",
        status_code=200,
        fetched_at=datetime.now(timezone.utc),
    )
    match = PasswordMatch(
        value="VISUALPING{abcdef1234567890}",
        source_type=SourceType.HTML_TEXT,
        source_url="https://example.com/",
        context_before="",
        context_after="",
        locator="line:1,col:0",
    )

    async def scenario():
        ws = FakeWebSocket()
        task = asyncio.create_task(crawl_progress_websocket(ws, "c1"))
        await asyncio.sleep(0)

        state = routes._crawls["c1"]
        state.event_bus.publish(PAGE_FETCHED, page)
        await asyncio.sleep(0)
        state.event_bus.publish(MATCH_FOUND, match)
        await asyncio.sleep(0)
        state.event_bus.publish(CRAWL_FINISHED, CANNED_SUMMARY)

        await asyncio.wait_for(task, timeout=1)
        return ws

    ws = run(scenario())

    assert ws.accepted
    assert [m["type"] for m in ws.sent] == [PAGE_FETCHED, MATCH_FOUND, CRAWL_FINISHED]
    assert ws.sent[0]["payload"]["url"] == "https://example.com/"
    assert ws.sent[1]["payload"]["value"] == "VISUALPING{abcdef1234567890}"
    assert ws.sent[2]["payload"]["unique_passwords_found"] == 1
    assert ws.closed


def test_unknown_crawl_id_closes_immediately():
    routes._crawls.clear()

    async def scenario():
        ws = FakeWebSocket()
        await crawl_progress_websocket(ws, "does-not-exist")
        return ws

    ws = run(scenario())

    assert ws.accepted
    assert ws.sent == []
    assert ws.closed
    assert ws.close_code == 4404


def test_already_finished_crawl_sends_summary_and_closes():
    routes._crawls.clear()
    state = _CrawlState()
    from app.api.routes import CrawlStatus

    state.status = CrawlStatus.FINISHED
    state.report = CANNED_SUMMARY
    routes._crawls["c2"] = state

    async def scenario():
        ws = FakeWebSocket()
        await crawl_progress_websocket(ws, "c2")
        return ws

    ws = run(scenario())

    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == CRAWL_FINISHED
    assert ws.sent[0]["payload"]["unique_passwords_found"] == 1
    assert ws.closed


def test_real_websocket_transport_via_testclient_after_crawl_already_finished():
    routes._crawls.clear()
    from app.api.routes import CrawlStatus

    state = _CrawlState()
    state.status = CrawlStatus.FINISHED
    state.report = CANNED_SUMMARY
    routes._crawls["c3"] = state

    client = TestClient(app)
    with client.websocket_connect("/ws/crawls/c3") as websocket:
        message = websocket.receive_json()
        assert message["type"] == CRAWL_FINISHED
        assert message["payload"]["unique_passwords_found"] == 1
