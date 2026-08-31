"""Extractor for passwords drawn as image pixels -- readable only by eye
or OCR, not present as parseable text or metadata anywhere in the file
(a photo of a whiteboard, a screenshot of a sticky note, ...). No text
extractor or EXIF reader can ever find this; it requires actually
recognizing the characters drawn in the image.

Preprocesses (upscale + grayscale + threshold) before running Tesseract
(via `pytesseract`) under two page-segmentation modes and merging their
output, since a small/low-quality rendered text region is otherwise easy
for OCR to mis-read or garble under a single default config (issue #109).
Requires the Tesseract OCR binary on PATH (see README) -- if it's missing
or fails on a given image, `extract()` logs a warning and degrades to no
matches rather than raising, the same graceful-degradation contract every
other extractor in this package follows for malformed/exotic input, but
loud about it so a missed flag due to tooling issues (not installed,
crashed) is distinguishable from "no flag exists in this image."
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable

import pytesseract
from PIL import Image
from PIL.Image import DecompressionBombError

from app.matching import find_passwords
from app.models import PasswordMatch, SourceType

logger = logging.getLogger(__name__)

# --psm 6 (uniform block of text) and --psm 11 (sparse text) catch
# different real-world layouts -- a clean paragraph vs. scattered text on
# a diagram/whiteboard -- and a mismatched mode can silently produce
# garbled output with no error, so both are tried and merged rather than
# picking one.
_PSM_CONFIGS = ("--psm 6", "--psm 11")

# Global binarization threshold (0-255). A fixed threshold is a
# deliberate simplification -- it assumes reasonably even lighting across
# the text region, which holds for the target's rendered/photographed
# text samples seen so far. Per-region adaptive thresholding would handle
# uneven lighting/shadows better but is out of scope for this issue.
_THRESHOLD = 150
_UPSCALE_FACTOR = 4

# Optional extension point for a vision-capable model/API fallback, tried
# only when plain OCR finds nothing (issue #109's proposed fix). Takes
# (content, content_type, url), returns candidate text strings to scan
# for the password pattern the same way OCR'd text is. No concrete
# implementation is wired in by default: calling an external vision API
# would send crawled (potentially sensitive) image content off-box,
# which conflicts with this project's standing "no outbound network
# calls beyond the target site being crawled" boundary -- see
# docs/DATA_FLOW_REPORT.md. Left as an injectable hook so a future
# operator-approved integration doesn't require touching this extractor.
VisionFallback = Callable[[bytes, str, str], list[str]]


def _preprocess(image: Image.Image) -> Image.Image:
    grayscale = image.convert("L")
    upscaled = grayscale.resize(
        (grayscale.width * _UPSCALE_FACTOR, grayscale.height * _UPSCALE_FACTOR),
        Image.Resampling.LANCZOS,
    )
    return upscaled.point(lambda pixel: 255 if pixel > _THRESHOLD else 0)


class ImageOcrExtractor:
    def __init__(
        self,
        context_chars: int = 80,
        vision_fallback: VisionFallback | None = None,
    ) -> None:
        self._context_chars = context_chars
        self._vision_fallback = vision_fallback

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if not content_type.startswith("image/"):
            return []

        try:
            with Image.open(io.BytesIO(content)) as image:
                processed = _preprocess(image)
        except (OSError, DecompressionBombError):
            logger.warning(
                "ImageOcrExtractor: could not open/preprocess image at %s", url, exc_info=True
            )
            return []

        texts: list[str] = []
        for config in _PSM_CONFIGS:
            try:
                texts.append(pytesseract.image_to_string(processed, config=config))
            except pytesseract.TesseractNotFoundError:
                logger.warning(
                    "ImageOcrExtractor: Tesseract binary not found on PATH -- "
                    "skipping OCR for %s",
                    url,
                )
                return []
            except pytesseract.TesseractError:
                logger.warning(
                    "ImageOcrExtractor: OCR pass (%s) failed for %s", config, url, exc_info=True
                )

        seen_values: set[str] = set()
        matches: list[PasswordMatch] = []
        for text in texts:
            for match in find_passwords(
                text, before=self._context_chars, after=self._context_chars
            ):
                if match.value in seen_values:
                    continue
                seen_values.add(match.value)
                matches.append(
                    PasswordMatch(
                        value=match.value,
                        source_type=SourceType.IMAGE_OCR,
                        source_url=url,
                        context_before=match.context_before,
                        context_after=match.context_after,
                        # No meaningful pixel position without also requesting
                        # Tesseract's per-word bounding boxes -- the offset is
                        # into the OCR'd text, not the image, same spirit as
                        # BinaryFallbackExtractor's `offset:N` for a non-text
                        # body.
                        locator=f"ocr:offset:{match.start}",
                    )
                )

        if matches or self._vision_fallback is None:
            return matches

        try:
            candidate_texts = self._vision_fallback(content, content_type, url)
        except Exception:
            logger.warning("ImageOcrExtractor: vision fallback failed for %s", url, exc_info=True)
            return matches

        for text in candidate_texts:
            for match in find_passwords(
                text, before=self._context_chars, after=self._context_chars
            ):
                if match.value in seen_values:
                    continue
                seen_values.add(match.value)
                matches.append(
                    PasswordMatch(
                        value=match.value,
                        source_type=SourceType.IMAGE_OCR,
                        source_url=url,
                        context_before=match.context_before,
                        context_after=match.context_after,
                        locator=f"vision:offset:{match.start}",
                    )
                )
        return matches
