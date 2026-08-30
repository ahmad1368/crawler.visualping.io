"""Extractor for passwords hidden as JS char-code sequences -- an array
literal (`[86, 73, 83, ...]`) or a `String.fromCharCode(86, 73, 83, ...)`
call whose numbers spell out the password only once decoded into
characters. A plain regex over the raw file text never sees the password
itself here (only digits/commas), which is why `CssJsExtractor`'s
plaintext scan can't catch this obfuscation -- this extractor statically
decodes each numeric sequence into the string it would evaluate to at
runtime and re-runs the same password regex against that decoded string.
"""

from __future__ import annotations

import re

from app.matching import find_passwords, locator_for_offset
from app.models import PasswordMatch, SourceType

_JS_CONTENT_TYPES = {
    "application/javascript",
    "text/javascript",
    "application/x-javascript",
}

_NUMBER = r"(?:0[xX][0-9a-fA-F]{1,6}|\d{1,7})"
# Below this many codes, a numeric sequence is far more likely to be
# unrelated data (an RGB triple, an id list, ...) than an obfuscated
# password -- harmless either way since a short sequence can't decode to
# the (28-char) password pattern, but skipping it avoids wasted decode
# attempts on ordinary numeric arrays.
_MIN_CODES = 6

_ARRAY_PATTERN = re.compile(
    rf"\[\s*(?P<codes>{_NUMBER}(?:\s*,\s*{_NUMBER}){{{_MIN_CODES - 1},}})\s*\]"
)
_FROM_CHARCODE_PATTERN = re.compile(
    rf"fromCharCode\s*\(\s*(?P<codes>{_NUMBER}(?:\s*,\s*{_NUMBER}){{{_MIN_CODES - 1},}})\s*\)"
)


def _source_type_for(content_type: str) -> SourceType | None:
    normalized = content_type.split(";", 1)[0].strip().lower()
    return SourceType.JS_CHARCODE if normalized in _JS_CONTENT_TYPES else None


def _parse_number(token: str) -> int:
    token = token.strip()
    return int(token, 16) if token[:2].lower() == "0x" else int(token, 10)


def _decode(codes_text: str) -> str | None:
    codes = [_parse_number(token) for token in codes_text.split(",")]
    if any(code > 0x10FFFF for code in codes):
        return None
    return "".join(chr(code) for code in codes)


class JsCharCodeExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        source_type = _source_type_for(content_type)
        if source_type is None:
            return []

        text = content.decode("utf-8", errors="replace")
        results: list[PasswordMatch] = []

        for pattern in (_ARRAY_PATTERN, _FROM_CHARCODE_PATTERN):
            for literal_match in pattern.finditer(text):
                decoded = _decode(literal_match.group("codes"))
                if decoded is None:
                    continue
                start, end = literal_match.span()
                for pw_match in find_passwords(
                    decoded, before=self._context_chars, after=self._context_chars
                ):
                    results.append(
                        PasswordMatch(
                            value=pw_match.value,
                            source_type=source_type,
                            source_url=url,
                            context_before=text[max(0, start - self._context_chars) : start],
                            context_after=text[end : end + self._context_chars],
                            locator=locator_for_offset(text, start),
                        )
                    )
        return results
