"""Extractor for passwords hidden as reversed text (issue #98).

A password written backwards (`}0000000fedcba98{GNIPLAUSIV`) never
matches the forward-only password regex against the raw text. Reverses
the whole decoded body once and re-runs the shared regex against that --
a real password planted this way becomes readable in the reversed
string exactly where it was hidden in the original.
"""

from __future__ import annotations

from app.extractors.deobfuscation import is_text_like, reverse_text
from app.matching import find_passwords
from app.models import PasswordMatch, SourceType


class ReversedTextExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if not is_text_like(content_type):
            return []

        text = reverse_text(content.decode("utf-8", errors="replace"))
        return [
            PasswordMatch(
                value=match.value,
                source_type=SourceType.REVERSED_TEXT,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                # Offset into the *reversed* text, not the original file
                # -- reversing changes every position, so there's no
                # single stable forward-file offset to point at.
                locator=f"reversed:offset:{match.start}",
            )
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]
