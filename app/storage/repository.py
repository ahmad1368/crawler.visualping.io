"""Repository interface for persisting crawl results.

Implementations MUST treat every `PasswordMatch` and raw content snapshot
they store as sensitive -- this is the crawler's durable record of exposed
secrets found on the target site.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import CrawlSummary, PageFetchData, PageResult, PasswordMatch


class Repository(ABC):
    @abstractmethod
    def save_page(
        self,
        page: PageResult,
        snapshot: bytes,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        """Persist a page's fetch result (and its matches), a raw content
        snapshot for the UI's "jump to location" viewer, and -- issue #72
        -- content_type/headers/cookies alongside it, so a later replay
        pass can re-run extraction against this page without a live
        re-fetch. All three are optional so existing callers that only
        care about the snapshot (and any Repository implementation that
        doesn't support replay) don't have to supply them."""

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

    @abstractmethod
    def get_all_page_fetch_data(self) -> list[PageFetchData]:
        """Return every persisted page's raw fetched bytes plus
        content_type/headers/cookies (issue #72) -- the input a replay
        pass needs to re-run extraction with zero network/browser calls.
        Only pages saved with that data (via save_page's optional
        content_type/headers/cookies) are included; a page saved without
        them (or by a Repository that never persists them) is skipped
        rather than replayed with guessed/empty values."""
