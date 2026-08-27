import io

from PIL import Image, PngImagePlugin

from app.extractors.binary_fallback import BinaryFallbackExtractor
from app.models import SourceType

PASSWORD = "VISUALPING{abcdef1234567890}"


def _binary_fixture() -> bytes:
    return (
        b"\x00\x01\x02\xff\xfe\xfd"
        + b"some garbage bytes "
        + PASSWORD.encode("ascii")
        + b" more garbage \x80\x81\x82"
    )


def test_extracts_password_from_arbitrary_binary_content():
    content = _binary_fixture()
    extractor = BinaryFallbackExtractor()

    matches = extractor.extract(content, "application/octet-stream", "https://example.com/file.bin")

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.BINARY
    assert matches[0].source_url == "https://example.com/file.bin"


def test_locator_is_correct_byte_offset():
    content = _binary_fixture()
    extractor = BinaryFallbackExtractor()

    matches = extractor.extract(content, "application/octet-stream", "https://example.com/file.bin")

    expected_offset = content.index(PASSWORD.encode("ascii"))
    assert matches[0].locator == f"offset:{expected_offset}"


def test_does_not_raise_on_non_utf8_bytes():
    content = bytes(range(256)) + PASSWORD.encode("ascii")
    extractor = BinaryFallbackExtractor()

    matches = extractor.extract(content, "application/octet-stream", "https://example.com/file.bin")

    assert len(matches) == 1


def test_skips_content_types_handled_by_other_extractors():
    content = _binary_fixture()
    extractor = BinaryFallbackExtractor()

    for content_type in ("text/html", "text/css", "application/javascript"):
        assert extractor.extract(content, content_type, "https://example.com/x") == []


def test_does_not_skip_image_content_types():
    """Regression test: images used to be blanket-excluded on the
    assumption ImageExifExtractor covers them, but EXIF only covers
    EXIF -- a plaintext-in-the-file-bytes password outside any EXIF
    field (e.g. a PNG text chunk, see the test below) needs this
    extractor to still run on image content."""
    content = _binary_fixture()
    extractor = BinaryFallbackExtractor()

    matches = extractor.extract(content, "image/png", "https://example.com/photo.png")

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.BINARY


def test_finds_password_in_png_text_chunk_metadata():
    """PNG `tEXt` chunks (e.g. a "Comment" field) are a completely
    different mechanism from EXIF -- plain, uncompressed strings sitting
    directly in the file bytes, invisible to ImageExifExtractor's
    getexif()-based scan. This extractor's raw byte scan catches them
    without needing any PNG-specific parsing."""
    image = Image.new("RGB", (2, 2), color="blue")
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Comment", f"debug note {PASSWORD}")
    buf = io.BytesIO()
    image.save(buf, format="PNG", pnginfo=meta)
    content = buf.getvalue()

    matches = BinaryFallbackExtractor().extract(
        content, "image/png", "https://example.com/photo.png"
    )

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.BINARY


def test_no_match_returns_empty_list():
    extractor = BinaryFallbackExtractor()

    matches = extractor.extract(
        b"\x00\x01\x02no password here\xff",
        "application/octet-stream",
        "https://example.com/file.bin",
    )

    assert matches == []
