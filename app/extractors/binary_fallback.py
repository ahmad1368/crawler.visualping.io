"""Generic binary/string fallback extractor.

For any content type not already handled by a more specific extractor
(HTML, CSS/JS, images), treats the response body as raw bytes and scans it
like `strings` would -- decoding with `latin-1` (a 1:1 byte<->codepoint
mapping that never raises, unlike `utf-8`) so arbitrary binary content
never crashes the scan, then running the same password regex over the
result. `locator` is the match's byte offset into the content.
"""

from __future__ import annotations

from app.matching import find_passwords
from app.models import PasswordMatch, SourceType

_HANDLED_CONTENT_TYPES = {
    "text/html",
    "text/css",
    "application/javascript",
    "text/javascript",
    "application/x-javascript",
}


def _is_handled_elsewhere(content_type: str) -> bool:
    normalized = content_type.split(";", 1)[0].strip().lower()
    return normalized in _HANDLED_CONTENT_TYPES or normalized.startswith("image/")


class BinaryFallbackExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if _is_handled_elsewhere(content_type):
            return []

        text = content.decode("latin-1")
        return [
            PasswordMatch(
                value=match.value,
                source_type=SourceType.BINARY,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=f"offset:{match.start}",
            )
            for match in find_passwords(
                text, before=self._context_chars, after=self._context_chars
            )
        ]
