"""Extractor for exposed passwords in image EXIF metadata.

Reads EXIF fields (e.g. `UserComment`, `ImageDescription`) from downloaded
images via Pillow. Operators sometimes stash debug notes -- including
credentials -- in these fields without realizing they ship with the image.

`Image.getexif()` only returns the base IFD0 tags (`ImageDescription`,
`Make`, `DateTime`, ...) -- fields like `UserComment` or `DateTimeOriginal`
live in the nested "Exif" sub-IFD (pointer tag 0x8769), and GPS fields live
in their own nested IFD (pointer tag 0x8825). Both are reachable only via
`exif.get_ifd(...)`, never through the top-level `Exif` mapping's own
`.items()` -- any real-world tool (a camera, exiftool, piexif) writes
`UserComment` there, so skipping this misses it entirely.
"""

from __future__ import annotations

import io
from collections.abc import Mapping

from PIL import ExifTags, Image
from PIL.Image import DecompressionBombError

from app.matching import find_passwords
from app.models import PasswordMatch, SourceType

_USER_COMMENT_CHARSET_PREFIXES = (
    b"ASCII\x00\x00\x00",
    b"UNICODE\x00",
    b"JIS\x00\x00\x00\x00\x00",
)
_UNICODE_PREFIX = b"UNICODE\x00"


def _decode_exif_candidates(value: object) -> list[str]:
    """Return every plausible plaintext decoding of a raw EXIF field value.

    A `UserComment` value carries an 8-byte charset prefix (EXIF spec, tag
    0x9286) identifying how the remaining bytes are encoded. The
    "UNICODE\\0" prefix means UTF-16 -- decoding that payload as UTF-8
    (the old behavior here) mangles every character into replacement
    bytes, so a plaintext password hidden that way was never found; a
    site can pick UTF-16 specifically because it defeats a naive
    text-string search, which is exactly what happened here. Pillow
    doesn't expose the TIFF byte-order flag needed to know LE vs BE, so
    both are decoded and returned as separate candidates -- a real
    password only ever matches the correctly-endianed one, the other
    decodes to unmatched noise, so trying both risks no duplicate match.
    """
    if isinstance(value, str):
        return [value]
    if not isinstance(value, bytes):
        return []

    payload = value
    for prefix in _USER_COMMENT_CHARSET_PREFIXES:
        if value.startswith(prefix):
            payload = value[len(prefix) :]
            if prefix == _UNICODE_PREFIX:
                return [
                    payload.decode("utf-16-le", errors="replace"),
                    payload.decode("utf-16-be", errors="replace"),
                ]
            break

    try:
        return [payload.decode("utf-8", errors="replace")]
    except Exception:
        return []


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
        matches.extend(self._scan_ifd(exif, ExifTags.TAGS, "exif", url))
        matches.extend(self._scan_ifd(exif.get_ifd(ExifTags.IFD.Exif), ExifTags.TAGS, "exif", url))
        matches.extend(
            self._scan_ifd(exif.get_ifd(ExifTags.IFD.GPSInfo), ExifTags.GPSTAGS, "exif-gps", url)
        )
        return matches

    def _scan_ifd(
        self, ifd: Mapping[int, object], tag_names: dict[int, str], locator_prefix: str, url: str
    ) -> list[PasswordMatch]:
        matches: list[PasswordMatch] = []
        for tag_id, value in ifd.items():
            tag_name = tag_names.get(tag_id, str(tag_id))
            for text in _decode_exif_candidates(value):
                if not text:
                    continue
                matches.extend(self._matches_for(text, url, f"{locator_prefix}:{tag_name}"))
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
            for match in find_passwords(text, before=self._context_chars, after=self._context_chars)
        ]
