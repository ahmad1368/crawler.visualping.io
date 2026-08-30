"""Defensive cap on runaway pagination-style URL families.

Some sites link the same content family through an ever-incrementing
query param (e.g. `?page=N`) that can go arbitrarily deep -- confirmed
past 500 pages against a real target with no end in sight, a pattern
indistinguishable from a deliberate crawl-budget trap. `PaginationGuard`
defends against this two ways:

1. Stops following a family once it's gone `max_unproductive` consecutive
   pages without yielding either a new password match *or* a genuinely
   new link to content outside the family itself. Two productivity
   signals, not one, because either alone is broken:
   - Matches alone (the original issue #78 fix) wrongly treats an
     index/listing family as unproductive when it never itself contains
     a password -- the overwhelmingly common real shape (a listing page
     links out to individual content pages; the content, not the
     listing, carries the secret). That killed a real crawl's coverage
     in practice (from ~680 pages down to ~480 against a real target) by
     stopping a legitimate index after just `max_unproductive` pages,
     silently dropping every link its later pages would have surfaced.
   - Link discovery alone (the pre-#78 signal) is broken the other way:
     ordinary sequential pagination *always* discovers a link to the
     next page in the family the first time it's seen, so naively
     counting "any new link" never actually distinguished a trap from
     normal pagination -- a real target's randomized-but-password-free
     `?page=N` family exploited exactly this to look "productive"
     forever.
   The fix here is to count new links *excluding same-family links* (the
   caller passes `new_external_links`, links discovered on this page
   that lead somewhere other than the next page(s) of this same family).
   A legitimate index that keeps surfacing brand-new content URLs stays
   "productive" and keeps going; a trap whose only link each page is the
   next page in its own chain does not, since that link never counts.
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
few pages, or a new external link every few pages) isn't cut off early,
small enough that a family with neither burns at most 10 of the crawl's
page budget before being abandoned, not hundreds. The default
`max_family_pages` of 200 (raised from an original 50 for the same
real-target coverage reason as the external-links fix above) is a
separate, much larger backstop -- most legitimate paginated result sets
are well under this; it exists purely to guarantee termination against an
adversarial family that games the streak heuristic (e.g. by faking a
new-looking external link occasionally), not to constrain a normal one.

Remaining known trade-off, narrower than before but not eliminated: an
index-style family whose *external* links themselves stop being new
after some point (e.g. it starts re-linking already-discovered pages)
would still look unproductive and get cut off, even if page content past
that point changes. Not distinguishable from a genuine dead end without
also inspecting page content similarity, which this guard deliberately
doesn't do. Any links already discovered on earlier pages are still
crawled normally as their own, independent URLs either way.
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
    def __init__(self, max_unproductive: int = 10, max_family_pages: int | None = 200) -> None:
        self._max_unproductive = max_unproductive
        self._max_family_pages = max_family_pages
        self._streaks: dict[str, int] = {}
        self._page_counts: dict[str, int] = {}
        self._stopped: set[str] = set()

    def is_stopped(self, url: str) -> bool:
        key = pagination_family_key(url)
        return key is not None and key in self._stopped

    def record(self, url: str, new_matches: int, new_external_links: int = 0) -> None:
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

        if new_matches or new_external_links:
            self._streaks[key] = 0
            return

        streak = self._streaks.get(key, 0) + 1
        self._streaks[key] = streak
        if streak >= self._max_unproductive:
            self._stopped.add(key)
            logger.info(
                "stopping pagination family %r after %d consecutive pages "
                "with no new password matches or new external links",
                key,
                streak,
            )
