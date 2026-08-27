import asyncio
import socket
import threading
import time
from datetime import datetime, timezone

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.api import routes
from app.events import CRAWL_FINISHED, PAGE_FETCHED
from app.main import app  # imports app.api.websocket for /ws/crawls/{id} too
from app.models import CrawlSummary, PageResult, PasswordMatch, SourceType


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


HTML_MATCH_VALUE = "VISUALPING{abcdef1234567890}"
EXIF_MATCH_VALUE = "VISUALPING{0123456789abcdef}"
PAGE_SNAPSHOT = "<html><body>the secret is VISUALPING{abcdef1234567890} right there</body></html>"


class _FakeRepository:
    def get_matches(self):
        return [
            PasswordMatch(
                value=HTML_MATCH_VALUE,
                source_type=SourceType.HTML_TEXT,
                source_url="https://example.com",
                context_before="secret is ",
                context_after=" for now",
                locator="line:1,col:0",
            ),
            PasswordMatch(
                value=EXIF_MATCH_VALUE,
                source_type=SourceType.IMAGE_METADATA,
                source_url="https://example.com/photo.jpg",
                context_before="note: ",
                context_after=" -end",
                locator="exif:UserComment",
            ),
        ]

    def get_snapshot(self, url):
        if url == "https://example.com":
            return PAGE_SNAPSHOT.encode()
        return None


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


class _PausableFakeOrchestrator:
    """Like `_FakeOrchestrator`, but implements real pause()/resume()/
    stop() semantics (an asyncio.Event gating the loop, checked before
    each page rather than mid-page) so a browser-driven test can verify
    actual behavior, not just button enabled/disabled state."""

    def __init__(self, event_bus, pages: list[PageResult], page_delay: float = 0.2) -> None:
        self._event_bus = event_bus
        self._pages = pages
        self._page_delay = page_delay
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_requested = False

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def stop(self) -> None:
        self._stop_requested = True
        self._pause_event.set()

    async def run(self) -> CrawlSummary:
        processed = 0
        for page in self._pages:
            await self._pause_event.wait()
            if self._stop_requested:
                break
            await asyncio.sleep(self._page_delay)
            self._event_bus.publish(PAGE_FETCHED, page)
            processed += 1

        summary = CrawlSummary(
            pages_visited=processed,
            resources_checked=processed,
            unique_passwords_found=0,
            queue_empty=processed == len(self._pages),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        self._event_bus.publish(CRAWL_FINISHED, summary)
        return summary


def _run_live_server(monkeypatch, fake_build_orchestrator):
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

        return orchestrator, _FakeRepository(), cleanup

    yield from _run_live_server(monkeypatch, fake_build_orchestrator)


@pytest.fixture
def live_server_pausable(monkeypatch):
    async def fake_build_orchestrator(request, event_bus):
        pages = [
            PageResult(
                url=str(request.url) + f"page{i}",
                status_code=200,
                fetched_at=datetime.now(timezone.utc),
            )
            for i in range(8)
        ]
        orchestrator = _PausableFakeOrchestrator(event_bus, pages, page_delay=0.2)

        async def cleanup() -> None:
            return None

        return orchestrator, _FakeRepository(), cleanup

    yield from _run_live_server(monkeypatch, fake_build_orchestrator)


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
            assert (
                page.locator("#summary-resources-checked").inner_text() == "1"
            ), "resources-checked stat should update live from page_fetched events"

            page.wait_for_function("document.querySelectorAll('#log p').length >= 3", timeout=5000)
            log_lines = page.locator("#log p").all_inner_texts()
            fetched_lines = [line for line in log_lines if line.startswith("Fetched")]
            assert len(fetched_lines) == 2

            page.wait_for_function(
                "document.getElementById('run-button').disabled === false", timeout=5000
            )
            final_lines = page.locator("#log p").all_inner_texts()
            assert any("Crawl finished" in line for line in final_lines)

            page.wait_for_function(
                "document.getElementById('summary-pages-visited').textContent === '2'"
            )
            assert page.locator("#summary-resources-checked").inner_text() == "2"
            assert page.locator("#summary-queue-empty").inner_text() == "Yes"

            page.wait_for_function(
                "document.querySelectorAll('#results-table tbody tr').length >= 2"
            )
            rows = page.locator("#results-table tbody tr")
            html_row = rows.filter(has_text="html_text")
            exif_row = rows.filter(has_text="image_metadata")

            html_password_button = html_row.locator("button.password-cell")
            assert html_password_button.inner_text() == HTML_MATCH_VALUE
            assert html_row.locator("td").nth(4).inner_text() == "1"

            # html_text: opens the real snapshot, highlights the match, and
            # scrolls it into view.
            html_password_button.click()
            page.wait_for_selector("#snapshot-overlay[style*='flex']")
            mark = page.locator("#snapshot-body mark#snapshot-mark")
            page.wait_for_function(
                "document.querySelector('#snapshot-body mark#snapshot-mark') !== null"
            )
            assert mark.inner_text() == HTML_MATCH_VALUE
            assert HTML_MATCH_VALUE in page.locator("#snapshot-body pre").inner_text()

            page.click("#snapshot-close")
            page.wait_for_selector("#snapshot-overlay", state="hidden")

            # image_metadata: non-text source, shows the locator + context
            # fallback instead of trying to fetch/scroll a snapshot.
            exif_password_button = exif_row.locator("button.password-cell")
            assert exif_password_button.inner_text() == EXIF_MATCH_VALUE
            exif_password_button.click()
            page.wait_for_selector("#snapshot-locator")
            assert "exif:UserComment" in page.locator("#snapshot-locator").inner_text()
            fallback_mark = page.locator("#snapshot-body mark")
            assert fallback_mark.inner_text() == EXIF_MATCH_VALUE
        finally:
            browser.close()


def _fetched_count(page) -> int:
    return len(
        [line for line in page.locator("#log p").all_inner_texts() if line.startswith("Fetched")]
    )


def test_pause_resume_stop_controls(live_server_pausable):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(live_server_pausable + "/")

            page.fill("#url", "https://example.com")
            page.fill("#username", "alice")
            page.fill("#password", "s3cret")

            run_button = page.locator("#run-button")
            pause_button = page.locator("#pause-button")
            resume_button = page.locator("#resume-button")
            stop_button = page.locator("#stop-button")

            # Idle: only Run is enabled.
            assert run_button.is_enabled()
            assert pause_button.is_disabled()
            assert resume_button.is_disabled()
            assert stop_button.is_disabled()

            run_button.click()

            # Running: Run/Resume disabled, Pause/Stop enabled.
            assert run_button.is_disabled()
            assert resume_button.is_disabled()
            page.wait_for_function("!document.getElementById('pause-button').disabled")
            assert stop_button.is_enabled()

            page.wait_for_selector("#log p:has-text('Fetched')")
            pause_button.click()
            page.wait_for_selector("#log p:has-text('Crawl paused')")

            # Paused: Run/Pause disabled, Resume/Stop enabled.
            assert run_button.is_disabled()
            assert pause_button.is_disabled()
            assert resume_button.is_enabled()
            assert stop_button.is_enabled()

            # Let any already-in-flight page settle, then prove the crawl
            # genuinely stalls -- not just that the button looks disabled.
            page.wait_for_timeout(300)
            count_at_pause = _fetched_count(page)
            page.wait_for_timeout(600)  # ~3 page-intervals worth, paused
            assert _fetched_count(page) == count_at_pause, "no progress should happen while paused"

            resume_button.click()
            page.wait_for_selector("#log p:has-text('Crawl resumed')")

            # Running again: same enabled/disabled shape as the first
            # running check above.
            assert run_button.is_disabled()
            assert pause_button.is_enabled()
            assert resume_button.is_disabled()
            assert stop_button.is_enabled()

            page.wait_for_function(
                f"Array.from(document.querySelectorAll('#log p')).filter("
                f"p => p.textContent.startsWith('Fetched')).length > {count_at_pause}",
                timeout=3000,
            )

            stop_button.click()
            page.wait_for_selector("#log p:has-text('Stopping crawl')")

            # Stopping: nothing re-enables until crawl_finished arrives.
            assert run_button.is_disabled()
            assert pause_button.is_disabled()
            assert resume_button.is_disabled()
            assert stop_button.is_disabled()

            page.wait_for_function(
                "document.getElementById('run-button').disabled === false", timeout=5000
            )

            # Idle again: back to only Run enabled, and the crawl stopped
            # early -- fewer than all 8 fake pages were processed.
            assert pause_button.is_disabled()
            assert resume_button.is_disabled()
            assert stop_button.is_disabled()
            final_lines = page.locator("#log p").all_inner_texts()
            assert any("Crawl finished" in line for line in final_lines)

            page.wait_for_function(
                "document.getElementById('summary-pages-visited').textContent !== '0'"
            )
            pages_visited = int(page.locator("#summary-pages-visited").inner_text())
            assert 0 < pages_visited < 8, "stop should end the crawl before all pages are processed"
        finally:
            browser.close()
