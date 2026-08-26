"""Tracks the queue of URLs to crawl and those already visited.

Normalizes URLs before dedupe so trivial variants (a trailing slash, a
fragment, or a decorative tracking query param) don't get queued twice,
restricts the frontier to the seed URL's origin, and never re-queues a URL
once seen -- which is what keeps cyclic links (and redirect loops
discovered as links) from looping forever. `mark_visited()` lets a caller
pre-seed already-completed URLs (e.g. from a `Repository`, on resume)
without enqueuing them for a re-fetch.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Common decorative/tracking query params that link to the same underlying
# page under different values -- without stripping these, sites that tag
# every internal link with e.g. ?utm_source=... explode the frontier into
# one "distinct" URL per link, and the crawl never reaches queue_empty.
_TRACKING_PARAMS = {
    "ref",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "v",
    "hl",
    "fbclid",
    "gclid",
    "source",
}


def normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(kept_params)

    return urlunsplit((scheme, netloc, path, query, ""))


def _origin(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    return (parsed.scheme.lower(), parsed.netloc.lower())


class UrlFrontier:
    def __init__(self, seed_url: str) -> None:
        self._origin = _origin(seed_url)
        self._queue: deque[str] = deque()
        self._seen: set[str] = set()
        self.add(seed_url)

    def is_same_origin(self, url: str) -> bool:
        return _origin(url) == self._origin

    def add(self, url: str) -> bool:
        if not self.is_same_origin(url):
            return False

        normalized = normalize_url(url)
        if normalized in self._seen:
            return False

        self._seen.add(normalized)
        self._queue.append(normalized)
        return True

    def add_many(self, urls: Iterable[str]) -> int:
        return sum(1 for url in urls if self.add(url))

    def mark_visited(self, url: str) -> None:
        """Mark a URL as already handled without queuing it -- for
        resuming a crawl from a Repository's previously-persisted pages."""
        self._seen.add(normalize_url(url))

    def has_next(self) -> bool:
        return bool(self._queue)

    def next(self) -> str:
        return self._queue.popleft()

    def __len__(self) -> int:
        return len(self._queue)
