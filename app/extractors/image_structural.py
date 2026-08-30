"""Structural JPEG segment / PNG chunk parser for image-embedded metadata
(issue #101).

`ImageExifExtractor` reads EXIF (JPEG APP1) via Pillow, including
UTF-16-encoded `UserComment` values, and `BinaryFallbackExtractor` scans
every image's raw bytes as `latin-1` text. Neither reaches two real
locations a password can hide:

- A JPEG `COM` (comment, marker `0xFFFE`) segment encoded in something
  other than plain ASCII/Latin-1 -- a UTF-16 value's interleaved null
  bytes break the password regex's contiguous-character match, so a raw
  `latin-1` scan never sees it as a match even though the bytes are
  right there.
- A PNG `zTXt` chunk (and an `iTXt` chunk with its compression flag set)
  -- these are zlib-*compressed*. A password hidden there is completely
  invisible to any plain-text scan, raw fallback included, until the
  bytes are actually decompressed.

This extractor is deliberately narrow: it does NOT re-parse JPEG APP1/
EXIF (`ImageExifExtractor` already does that correctly, including the
nested-IFD/byte-order handling a hand-rolled TIFF parser would have to
get right all over again for no new coverage) -- only the `COM` segment
and the three PNG text chunk types.
"""

from __future__ import annotations

import logging
import struct
import zlib

from app.matching import find_passwords
from app.models import PasswordMatch, SourceType

logger = logging.getLogger(__name__)

_JPEG_SOI = b"\xff\xd8"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_JPEG_COM_MARKER = 0xFFFE
_JPEG_SOS_MARKER = 0xFFDA
_JPEG_EOI_MARKER = 0xFFD9
# Markers with no length-prefixed payload following them (RSTn + TEM).
_JPEG_STANDALONE_MARKERS = frozenset({0xFF01, *range(0xFFD0, 0xFFD8)})

# The exact four encodings called out in the spec, tried in this order --
# a real password only matches the one an obfuscator actually used; the
# others just decode to unmatched noise, so trying every one risks no
# duplicate beyond the usual (source_url, value) dedup every extractor
# already relies on downstream.
_ENCODINGS = ("utf-16-le", "utf-16-be", "utf-8", "ascii")


def _strip_bom(payload: bytes) -> bytes:
    if payload.startswith(b"\xff\xfe") or payload.startswith(b"\xfe\xff"):
        return payload[2:]
    return payload


def _decode_all_encodings(payload: bytes) -> list[tuple[str, str]]:
    """Return every (encoding_name, decoded_text) candidate for `payload`
    -- the whole point is finding a value an adversary chose specifically
    to defeat a naive single-encoding text search."""
    stripped = _strip_bom(payload)
    candidates: list[tuple[str, str]] = []
    for encoding in _ENCODINGS:
        try:
            candidates.append((encoding, stripped.decode(encoding, errors="replace")))
        except LookupError:
            continue
    return candidates


def _parse_jpeg_com_segments(content: bytes) -> list[bytes]:
    """Walk JPEG markers from SOI, returning every COM segment's raw
    payload. Stops at SOS (entropy-coded scan data follows, no more
    markers to reliably find without fully decoding the scan -- COM
    always precedes SOS anyway) or EOI. Any malformed/truncated marker
    stream just stops parsing early rather than reading garbage."""
    segments: list[bytes] = []
    if not content.startswith(_JPEG_SOI):
        return segments

    pos = 2
    end = len(content)
    while pos + 2 <= end:
        if content[pos] != 0xFF:
            break  # not aligned on a marker boundary
        marker = (content[pos] << 8) | content[pos + 1]
        if marker in (_JPEG_SOS_MARKER, _JPEG_EOI_MARKER):
            break
        if marker in _JPEG_STANDALONE_MARKERS:
            pos += 2
            continue
        if pos + 4 > end:
            break
        seg_length = (content[pos + 2] << 8) | content[pos + 3]
        if seg_length < 2 or pos + 2 + seg_length > end:
            break
        if marker == _JPEG_COM_MARKER:
            segments.append(content[pos + 4 : pos + 2 + seg_length])
        pos += 2 + seg_length
    return segments


def _parse_png_text_chunks(content: bytes) -> list[tuple[str, str, bytes]]:
    """Walk PNG chunks after the 8-byte signature, returning
    (chunk_type, keyword, raw_text_payload) for every tEXt/zTXt/iTXt
    chunk -- zTXt and compressed iTXt payloads are already
    zlib-decompressed here. CRC is skipped over, not verified -- not
    needed for extraction purposes."""
    chunks: list[tuple[str, str, bytes]] = []
    if not content.startswith(_PNG_SIGNATURE):
        return chunks

    pos = 8
    end = len(content)
    while pos + 8 <= end:
        chunk_length = struct.unpack(">I", content[pos : pos + 4])[0]
        chunk_type = content[pos + 4 : pos + 8]
        data_start = pos + 8
        data_end = data_start + chunk_length
        if data_end + 4 > end:
            break
        data = content[data_start:data_end]

        if chunk_type == b"tEXt":
            keyword, _, text = data.partition(b"\x00")
            chunks.append(("tEXt", keyword.decode("latin-1", errors="replace"), text))
        elif chunk_type == b"zTXt":
            keyword, _, rest = data.partition(b"\x00")
            if rest:
                compressed = rest[1:]  # rest[0] is the compression method (always 0/zlib)
                try:
                    text = zlib.decompress(compressed)
                except zlib.error:
                    text = b""
                chunks.append(("zTXt", keyword.decode("latin-1", errors="replace"), text))
        elif chunk_type == b"iTXt":
            keyword, _, rest = data.partition(b"\x00")
            if len(rest) >= 2:
                compression_flag = rest[0]
                remainder = rest[2:]  # rest[1] is compression method, always 0/zlib if used
                _lang, _, remainder = remainder.partition(b"\x00")
                _translated_keyword, _, text = remainder.partition(b"\x00")
                if compression_flag:
                    try:
                        text = zlib.decompress(text)
                    except zlib.error:
                        text = b""
                chunks.append(("iTXt", keyword.decode("latin-1", errors="replace"), text))

        pos = data_end + 4  # skip past the CRC
        if chunk_type == b"IEND":
            break
    return chunks


class ImageStructuralExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if not content_type.startswith("image/"):
            return []

        matches: list[PasswordMatch] = []
        chunk_types_found: list[str] = []
        encodings_tried: set[str] = set()

        try:
            for payload in _parse_jpeg_com_segments(content):
                chunk_types_found.append("COM")
                for encoding, text in _decode_all_encodings(payload):
                    encodings_tried.add(encoding)
                    matches.extend(self._matches_for(text, url, "jpeg:COM"))
        except Exception:
            logger.debug("JPEG structural parse failed for %s, skipping", url, exc_info=True)

        try:
            for chunk_type, keyword, payload in _parse_png_text_chunks(content):
                chunk_types_found.append(chunk_type)
                for encoding, text in _decode_all_encodings(payload):
                    encodings_tried.add(encoding)
                    matches.extend(self._matches_for(text, url, f"png:{chunk_type}:{keyword}"))
        except Exception:
            logger.debug("PNG structural parse failed for %s, skipping", url, exc_info=True)

        if chunk_types_found:
            logger.info(
                "Structural image parse: %s -- chunk types found: %s, "
                "encodings tried: %s, %d flag(s) extracted.",
                url,
                sorted(set(chunk_types_found)),
                sorted(encodings_tried),
                len(matches),
            )

        return matches

    def _matches_for(self, text: str, url: str, locator: str) -> list[PasswordMatch]:
        if not text:
            return []
        return [
            PasswordMatch(
                value=match.value,
                source_type=SourceType.IMAGE_METADATA,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=locator,
            )
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]
