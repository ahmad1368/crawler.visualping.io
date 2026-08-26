"""Extractor for exposed passwords in HTTP response headers and cookies.

Scans every response header (including custom `X-*` headers) and every
`Set-Cookie` value for a fetched response. Unlike the body-content
extractors, there's no single byte blob to scan -- the natural input here
is the header/cookie name-value maps a fetch result already carries
(`FetchResult.headers` / `.cookies` from issue #4), so this extractor's
`extract()` takes those directly instead of `(content, content_type, url)`.
"""

from __future__ import annotations

from app.matching import find_passwords
from app.models import PasswordMatch, SourceType


class HeaderCookieExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(
        self, headers: dict[str, str], cookies: dict[str, str], url: str
    ) -> list[PasswordMatch]:
        matches: list[PasswordMatch] = []
        for name, value in headers.items():
            matches.extend(self._matches_for(value, url, SourceType.HTTP_HEADER, f"header:{name}"))
        for name, value in cookies.items():
            matches.extend(self._matches_for(value, url, SourceType.COOKIE, f"cookie:{name}"))
        return matches

    def _matches_for(
        self, text: str, url: str, source_type: SourceType, locator: str
    ) -> list[PasswordMatch]:
        return [
            PasswordMatch(
                value=match.value,
                source_type=source_type,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=locator,
            )
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]
