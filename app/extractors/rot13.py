"""Extractor for passwords hidden via ROT13 substitution (issue #98).

ROT13 shifts A-Z/a-z by 13 positions, leaving digits and punctuation
untouched -- a password ROT13'd once (`IVFHNYCVAT{...}`) never matches
the plain password regex. ROT13 is its own inverse, so applying it again
recovers the original text; re-runs the shared regex against that.
"""

from __future__ import annotations

from app.extractors.deobfuscation import is_text_like, rot13_text
from app.matching import find_passwords
from app.models import PasswordMatch, SourceType


class Rot13Extractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if not is_text_like(content_type):
            return []

        text = rot13_text(content.decode("utf-8", errors="replace"))
        return [
            PasswordMatch(
                value=match.value,
                source_type=SourceType.ROT13,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=f"rot13:offset:{match.start}",
            )
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]
