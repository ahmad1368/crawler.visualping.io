"""Shared transform primitives for deep-payload deobfuscation (issue #98).

Three independent techniques an obfuscator can use to hide a password
from a naive plaintext scan: Base64/hex encoding, character-reversal, and
ROT13 substitution. The functions here take arbitrary text and return a
decoded/transformed candidate string (or list of them) -- callers run
`app/matching.py::find_passwords()` against each result themselves; this
module never touches the password regex.
"""

from __future__ import annotations

import base64
import codecs
import re

# Minimum candidate length before attempting a Base64/hex decode --
# mirrors JsCharCodeExtractor's _MIN_CODES: a shorter run is far more
# likely to be unrelated data (a CSS hash, a short token) than an
# obfuscated ~28-char VISUALPING{...} flag (Base64: ceil(28/3)*4 = 40
# chars; hex: 28*2 = 56 chars), and skipping it avoids wasted decode
# attempts on ordinary short strings.
_MIN_BASE64_LENGTH = 20
_MIN_HEX_LENGTH = 20

_BASE64_CANDIDATE = re.compile(rf"[A-Za-z0-9+/_-]{{{_MIN_BASE64_LENGTH},}}={{0,2}}")
_HEX_CANDIDATE = re.compile(rf"(?:[0-9a-fA-F]{{2}}){{{_MIN_HEX_LENGTH // 2},}}")

# Content types worth running these transforms against -- scripts,
# stylesheets, markup, structured data payloads. Deliberately excludes
# images/binary: BinaryFallbackExtractor already raw-scans those, and
# reversing/ROT13-transforming a multi-MB image byte string as "text" on
# every fetch would add real per-page cost for a technique that's a
# text-obfuscation trick, not a pixel one.
_TEXT_LIKE_CONTENT_TYPE_PREFIXES = (
    "text/",
    "application/javascript",
    "application/x-javascript",
    "application/json",
    "application/xml",
)


def is_text_like(content_type: str) -> bool:
    normalized = content_type.split(";", 1)[0].strip().lower()
    return normalized.startswith(_TEXT_LIKE_CONTENT_TYPE_PREFIXES)


def base64_hex_candidates(text: str) -> list[str]:
    """Return every plausible decoded string from Base64 (standard and
    URL-safe alphabets) and hex byte-stream runs found in `text`.
    Malformed/undecodable candidates are silently skipped -- never
    raises, same defensive pattern as every other extractor."""
    candidates: list[str] = []

    for match in _BASE64_CANDIDATE.finditer(text):
        token = match.group(0)
        padded = token + "=" * (-len(token) % 4)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded = decoder(padded)
            except Exception:
                continue
            candidates.append(decoded.decode("utf-8", errors="replace"))

    for match in _HEX_CANDIDATE.finditer(text):
        token = match.group(0)
        try:
            decoded = bytes.fromhex(token)
        except ValueError:
            continue
        candidates.append(decoded.decode("utf-8", errors="replace"))

    return candidates


def reverse_text(text: str) -> str:
    return text[::-1]


def rot13_text(text: str) -> str:
    return codecs.encode(text, "rot13")
