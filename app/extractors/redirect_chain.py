"""Extractor for exposed passwords in HTTP redirect chains (issue #103).

`HttpFetcher` follows redirects transparently (`follow_redirects=True`)
so a crawl reaches the content a 301/302/307/308 actually points at --
but that means every intermediate hop's own response is discarded once
the final page is reached. A flag baked into an intermediate `Location`
header (or its query string) -- e.g. a debug/staging redirect rule that
echoes something back before sending the browser on -- would otherwise
never be seen at all. Unlike the body-content extractors, there's no
single byte blob to scan here either -- the natural input is
`FetchResult.redirect_history` a fetch result already carries, same
shape reason `HeaderCookieExtractor` isn't routed through
`ExtractorRegistry`.
"""

from __future__ import annotations

from app.crawler.fetcher import RedirectHop
from app.matching import find_passwords
from app.models import PasswordMatch, SourceType


class RedirectExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, redirect_history: list[RedirectHop], url: str) -> list[PasswordMatch]:
        matches: list[PasswordMatch] = []
        for index, hop in enumerate(redirect_history):
            if hop.location:
                matches.extend(self._matches_for(hop.location, url, f"redirect:{index}:location"))
        return matches

    def _matches_for(self, text: str, url: str, locator: str) -> list[PasswordMatch]:
        return [
            PasswordMatch(
                value=match.value,
                source_type=SourceType.REDIRECT,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=locator,
            )
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]
