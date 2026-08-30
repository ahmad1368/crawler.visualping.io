"""Extractor for passwords drawn as image pixels -- readable only by eye
or OCR, not present as parseable text or metadata anywhere in the file
(a photo of a whiteboard, a screenshot of a sticky note, ...). No text
extractor or EXIF reader can ever find this; it requires actually
recognizing the characters drawn in the image.

Runs Tesseract (via `pytesseract`) over the decoded image and scans
whatever text it recognizes for the password pattern. Requires the
Tesseract OCR binary on PATH (see README) -- if it's missing or fails on
a given image, `extract()` degrades to no matches rather than raising,
the same graceful-degradation contract every other extractor in this
package follows for malformed/exotic input.
"""

from __future__ import annotations

import io

import pytesseract
from PIL import Image
from PIL.Image import DecompressionBombError

from app.matching import find_passwords
from app.models import PasswordMatch, SourceType


class ImageOcrExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if not content_type.startswith("image/"):
            return []

        try:
            with Image.open(io.BytesIO(content)) as image:
                text = pytesseract.image_to_string(image)
        except (
            OSError,
            DecompressionBombError,
            pytesseract.TesseractError,
            pytesseract.TesseractNotFoundError,
        ):
            return []

        return [
            PasswordMatch(
                value=match.value,
                source_type=SourceType.IMAGE_OCR,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                # No meaningful pixel position without also requesting
                # Tesseract's per-word bounding boxes -- the offset is into
                # the OCR'd text, not the image, same spirit as
                # BinaryFallbackExtractor's `offset:N` for a non-text body.
                locator=f"ocr:offset:{match.start}",
            )
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]
