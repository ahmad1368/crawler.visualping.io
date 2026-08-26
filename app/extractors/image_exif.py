"""Extractor for exposed passwords in image EXIF metadata.

Reads EXIF fields (e.g. `UserComment`, `ImageDescription`) from downloaded
images via Pillow. Operators sometimes stash debug notes -- including
credentials -- in these fields without realizing they ship with the image.
"""

from __future__ import annotations

import io

from PIL import ExifTags, Image
from PIL.Image import DecompressionBombError

from app.matching import find_passwords
from app.models import PasswordMatch, SourceType

_USER_COMMENT_CHARSET_PREFIXES = (
    b"ASCII\x00\x00\x00",
    b"UNICODE\x00",
    b"JIS\x00\x00\x00\x00\x00",
)


def _decode_exif_value(value: object) -> str | None:
    if isinstance(value, bytes):
        for prefix in _USER_COMMENT_CHARSET_PREFIXES:
            if value.startswith(prefix):
                value = value[len(prefix) :]
                break
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(value, str):
        return value
    return None


class ImageExifExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if not content_type.startswith("image/"):
            return []

        try:
            with Image.open(io.BytesIO(content)) as image:
                exif = image.getexif()
        except (OSError, DecompressionBombError):
            return []

        matches: list[PasswordMatch] = []
        for tag_id, value in exif.items():
            text = _decode_exif_value(value)
            if not text:
                continue
            tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
            matches.extend(self._matches_for(text, url, f"exif:{tag_name}"))
        return matches

    def _matches_for(self, text: str, url: str, locator: str) -> list[PasswordMatch]:
        return [
            PasswordMatch(
                value=match.value,
                source_type=SourceType.IMAGE_METADATA,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=locator,
            )
            for match in find_passwords(
                text, before=self._context_chars, after=self._context_chars
            )
        ]
