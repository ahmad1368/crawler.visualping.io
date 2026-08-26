"""Extractor for exposed passwords in downloaded CSS/JS file bodies.

Unlike the HTML extractor, CSS/JS content isn't parsed into a DOM -- a
password could be hidden anywhere (a `content:` property, a string
literal, a `//` or `/* */` comment), so the whole body is scanned as
plain text.
"""

from __future__ import annotations

from app.matching import find_passwords, locator_for_offset
from app.models import PasswordMatch, SourceType

_CSS_CONTENT_TYPES = {"text/css"}
_JS_CONTENT_TYPES = {
    "application/javascript",
    "text/javascript",
    "application/x-javascript",
}


def _source_type_for(content_type: str) -> SourceType | None:
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized in _CSS_CONTENT_TYPES:
        return SourceType.CSS
    if normalized in _JS_CONTENT_TYPES:
        return SourceType.JS
    return None


class CssJsExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        source_type = _source_type_for(content_type)
        if source_type is None:
            return []

        text = content.decode("utf-8", errors="replace")
        return [
            PasswordMatch(
                value=match.value,
                source_type=source_type,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=locator_for_offset(text, match.start),
            )
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]
