import io

import piexif
from PIL import Image

from app.extractors.image_exif import ImageExifExtractor
from app.models import SourceType

USER_COMMENT_TAG_ID = 37510
IMAGE_DESCRIPTION_TAG_ID = 270

PASSWORD = "VISUALPING{abcdef1234567890}"


def _make_jpeg_with_exif(tags: dict[int, object]) -> bytes:
    """Build a JPEG with tags written flatly into IFD0.

    Fine for exercising IFD0-only fields (e.g. ImageDescription), but
    NOT representative of how a real tool stores UserComment -- see
    `_make_jpeg_with_nested_exif` below.
    """
    image = Image.new("RGB", (2, 2), color="white")
    exif = image.getexif()
    for tag_id, value in tags.items():
        exif[tag_id] = value
    buf = io.BytesIO()
    image.save(buf, format="jpeg", exif=exif.tobytes())
    return buf.getvalue()


def _make_jpeg_with_nested_exif(
    exif_ifd_tags: dict[int, object] | None = None,
    gps_ifd_tags: dict[int, object] | None = None,
) -> bytes:
    """Build a JPEG with tags written the way a real tool (a camera,
    exiftool, piexif) does: `UserComment` etc. live in the nested "Exif"
    sub-IFD, `GPS*` fields in the nested GPS IFD -- not flat in IFD0."""
    image = Image.new("RGB", (2, 2), color="white")
    exif_dict: dict[str, dict[int, object]] = {
        "0th": {},
        "Exif": exif_ifd_tags or {},
        "GPS": gps_ifd_tags or {},
        "1st": {},
        "thumbnail": None,
    }
    exif_bytes = piexif.dump(exif_dict)
    buf = io.BytesIO()
    image.save(buf, format="jpeg", exif=exif_bytes)
    return buf.getvalue()


def test_extracts_password_from_user_comment():
    content = _make_jpeg_with_exif(
        {USER_COMMENT_TAG_ID: b"ASCII\x00\x00\x00" + PASSWORD.encode("ascii")}
    )
    extractor = ImageExifExtractor()

    matches = extractor.extract(content, "image/jpeg", "https://example.com/photo.jpg")

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.IMAGE_METADATA
    assert matches[0].locator == "exif:UserComment"
    assert matches[0].source_url == "https://example.com/photo.jpg"


def test_extracts_password_from_user_comment_in_nested_exif_subifd():
    """Regression test for the real-world shape: UserComment written into
    the nested "Exif" sub-IFD (tag 0x8769), the way piexif/exiftool/a real
    camera writes it -- not flattened into IFD0 like the fixture above."""
    content = _make_jpeg_with_nested_exif(
        exif_ifd_tags={piexif.ExifIFD.UserComment: b"ASCII\x00\x00\x00" + PASSWORD.encode("ascii")}
    )
    extractor = ImageExifExtractor()

    matches = extractor.extract(content, "image/jpeg", "https://example.com/photo.jpg")

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.IMAGE_METADATA
    assert matches[0].locator == "exif:UserComment"


def test_extracts_password_from_utf16_le_user_comment():
    """UserComment's "UNICODE\\0" charset prefix (EXIF spec) means the
    payload is UTF-16, not UTF-8 -- decoding it as UTF-8 mangles every
    character, which is exactly why a site would pick UTF-16 for a
    UserComment password: it defeats a naive text-string search."""
    content = _make_jpeg_with_nested_exif(
        exif_ifd_tags={piexif.ExifIFD.UserComment: b"UNICODE\x00" + PASSWORD.encode("utf-16-le")}
    )
    extractor = ImageExifExtractor()

    matches = extractor.extract(content, "image/jpeg", "https://example.com/photo.jpg")

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.IMAGE_METADATA
    assert matches[0].locator == "exif:UserComment"


def test_extracts_password_from_utf16_be_user_comment():
    """Same as above but big-endian -- Pillow doesn't expose the TIFF
    byte-order flag needed to know which endianness a given file used, so
    both must be tried."""
    content = _make_jpeg_with_nested_exif(
        exif_ifd_tags={piexif.ExifIFD.UserComment: b"UNICODE\x00" + PASSWORD.encode("utf-16-be")}
    )
    extractor = ImageExifExtractor()

    matches = extractor.extract(content, "image/jpeg", "https://example.com/photo.jpg")

    assert len(matches) == 1
    assert matches[0].value == PASSWORD


def test_extracts_password_from_gps_ifd():
    content = _make_jpeg_with_nested_exif(
        gps_ifd_tags={piexif.GPSIFD.GPSProcessingMethod: PASSWORD.encode("ascii")}
    )
    extractor = ImageExifExtractor()

    matches = extractor.extract(content, "image/jpeg", "https://example.com/photo.jpg")

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].locator == "exif-gps:GPSProcessingMethod"


def test_extracts_password_from_image_description():
    content = _make_jpeg_with_exif({IMAGE_DESCRIPTION_TAG_ID: PASSWORD})
    extractor = ImageExifExtractor()

    matches = extractor.extract(content, "image/jpeg", "https://example.com/photo.jpg")

    assert len(matches) == 1
    assert matches[0].locator == "exif:ImageDescription"


def test_image_without_matching_exif_returns_no_matches():
    content = _make_jpeg_with_exif({IMAGE_DESCRIPTION_TAG_ID: "just a normal photo"})
    extractor = ImageExifExtractor()

    matches = extractor.extract(content, "image/jpeg", "https://example.com/photo.jpg")

    assert matches == []


def test_ignores_non_image_content_type():
    content = _make_jpeg_with_exif({IMAGE_DESCRIPTION_TAG_ID: PASSWORD})
    extractor = ImageExifExtractor()

    matches = extractor.extract(content, "text/html", "https://example.com/photo.jpg")

    assert matches == []


def test_handles_unparseable_image_content_gracefully():
    extractor = ImageExifExtractor()

    matches = extractor.extract(b"not a real image", "image/jpeg", "https://example.com/photo.jpg")

    assert matches == []
