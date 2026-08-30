"""Extractor for passwords hidden via Base64 or hex encoding (issue #98).

A regex over raw text can never see a password that's been Base64- or
hex-encoded first -- the encoded bytes don't contain the literal
`VISUALPING{...}` string anywhere. Finds candidate encoded runs (length-
gated to avoid decoding every short token in the page, see
`app/extractors/deobfuscation.py`), decodes each, and re-runs the shared
password regex against the decoded text.
"""

from __future__ import annotations

from app.extractors.deobfuscation import base64_hex_candidates, is_text_like
from app.matching import find_passwords
from app.models import PasswordMatch, SourceType


class Base64HexExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if not is_text_like(content_type):
            return []

        text = content.decode("utf-8", errors="replace")
        matches: list[PasswordMatch] = []
        for candidate in base64_hex_candidates(text):
            matches.extend(self._matches_for(candidate, url))
        return matches

    def _matches_for(self, text: str, url: str) -> list[PasswordMatch]:
        return [
            PasswordMatch(
                value=match.value,
                source_type=SourceType.BASE64_HEX,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                # Offset into the *decoded* candidate string, not the
                # original file -- there is no single stable position in
                # the raw (still-encoded) bytes to point at.
                locator=f"base64-hex:decoded-offset:{match.start}",
            )
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]
