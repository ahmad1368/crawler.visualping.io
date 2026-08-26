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
    HTML = "html"
    CSS = "css"
    JS = "js"
    HTTP_HEADER = "http_header"
    COOKIE = "cookie"
    IMAGE_METADATA = "image_metadata"
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
