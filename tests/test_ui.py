import asyncio
import socket
import threading
import time
from datetime import datetime, timezone

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.api import routes
from app.events import CRAWL_FINISHED, MATCH_FOUND, PAGE_FETCHED
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


STREAM_MATCH_VALUE_A = "VISUALPING{aaaaaaaaaaaaaaaa}"
STREAM_MATCH_VALUE_B = "VISUALPING{bbbbbbbbbbbbbbbb}"


def _streamed_matches() -> list[PasswordMatch]:
    # Two matches for the SAME (source_url, value) pair, then one for a
    # different pair -- exercises the client's count_in_page grouping,
    # not just "a row shows up."
    return [
        PasswordMatch(
            value=STREAM_MATCH_VALUE_A,
            source_type=SourceType.HTML_TEXT,
            source_url="https://example.com/page-a",
            context_before="before-a",
            context_after="after-a",
            locator="line:1,col:0",
        ),
        PasswordMatch(
            value=STREAM_MATCH_VALUE_A,
            source_type=SourceType.HTML_TEXT,
            source_url="https://example.com/page-a",
            context_before="before-a",
            context_after="after-a",
            locator="line:1,col:0",
        ),
        PasswordMatch(
            value=STREAM_MATCH_VALUE_B,
            source_type=SourceType.HTML_TEXT,
            source_url="https://example.com/page-b",
            context_before="before-b",
            context_after="after-b",
            locator="line:2,col:0",
        ),
    ]


class _MatchStreamingFakeRepository:
    """Backs the final GET /report reconciliation pass with the same
    matches streamed live -- so the test can check the post-finish table
    matches what was already shown before crawl_finished."""

    def get_matches(self):
        return _streamed_matches()

    def get_snapshot(self, url):
        return None


class _MatchStreamingFakeOrchestrator:
    """Publishes MATCH_FOUND events with a real delay between them, so a
    UI test can observe the results table populate live, before
    crawl_finished -- including two matches for the same (source_url,
    value) pair, to prove the client groups them into one row with
    count_in_page=2 rather than a duplicate row."""

    def __init__(self, event_bus) -> None:
        self._event_bus = event_bus

    async def run(self) -> CrawlSummary:
        for match in _streamed_matches():
            await asyncio.sleep(0.15)
            self._event_bus.publish(MATCH_FOUND, match)

        # A real gap before crawl_finished, so the test has a reliable
        # window to observe "all matches streamed, crawl not yet done."
        await asyncio.sleep(0.3)

        summary = CrawlSummary(
            pages_visited=2,
            resources_checked=2,
            unique_passwords_found=2,
            queue_empty=True,
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


@pytest.fixture
def live_server_match_streaming(monkeypatch):
    async def fake_build_orchestrator(request, event_bus):
        orchestrator = _MatchStreamingFakeOrchestrator(event_bus)

        async def cleanup() -> None:
            return None

        return orchestrator, _MatchStreamingFakeRepository(), cleanup

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


def test_results_table_populates_live_before_crawl_finishes(live_server_match_streaming):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(live_server_match_streaming + "/")

            page.fill("#url", "https://example.com")
            page.fill("#username", "alice")
            page.fill("#password", "s3cret")
            page.click("#run-button")

            # First match_found (page-a, value A) -- a row appears
            # immediately, well before the crawl finishes.
            page.wait_for_function(
                "document.querySelectorAll('#results-table tbody tr').length >= 1", timeout=3000
            )
            assert page.locator("#log p:has-text('Crawl finished')").count() == 0
            rows = page.locator("#results-table tbody tr")
            assert rows.count() == 1
            assert rows.nth(0).locator("td").nth(2).inner_text() == STREAM_MATCH_VALUE_A
            assert rows.nth(0).locator("td").nth(4).inner_text() == "1"

            # Second match_found for the SAME (source_url, value) pair
            # increments the existing row's count rather than adding a
            # second row for it.
            page.wait_for_function(
                "document.querySelectorAll('#results-table tbody tr')[0]"
                ".children[4].textContent === '2'",
                timeout=3000,
            )
            assert page.locator("#results-table tbody tr").count() == 1

            # Third match_found, a different (source_url, value) pair,
            # adds a second row -- still before crawl_finished.
            page.wait_for_function(
                "document.querySelectorAll('#results-table tbody tr').length >= 2", timeout=3000
            )
            assert page.locator("#log p:has-text('Crawl finished')").count() == 0

            # Crawl finishes; the GET /report reconciliation pass lands on
            # the same final shape the live stream already showed.
            page.wait_for_selector("#log p:has-text('Crawl finished')", timeout=3000)
            page.wait_for_function(
                "document.getElementById('run-button').disabled === false", timeout=3000
            )
            final_rows = page.locator("#results-table tbody tr")
            assert final_rows.count() == 2
            values = page.locator("#results-table tbody tr td:nth-child(3)").all_inner_texts()
            assert set(values) == {STREAM_MATCH_VALUE_A, STREAM_MATCH_VALUE_B}
        finally:
            browser.close()
