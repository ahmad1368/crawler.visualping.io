"""Re-run extraction against a completed crawl's already-persisted
snapshots, with zero network or browser calls (issue #72).

Fetching -- an `HttpFetcher` request plus a full `BrowserFetcher`
Playwright navigation per HTML page -- is the actual bottleneck in a
crawl; extraction itself is a fast, pure-CPU regex pass. Since every
page's raw bytes, content_type, and response headers/cookies are already
durably stored (`Repository.save_page()`, issue #14, extended in #72),
tuning an extractor and re-checking all matches doesn't need a live
re-crawl: this replays extraction over what's already on disk.

Deliberately read-only: `replay_extraction()` returns fresh matches
without writing them back to the repository. A live crawl's
`Orchestrator` is the only thing that ever persists a `PasswordMatch` --
replaying doesn't touch the `matches` table, so it can't duplicate or
corrupt what a real crawl already recorded, and is safe to call
repeatedly (e.g. once per extractor-tuning iteration) with no side
effects to undo.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.extractors.base import ExtractorRegistry
from app.extractors.headers_cookies import HeaderCookieExtractor
from app.models import CrawlSummary, PasswordMatch
from app.storage.repository import Repository


def replay_extraction(
    repository: Repository,
    extractor_registry: ExtractorRegistry,
    header_cookie_extractor: HeaderCookieExtractor,
) -> tuple[CrawlSummary, list[PasswordMatch]]:
    """Re-run every registered extractor against every page's stored
    fetch data in `repository`. Returns a `CrawlSummary` shaped the same
    way a live crawl's would (so API consumers don't need a separate
    response shape) and the full list of matches found -- not persisted,
    the caller decides what to do with them."""
    started_at = datetime.now(timezone.utc)
    all_matches: list[PasswordMatch] = []
    unique_values: set[str] = set()
    pages_processed = 0

    for page_data in repository.get_all_page_fetch_data():
        matches = list(
            extractor_registry.run_all(
                page_data.content, page_data.content_type or "", page_data.url
            )
        )
        matches.extend(
            header_cookie_extractor.extract(page_data.headers, page_data.cookies, page_data.url)
        )
        all_matches.extend(matches)
        unique_values.update(match.value for match in matches)
        pages_processed += 1

    summary = CrawlSummary(
        pages_visited=pages_processed,
        resources_checked=pages_processed,
        unique_passwords_found=len(unique_values),
        queue_empty=True,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
    )
    return summary, all_matches
