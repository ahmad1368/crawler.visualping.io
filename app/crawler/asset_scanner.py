"""Post-crawl static-asset completeness audit (issue #99).

Structural link discovery (`BrowserFetcher`'s rendered-DOM links, captured
network requests, and click-interaction discovery, issues #5/#67) can
still miss a `/static/...` asset that's only ever referenced as a string
literal -- built up in JS and never actually requested during the crawl's
own page loads (a conditional/lazy code path, a URL only used by a
feature the crawl's automated clicks never triggered). `audit_static_
assets()` is a safety net: after the primary crawl's frontier empties, it
re-scans every already-fetched page's stored text for `/static/...`
references, diffs that against what was actually fetched, and fetches
whatever's missing -- through the exact same extractor pipeline as any
other page, so a password hidden in a genuinely-missed asset still gets
found.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from app.crawler.fetcher import HttpFetcher
from app.events import MATCH_FOUND, PAGE_FETCHED, EventBus
from app.extractors.base import ExtractorRegistry
from app.extractors.headers_cookies import HeaderCookieExtractor
from app.models import AssetRecord, AssetStatus, PageResult, StaticAssetCompletenessReport
from app.storage.repository import Repository

logger = logging.getLogger(__name__)

# Content types worth text-scanning for /static/... references. Anything
# else (images, fonts, binary blobs) can't meaningfully contain a URL
# string to extract -- skip it rather than decoding arbitrary bytes as
# text for no reason.
_SCANNABLE_CONTENT_TYPE_PREFIXES = (
    "text/",
    "application/javascript",
    "application/x-javascript",
    "application/json",
    "application/xml",
)

# Fallback: any literal /static/... path anywhere in the text, catching
# references built dynamically in JS (string concatenation, fetch calls)
# that the tag-attribute pattern below would never match.
_FALLBACK_PATTERN = re.compile(r"/static/[a-zA-Z0-9_\-./]+(?:\?[a-zA-Z0-9_\-=&.]*)?")

# The tag/attribute pairs called out explicitly in the spec -- a narrower,
# more precise pass than the fallback regex, but only catches assets
# present in real HTML markup, not ones referenced purely in script text.
_TAG_ATTR_PATTERN = re.compile(
    r"""<(?:script|link|img|source|embed)\b[^>]*?\b(?:src|href)\s*=\s*["'](?P<url>[^"']+)["']""",
    re.IGNORECASE,
)


def find_static_asset_references(text: str) -> set[str]:
    """Return every distinct `/static/...` path referenced in `text`,
    combining tag-attribute extraction and the fallback whole-text regex."""
    found: set[str] = set()
    for match in _TAG_ATTR_PATTERN.finditer(text):
        candidate = match.group("url")
        if "/static/" in candidate:
            found.add(candidate[candidate.index("/static/") :])
    for match in _FALLBACK_PATTERN.finditer(text):
        found.add(match.group(0))
    return found


def _is_scannable(content_type: str | None) -> bool:
    if content_type is None:
        return False
    normalized = content_type.split(";", 1)[0].strip().lower()
    return normalized.startswith(_SCANNABLE_CONTENT_TYPE_PREFIXES)


class MasterAssetRegistry:
    """Tracks every static-asset URL discovered during a crawl, whether
    fetched by the primary crawl or by this audit pass. Safe under the
    orchestrator's asyncio concurrency model (an `asyncio.Lock`, not a
    threading lock -- this codebase has no real threads in the crawl
    path)."""

    def __init__(self) -> None:
        self._records: dict[str, AssetRecord] = {}
        self._lock = asyncio.Lock()

    async def mark(
        self,
        url: str,
        status: AssetStatus,
        origin_page: str,
        content_type: str | None = None,
    ) -> None:
        async with self._lock:
            self._records[url] = AssetRecord(
                url=url, status=status, content_type=content_type, origin_page=origin_page
            )

    def records(self) -> list[AssetRecord]:
        return list(self._records.values())


async def audit_static_assets(
    repository: Repository,
    http_fetcher: HttpFetcher,
    extractor_registry: ExtractorRegistry,
    header_cookie_extractor: HeaderCookieExtractor,
    event_bus: EventBus | None = None,
    unique_values: set[str] | None = None,
) -> StaticAssetCompletenessReport:
    """Post-crawl completeness pass -- see module docstring.

    If `unique_values` is given, every password value found on a
    newly-fetched asset is added to it in place -- lets a caller (the
    `Orchestrator`) fold audit-recovered matches into its own running
    unique-password count without this function needing to know that
    count's shape."""
    registry = MasterAssetRegistry()
    scanned_pages = [
        page for page in repository.get_all_page_fetch_data() if _is_scannable(page.content_type)
    ]
    visited = set(repository.get_visited_urls())

    # resolved asset URL -> the page it was referenced from (first one wins)
    referenced: dict[str, str] = {}
    for page in scanned_pages:
        text = page.content.decode("utf-8", errors="replace")
        for raw_ref in find_static_asset_references(text):
            resolved = urljoin(page.url, raw_ref)
            referenced.setdefault(resolved, page.url)

    for url, origin in referenced.items():
        status = AssetStatus.FETCHED if url in visited else AssetStatus.PENDING
        await registry.mark(url, status, origin)

    missing = [url for url in referenced if url not in visited]

    for url in missing:
        origin = referenced[url]
        try:
            fetch_result = await http_fetcher.fetch(url)
        except Exception:
            # A single unreachable asset must not abort the audit -- same
            # per-URL isolation as the primary crawl's own failure path.
            await registry.mark(url, AssetStatus.FAILED, origin)
            continue

        content_type = fetch_result.content_type or ""
        matches = list(extractor_registry.run_all(fetch_result.content, content_type, url))
        matches.extend(
            header_cookie_extractor.extract(fetch_result.headers, fetch_result.cookies, url)
        )

        page_result = PageResult(
            url=url,
            status_code=fetch_result.status_code,
            fetched_at=datetime.now(timezone.utc),
            matches=matches,
        )
        repository.save_page(
            page_result,
            snapshot=fetch_result.content,
            content_type=fetch_result.content_type,
            headers=fetch_result.headers,
            cookies=fetch_result.cookies,
        )
        await registry.mark(
            url, AssetStatus.FETCHED, origin, content_type=fetch_result.content_type
        )

        if event_bus is not None:
            event_bus.publish(PAGE_FETCHED, page_result)
            for match in matches:
                event_bus.publish(MATCH_FOUND, match)
        if unique_values is not None:
            unique_values.update(match.value for match in matches)

    total_static_references_found = len(referenced)
    missing_assets_count = len(missing)
    # Coverage of the *primary* crawl, not this audit's own remediation --
    # 0 missing means the primary crawl already found everything on its
    # own; a later successful fetch here doesn't retroactively improve it.
    completeness_percentage = (
        100.0
        if total_static_references_found == 0
        else round(
            (total_static_references_found - missing_assets_count)
            / total_static_references_found
            * 100,
            2,
        )
    )

    logger.info(
        "Static-asset completeness scan: audited %d pages for '/static/...' "
        "references -- found %d missing assets.",
        len(scanned_pages),
        missing_assets_count,
    )

    return StaticAssetCompletenessReport(
        total_pages_scanned=len(scanned_pages),
        total_static_references_found=total_static_references_found,
        missing_assets_count=missing_assets_count,
        completeness_percentage=completeness_percentage,
        records=registry.records(),
    )
