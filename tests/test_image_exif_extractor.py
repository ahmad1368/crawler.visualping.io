import io

from PIL import Image

from app.extractors.image_exif import ImageExifExtractor
from app.models import SourceType

USER_COMMENT_TAG_ID = 37510
IMAGE_DESCRIPTION_TAG_ID = 270

PASSWORD = "VISUALPING{abcdef1234567890}"


def _make_jpeg_with_exif(tags: dict[int, object]) -> bytes:
    image = Image.new("RGB", (2, 2), color="white")
    exif = image.getexif()
    for tag_id, value in tags.items():
        exif[tag_id] = value
    buf = io.BytesIO()
    image.save(buf, format="jpeg", exif=exif.tobytes())
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
