"""Least-significant-bit (LSB) steganography detector (issue #101, Layer 3).

Every other image extractor in this codebase reads text the image
*carries* -- EXIF fields, PNG/JPEG structural chunks, pixel-drawn text
recognized by OCR. This one instead reads the pixel *values themselves*:
a classic steganography technique hides a message by overwriting only
the least-significant bit of each color channel byte (a change too small
to be visible to the eye), so the message is present nowhere as text at
all -- not in metadata, not as rendered pixels, only in the low-order
bits of the raw pixel data.

Extraction order is fixed and documented so results are reproducible:
the image is converted to RGBA (so every pixel always has all four
channels, even if the source had none), `Image.tobytes()` gives a flat
byte string in row-major, per-pixel R,G,B,A order, and the LSB of each
byte -- in that same order -- becomes one bit of the reconstructed
message, packed MSB-first into bytes 8 at a time.

For an ordinary image (no hidden message), the low-order bits of real
pixel data are effectively noise -- decoding noise as text and matching
it against a specific 16-hex-character pattern is astronomically
unlikely, so this carries essentially no false-positive risk without
needing extra heuristics.
"""

from __future__ import annotations

import io
import logging

from PIL import Image
from PIL.Image import DecompressionBombError

from app.matching import find_passwords
from app.models import PasswordMatch, SourceType

logger = logging.getLogger(__name__)


def _extract_lsb_bytes(image: Image.Image) -> bytes:
    """Return the reassembled byte stream from the LSB of every channel
    byte, in row-major R,G,B,A per-pixel order. Trailing bits that don't
    fill a complete byte are dropped."""
    channel_bytes = image.convert("RGBA").tobytes()
    bit_count = len(channel_bytes) - (len(channel_bytes) % 8)

    out = bytearray(bit_count // 8)
    for i in range(bit_count):
        bit = channel_bytes[i] & 1
        if bit:
            out[i // 8] |= 1 << (7 - (i % 8))
    return bytes(out)


class ImageLsbExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if not content_type.startswith("image/"):
            return []

        try:
            with Image.open(io.BytesIO(content)) as image:
                reassembled = _extract_lsb_bytes(image)
        except (OSError, DecompressionBombError):
            return []

        text = reassembled.decode("latin-1")
        matches = [
            PasswordMatch(
                value=match.value,
                source_type=SourceType.IMAGE_LSB,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=f"lsb:offset:{match.start}",
            )
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]

        if matches:
            logger.info("LSB steganography DETECTED in %s -- %d flag(s) found.", url, len(matches))
        else:
            logger.debug("LSB steganography ruled out for %s.", url)

        return matches
