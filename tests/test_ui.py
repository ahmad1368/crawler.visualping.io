import asyncio
import socket
import threading
import time
from datetime import datetime, timezone

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.api import routes
from app.api import websocket as _websocket_module  # noqa: F401  (registers /ws/crawls/{id})
from app.api.routes import app
from app.events import CRAWL_FINISHED, PAGE_FETCHED
from app.models import CrawlSummary, PageResult


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeOrchestrator:
    """Simulates a two-page crawl with a visible delay between pages, so
    the UI test can observe a genuinely in-progress crawl."""

    def __init__(self, event_bus, pages: list[PageResult]) -> None:
        self._event_bus = event_bus
        self._pages = pages

    async def run(self) -> CrawlSummary:
        for page in self._pages:
            await asyncio.sleep(0.2)
            self._event_bus.publish(PAGE_FETCHED, page)

        summary = CrawlSummary(
            pages_visited=len(self._pages),
            resources_checked=len(self._pages),
            unique_passwords_found=0,
            queue_empty=True,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        self._event_bus.publish(CRAWL_FINISHED, summary)
        return summary


@pytest.fixture
def live_server(monkeypatch):
    async def fake_build_orchestrator(request, event_bus):
        pages = [
            PageResult(
                url=str(request.url),
                status_code=200,
                fetched_at=datetime.now(timezone.utc),
            ),
            PageResult(
                url=str(request.url) + "page2",
                status_code=200,
                fetched_at=datetime.now(timezone.utc),
            ),
        ]
        orchestrator = _FakeOrchestrator(event_bus, pages)

        async def cleanup() -> None:
            return None

        return orchestrator, cleanup

    monkeypatch.setattr(routes, "_build_orchestrator", fake_build_orchestrator)

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("test server did not start in time")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_run_button_disables_during_crawl_and_log_updates_live(live_server):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(live_server + "/")

            page.fill("#url", "https://example.com")
            page.fill("#username", "alice")
            page.fill("#password", "s3cret")

            run_button = page.locator("#run-button")
            run_button.click()

            assert run_button.is_disabled()

            page.wait_for_selector("#log p:has-text('Fetched')")
            assert run_button.is_disabled(), "button must stay disabled while crawl is in progress"

            page.wait_for_function(
                "document.querySelectorAll('#log p').length >= 3", timeout=5000
            )
            log_lines = page.locator("#log p").all_inner_texts()
            fetched_lines = [line for line in log_lines if line.startswith("Fetched")]
            assert len(fetched_lines) == 2

            page.wait_for_function(
                "document.getElementById('run-button').disabled === false", timeout=5000
            )
            final_lines = page.locator("#log p").all_inner_texts()
            assert any("Crawl finished" in line for line in final_lines)
        finally:
            browser.close()
