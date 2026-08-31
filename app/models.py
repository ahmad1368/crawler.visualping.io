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
    IMAGE_LSB = "image_lsb"
    BINARY = "binary"
    CLIENT_STORAGE = "client_storage"
    REDIRECT = "redirect"
    BASE64_HEX = "base64_hex"
    REVERSED_TEXT = "reversed_text"
    ROT13 = "rot13"
    CONTENT_NEGOTIATION = "content_negotiation"


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


class AssetStatus(str, Enum):
    FETCHED = "fetched"
    PENDING = "pending"
    FAILED = "failed"


class AssetRecord(BaseModel):
    """One `/static/...` URL tracked by `MasterAssetRegistry` (issue #99)."""

    url: str
    status: AssetStatus
    content_type: str | None = None
    origin_page: str


class StaticAssetCompletenessReport(BaseModel):
    """Result of the post-crawl static-asset audit (issue #99): compares
    every `/static/...` reference found across stored pages against what
    the primary crawl actually fetched, then fetches any gap. `missing_
    assets_count`/`completeness_percentage` describe the *primary crawl's*
    coverage -- the number found missing before this audit's own fetch
    tried to close the gap, not whether that remediation succeeded."""

    total_pages_scanned: int
    total_static_references_found: int
    missing_assets_count: int
    completeness_percentage: float
    records: list[AssetRecord] = []


class ContentNegotiationReport(BaseModel):
    """Result of the post-crawl content-negotiation probe (issue #103):
    re-requests a bounded, representative sample of already-crawled HTML
    pages with alternate Accept/X-Requested-With headers, looking for a
    payload only served under a specific negotiation -- an alternate
    representation a normal crawl would never see."""

    pages_probed: int
    headers_tested: list[str]
    matches_found: int


class CrawlSummary(BaseModel):
    pages_visited: int
    resources_checked: int
    unique_passwords_found: int
    queue_empty: bool
    started_at: datetime
    finished_at: datetime | None = None
    asset_completeness: StaticAssetCompletenessReport | None = None
    content_negotiation: ContentNegotiationReport | None = None


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
