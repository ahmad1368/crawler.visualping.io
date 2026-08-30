"""Pydantic models shared across the crawler, storage, and API layers.

`PasswordMatch` and anything containing it are sensitive: they hold an
exposed secret plus its surrounding context. Trace where these end up
(storage rows, API responses, log lines) rather than treating them as
plain data.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class SourceType(str, Enum):
    HTML_TEXT = "html_text"
    HTML_COMMENT = "html_comment"
    CSS = "css"
    JS = "js"
    JS_CHARCODE = "js_charcode"
    HTTP_HEADER = "http_header"
    COOKIE = "cookie"
    IMAGE_METADATA = "image_metadata"
    IMAGE_OCR = "image_ocr"
    BINARY = "binary"


class PasswordMatch(BaseModel):
    value: str
    source_type: SourceType
    source_url: str
    context_before: str
    context_after: str
    locator: str


class PageResult(BaseModel):
    url: str
    status_code: int
    fetched_at: datetime
    matches: list[PasswordMatch] = []


class CrawlSummary(BaseModel):
    pages_visited: int
    resources_checked: int
    unique_passwords_found: int
    queue_empty: bool
    started_at: datetime
    finished_at: datetime | None = None


class PageFetchData(BaseModel):
    """Everything an extractor pass needs for one page, read back out of
    storage instead of a live fetch (issue #72's replay path). Sensitive
    the same way a snapshot is: `content` is the full raw body, and
    `headers`/`cookies` are the target site's raw response headers/cookies
    (not just whichever bytes happened to match the password regex)."""

    url: str
    content: bytes
    content_type: str | None
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
