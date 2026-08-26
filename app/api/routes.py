"""FastAPI HTTP routes: start a crawl, check its status, fetch its report.

`CrawlRequest` carries the target URL and Basic Auth credentials straight
from the caller -- never logged, never echoed back in any response body
here. Each crawl gets its own `SqliteRepository` (a separate `*.db` file
per `crawl_id`), keeping one crawl's persisted matches/snapshots isolated
from another's.
"""

from __future__ import annotations

import sqlite3
import uuid
from enum import Enum

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field, HttpUrl

from app.crawler.browser_fetcher import BrowserFetcher
from app.crawler.fetcher import HttpFetcher
from app.crawler.frontier import UrlFrontier
from app.crawler.orchestrator import Orchestrator
from app.extractors.base import ExtractorRegistry
from app.extractors.binary_fallback import BinaryFallbackExtractor
from app.extractors.css_js import CssJsExtractor
from app.extractors.headers_cookies import HeaderCookieExtractor
from app.extractors.html import HtmlExtractor
from app.extractors.image_exif import ImageExifExtractor
from app.models import CrawlSummary
from app.storage.sqlite import SqliteRepository

app = FastAPI()


class CrawlStatus(str, Enum):
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


class CrawlRequest(BaseModel):
    url: HttpUrl
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    context_chars: int = 80


class CrawlCreatedResponse(BaseModel):
    crawl_id: str


class CrawlStatusResponse(BaseModel):
    crawl_id: str
    status: CrawlStatus


class _CrawlState:
    def __init__(self) -> None:
        self.status = CrawlStatus.RUNNING
        self.report: CrawlSummary | None = None
        self.error: str | None = None


_crawls: dict[str, _CrawlState] = {}


async def _build_orchestrator(request: CrawlRequest):
    """Wire a real `Orchestrator` to live httpx/Playwright resources.

    Returns `(orchestrator, cleanup)`, where `cleanup()` releases those
    resources. This is the single seam integration tests replace to avoid
    launching a real browser or making real network calls.
    """
    client = httpx.AsyncClient()
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch()

    frontier = UrlFrontier(str(request.url))
    registry = ExtractorRegistry()
    registry.register(HtmlExtractor(context_chars=request.context_chars))
    registry.register(CssJsExtractor(context_chars=request.context_chars))
    registry.register(ImageExifExtractor(context_chars=request.context_chars))
    registry.register(BinaryFallbackExtractor(context_chars=request.context_chars))

    orchestrator = Orchestrator(
        frontier=frontier,
        http_fetcher=HttpFetcher(client, request.username, request.password),
        browser_fetcher=BrowserFetcher(browser, request.username, request.password),
        extractor_registry=registry,
        header_cookie_extractor=HeaderCookieExtractor(context_chars=request.context_chars),
        repository=SqliteRepository(sqlite3.connect(f"crawl_{uuid.uuid4().hex}.db")),
    )

    async def cleanup() -> None:
        await browser.close()
        await playwright.stop()
        await client.aclose()

    return orchestrator, cleanup


async def _run_crawl(crawl_id: str, request: CrawlRequest) -> None:
    state = _crawls[crawl_id]
    try:
        orchestrator, cleanup = await _build_orchestrator(request)
        try:
            state.report = await orchestrator.run()
            state.status = CrawlStatus.FINISHED
        finally:
            await cleanup()
    except Exception as exc:
        state.status = CrawlStatus.FAILED
        state.error = str(exc)


@app.post("/crawls", response_model=CrawlCreatedResponse, status_code=202)
async def start_crawl(
    request: CrawlRequest, background_tasks: BackgroundTasks
) -> CrawlCreatedResponse:
    crawl_id = str(uuid.uuid4())
    _crawls[crawl_id] = _CrawlState()
    background_tasks.add_task(_run_crawl, crawl_id, request)
    return CrawlCreatedResponse(crawl_id=crawl_id)


@app.get("/crawls/{crawl_id}/status", response_model=CrawlStatusResponse)
async def get_crawl_status(crawl_id: str) -> CrawlStatusResponse:
    state = _crawls.get(crawl_id)
    if state is None:
        raise HTTPException(status_code=404, detail="crawl not found")
    return CrawlStatusResponse(crawl_id=crawl_id, status=state.status)


@app.get("/crawls/{crawl_id}/report", response_model=CrawlSummary)
async def get_crawl_report(crawl_id: str) -> CrawlSummary:
    state = _crawls.get(crawl_id)
    if state is None:
        raise HTTPException(status_code=404, detail="crawl not found")
    if state.status is CrawlStatus.RUNNING:
        raise HTTPException(status_code=409, detail="crawl not finished yet")
    if state.status is CrawlStatus.FAILED:
        raise HTTPException(status_code=500, detail=state.error or "crawl failed")
    return state.report
