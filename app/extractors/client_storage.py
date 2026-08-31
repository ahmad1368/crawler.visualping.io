"""Extractor for exposed passwords in client-side browser storage
(issue #103).

`document.cookie`, `localStorage`, and `sessionStorage` are all
JavaScript-only state -- never sent to the server, never present in any
HTTP response body or header, so no other extractor in this codebase can
ever see them. A value stashed there (a debug flag left in local
storage, a value set by inline JS and never actually rendered) is
otherwise completely invisible to a crawl. Unlike the body-content
extractors, there's no single byte blob to scan here either -- the
natural input is the storage snapshot `BrowserFetcher` already captures
per page, same shape reason `HeaderCookieExtractor` isn't routed through
`ExtractorRegistry`.
"""

from __future__ import annotations

from app.matching import find_passwords
from app.models import PasswordMatch, SourceType


class ClientStorageExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(
        self,
        cookies: str,
        local_storage: dict[str, str],
        session_storage: dict[str, str],
        url: str,
    ) -> list[PasswordMatch]:
        matches: list[PasswordMatch] = []
        if cookies:
            matches.extend(self._matches_for(cookies, url, "client-storage:cookie"))
        for key, value in local_storage.items():
            matches.extend(self._matches_for(value, url, f"client-storage:localStorage:{key}"))
        for key, value in session_storage.items():
            matches.extend(self._matches_for(value, url, f"client-storage:sessionStorage:{key}"))
        return matches

    def _matches_for(self, text: str, url: str, locator: str) -> list[PasswordMatch]:
        return [
            PasswordMatch(
                value=match.value,
                source_type=SourceType.CLIENT_STORAGE,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=locator,
            )
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]
