"""Post-crawl content-negotiation probe (issue #103).

A server can be configured to serve an alternate representation of the
same URL depending on request headers -- a JSON API response instead of
rendered HTML for `Accept: application/json`, a raw-text debug dump for
`Accept: text/plain`, an AJAX-only payload gated on
`X-Requested-With: XMLHttpRequest`. None of this project's other
extractors would ever see that alternate representation: every normal
crawl fetch uses the same fixed request shape, so a payload that only
exists under a different negotiation is invisible by construction.

This re-requests a bounded, representative sample of already-crawled
HTML pages (not every page -- kept deliberately small, matching issue
#99's "sequential, small tail-end" reasoning) with each of a fixed set of
alternate header combinations, and scans both the response body and its
headers. Any match found here has no natural "page" to attach a stored
snapshot to (it's a probe of an existing URL, not a new one -- reusing
that URL for `save_page()` would clobber the primary crawl's own stored
snapshot for it), so it's persisted via `Repository.save_match()`, the
standalone-match path that method exists for.
"""

from __future__ import annotations

import logging

from app.crawler.fetcher import HttpFetcher
from app.events import MATCH_FOUND, EventBus
from app.matching import find_passwords
from app.models import ContentNegotiationReport, PasswordMatch, SourceType
from app.storage.repository import Repository

logger = logging.getLogger(__name__)

_DEFAULT_SAMPLE_SIZE = 5

_PROBE_HEADER_SETS: tuple[dict[str, str], ...] = (
    {"Accept": "application/json"},
    {"Accept": "text/plain"},
    {"X-Requested-With": "XMLHttpRequest"},
)


def _label(header_set: dict[str, str]) -> str:
    return ", ".join(f"{key}: {value}" for key, value in header_set.items())


def _matches_for(text: str, url: str, locator: str, context_chars: int) -> list[PasswordMatch]:
    if not text:
        return []
    return [
        PasswordMatch(
            value=match.value,
            source_type=SourceType.CONTENT_NEGOTIATION,
            source_url=url,
            context_before=match.context_before,
            context_after=match.context_after,
            locator=locator,
        )
        for match in find_passwords(text, before=context_chars, after=context_chars)
    ]


async def probe_content_negotiation(
    repository: Repository,
    http_fetcher: HttpFetcher,
    sample_size: int = _DEFAULT_SAMPLE_SIZE,
    context_chars: int = 80,
    event_bus: EventBus | None = None,
    unique_values: set[str] | None = None,
) -> ContentNegotiationReport:
    """Post-crawl completeness pass -- see module docstring.

    If `unique_values` is given, every password value found is added to
    it in place -- lets a caller (the `Orchestrator`) fold probe-found
    matches into its own running unique-password count."""
    html_pages = [
        page.url
        for page in repository.get_all_page_fetch_data()
        if page.content_type and page.content_type.split(";", 1)[0].strip().lower() == "text/html"
    ]
    sample = html_pages[:sample_size]
    headers_tested = [_label(header_set) for header_set in _PROBE_HEADER_SETS]
    matches_found = 0

    for url in sample:
        for header_set in _PROBE_HEADER_SETS:
            label = _label(header_set)
            try:
                result = await http_fetcher.fetch(url, extra_headers=header_set)
            except Exception:
                # One probe failing (timeout, connection error) must not
                # abort the rest of the sample -- same per-request
                # isolation as every other network call in this codebase.
                logger.debug(
                    "Content-negotiation probe failed for %s (%s), skipping",
                    url,
                    label,
                    exc_info=True,
                )
                continue

            body_text = result.content.decode("utf-8", errors="replace")
            matches = _matches_for(
                body_text, url, f"content-negotiation:{label}:body", context_chars
            )
            matches.extend(
                _matches_for(
                    "\n".join(result.headers.values()),
                    url,
                    f"content-negotiation:{label}:headers",
                    context_chars,
                )
            )

            if matches:
                for match in matches:
                    repository.save_match(match)
                    if event_bus is not None:
                        event_bus.publish(MATCH_FOUND, match)
                if unique_values is not None:
                    unique_values.update(match.value for match in matches)
                matches_found += len(matches)
                logger.info(
                    "Content-negotiation probe: %s (%s) -- %d flag(s) found.",
                    url,
                    label,
                    len(matches),
                )
            else:
                logger.info(
                    "Content-negotiation probe: %s (%s) -- no additional payload found.",
                    url,
                    label,
                )

    report = ContentNegotiationReport(
        pages_probed=len(sample),
        headers_tested=headers_tested,
        matches_found=matches_found,
    )
    logger.info(
        "Content-negotiation scan: probed %d page(s) with %d header variant(s) -- "
        "%d flag(s) found.",
        report.pages_probed,
        len(headers_tested),
        matches_found,
    )
    return report
