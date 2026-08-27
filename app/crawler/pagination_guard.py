"""Defensive cap on runaway pagination-style URL families.

Some sites link the same content family through an ever-incrementing
query param (e.g. `?page=N`) that can go arbitrarily deep -- confirmed
past 500 pages against a real target with no end in sight, a pattern
indistinguishable from a deliberate crawl-budget trap. `PaginationGuard`
defends against this two ways:

1. Stops following a family once it's gone `max_unproductive` consecutive
   pages without yielding a new password match. Deliberately keyed on
   matches alone, not "a new link was discovered" -- issue #78 found a
   real target serving randomized-but-password-free content on every
   page of a `?page=N` family specifically to always look "new," which
   trivially defeats a link-based productivity signal: ordinary
   sequential pagination *always* discovers a link to the next page the
   first time it's seen, so "a new link" was never actually a reliable
   proxy for "still worth crawling," trap or not.
2. An unconditional hard ceiling (`max_family_pages`) on total pages
   visited in one family, independent of the streak above -- so even a
   family engineered to occasionally look "productive" enough to keep
   resetting the streak still can't run unbounded. Deliberately always
   on by default (not an opt-in cap like `Orchestrator`'s `max_pages`/
   `max_duration_seconds`, issue #71): those bound an entire crawl of an
   unknown-sized real site, where no fixed number is ever safely
   guessable up front. A single pagination family is a much narrower,
   inherently guard-worthy shape this class already treats as
   suspicious by existing, so a sane default ceiling here doesn't carry
   that same risk of truncating a legitimate crawl.

The default `max_unproductive` of 10 is a deliberate balance: large
enough that a family with real, sparse content (e.g. a new password every
few pages) isn't cut off early, small enough that a family with none
burns at most 10 of the crawl's page budget before being abandoned, not
hundreds. The default `max_family_pages` of 50 is a separate, larger
backstop -- most legitimate paginated result sets are well under this; it
exists purely to guarantee termination against an adversarial family that
games the streak heuristic, not to constrain a normal one.

Known trade-off, judged acceptable: an index-style pagination family that
never itself contains a password but links out to real content pages
elsewhere could, in principle, be cut off by `max_family_pages` before
discovering everything past page 50 of that index. Any such links already
discovered on earlier pages are still crawled normally as their own,
independent URLs -- only additional links first appearing beyond the cap
would be missed.
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
    def __init__(self, max_unproductive: int = 10, max_family_pages: int | None = 50) -> None:
        self._max_unproductive = max_unproductive
        self._max_family_pages = max_family_pages
        self._streaks: dict[str, int] = {}
        self._page_counts: dict[str, int] = {}
        self._stopped: set[str] = set()

    def is_stopped(self, url: str) -> bool:
        key = pagination_family_key(url)
        return key is not None and key in self._stopped

    def record(self, url: str, new_matches: int) -> None:
        key = pagination_family_key(url)
        if key is None:
            return

        page_count = self._page_counts.get(key, 0) + 1
        self._page_counts[key] = page_count
        if self._max_family_pages is not None and page_count >= self._max_family_pages:
            self._stopped.add(key)
            logger.info(
                "stopping pagination family %r after %d total pages "
                "(hard ceiling, independent of productivity)",
                key,
                page_count,
            )
            return

        if new_matches:
            self._streaks[key] = 0
            return

        streak = self._streaks.get(key, 0) + 1
        self._streaks[key] = streak
        if streak >= self._max_unproductive:
            self._stopped.add(key)
            logger.info(
                "stopping pagination family %r after %d consecutive pages "
                "with no new password matches",
                key,
                streak,
            )
