"""Repository interface for persisting crawl results.

Implementations MUST treat every `PasswordMatch` and raw content snapshot
they store as sensitive -- this is the crawler's durable record of exposed
secrets found on the target site.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import CrawlSummary, PageResult, PasswordMatch


class Repository(ABC):
    @abstractmethod
    def save_page(self, page: PageResult, snapshot: bytes) -> None:
        """Persist a page's fetch result (and its matches), plus a raw
        content snapshot for the UI's "jump to location" viewer."""

    @abstractmethod
    def save_match(self, match: PasswordMatch) -> None:
        """Persist a single password match not tied to a full `PageResult`
        (e.g. from a header/cookie scan, which has no page snapshot)."""

    @abstractmethod
    def get_report(self) -> CrawlSummary:
        """Return an aggregate summary of everything persisted so far."""

    @abstractmethod
    def get_snapshot(self, url: str) -> bytes | None:
        """Return the raw content snapshot stored for a URL, or None if
        none was stored."""

    @abstractmethod
    def get_matches(self) -> list[PasswordMatch]:
        """Return every password match persisted so far, in insertion
        order (for the UI's results table, issue #20)."""

    @abstractmethod
    def get_visited_urls(self) -> list[str]:
        """Return every URL already fully fetched and saved -- lets an
        Orchestrator resume a crashed crawl without re-fetching them."""
