"""Defensive cap on runaway pagination-style URL families.

Some sites link the same content family through an ever-incrementing
query param (e.g. `?page=N`) that can go arbitrarily deep -- confirmed
past 500 pages against a real target with no end in sight, a pattern
indistinguishable from a deliberate crawl-budget trap. Rather than follow
such a family forever (or up to `max_pages`), `PaginationGuard` stops
following a family once it's gone `max_unproductive` consecutive pages
without yielding a new link or a new password match, and logs why.

The default of 10 is a deliberate balance: large enough that a family
with real, sparse content (e.g. a new password every few pages) isn't cut
off early, small enough that a family with none burns at most 10 of the
crawl's page budget before being abandoned, not hundreds.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlsplit

logger = logging.getLogger(__name__)


def pagination_family_key(url: str) -> str | None:
    """Return a family key for a URL with exactly one purely-numeric query
    param (e.g. `?page=7`), or None if `url` doesn't match that shape."""
    parsed = urlsplit(url)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    if len(params) != 1:
        return None
    name, value = params[0]
    if not value.isdigit():
        return None
    return f"{parsed.path}?{name}"


class PaginationGuard:
    def __init__(self, max_unproductive: int = 10) -> None:
        self._max_unproductive = max_unproductive
        self._streaks: dict[str, int] = {}
        self._stopped: set[str] = set()

    def is_stopped(self, url: str) -> bool:
        key = pagination_family_key(url)
        return key is not None and key in self._stopped

    def record(self, url: str, new_links: int, new_matches: int) -> None:
        key = pagination_family_key(url)
        if key is None:
            return

        if new_links or new_matches:
            self._streaks[key] = 0
            return

        streak = self._streaks.get(key, 0) + 1
        self._streaks[key] = streak
        if streak >= self._max_unproductive:
            self._stopped.add(key)
            logger.info(
                "stopping pagination family %r after %d consecutive pages "
                "with no new links or matches",
                key,
                streak,
            )
