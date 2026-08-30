"""Extractor for exposed passwords in HTTP response headers and cookies.

Scans every response header (including custom `X-*` headers) and every
`Set-Cookie` value for a fetched response. Unlike the body-content
extractors, there's no single byte blob to scan -- the natural input here
is the header/cookie name-value maps a fetch result already carries
(`FetchResult.headers` / `.cookies` from issue #4), so this extractor's
`extract()` takes those directly instead of `(content, content_type, url)`.

Also runs each value through the Base64/hex/reversed/ROT13 transforms
(issue #98) -- a header/cookie value is exactly as plausible a place to
hide an obfuscated password as a response body, and the shared
`app/extractors/deobfuscation.py` primitives make this a small addition
rather than a second execution path.
"""

from __future__ import annotations

from app.extractors.deobfuscation import base64_hex_candidates, reverse_text, rot13_text
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
            matches.extend(self._transform_matches_for(value, url, f"header:{name}"))
        for name, value in cookies.items():
            matches.extend(self._matches_for(value, url, SourceType.COOKIE, f"cookie:{name}"))
            matches.extend(self._transform_matches_for(value, url, f"cookie:{name}"))
        return matches

    def _transform_matches_for(
        self, value: str, url: str, locator_prefix: str
    ) -> list[PasswordMatch]:
        matches: list[PasswordMatch] = []
        for candidate in base64_hex_candidates(value):
            matches.extend(
                self._matches_for(
                    candidate, url, SourceType.BASE64_HEX, f"{locator_prefix}:base64-hex"
                )
            )
        matches.extend(
            self._matches_for(
                reverse_text(value), url, SourceType.REVERSED_TEXT, f"{locator_prefix}:reversed"
            )
        )
        matches.extend(
            self._matches_for(rot13_text(value), url, SourceType.ROT13, f"{locator_prefix}:rot13")
        )
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
