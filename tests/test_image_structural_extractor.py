import io
import struct
import zlib

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app.extractors.image_structural import ImageStructuralExtractor
from app.models import SourceType

PASSWORD = "VISUALPING{abcdef1234567890}"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _make_jpeg_with_comment(comment: bytes) -> bytes:
    image = Image.new("RGB", (2, 2), color="white")
    buf = io.BytesIO()
    image.save(buf, format="jpeg", comment=comment)
    return buf.getvalue()


def _make_png_with_text(**text_chunks: str) -> bytes:
    image = Image.new("RGB", (2, 2), color="white")
    info = PngInfo()
    for key, value in text_chunks.items():
        info.add_text(key, value)
    buf = io.BytesIO()
    image.save(buf, format="png", pnginfo=info)
    return buf.getvalue()


def _make_png_with_compressed_text(keyword: str, value: str) -> bytes:
    image = Image.new("RGB", (2, 2), color="white")
    info = PngInfo()
    info.add_text(keyword, value, zip=True)
    buf = io.BytesIO()
    image.save(buf, format="png", pnginfo=info)
    return buf.getvalue()


def _make_png_with_compressed_itxt(keyword: str, value: str) -> bytes:
    image = Image.new("RGB", (2, 2), color="white")
    info = PngInfo()
    info.add_itxt(keyword, value, zip=True)
    buf = io.BytesIO()
    image.save(buf, format="png", pnginfo=info)
    return buf.getvalue()


def _raw_png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data))
    )


def _make_png_with_raw_text_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Build a minimal real PNG (IHDR + IDAT + IEND from Pillow) with one
    extra raw chunk inserted before IEND -- used for the adversarial
    UTF-16 case Pillow's own PngInfo API can't produce directly (it
    always writes tEXt as Latin-1 per the PNG spec)."""
    image = Image.new("RGB", (2, 2), color="white")
    buf = io.BytesIO()
    image.save(buf, format="png")
    base = buf.getvalue()
    iend_index = base.rindex(b"IEND") - 4  # back up to the length field
    extra = _raw_png_chunk(chunk_type, data)
    return base[:iend_index] + extra + base[iend_index:]


def test_finds_password_in_jpeg_com_segment():
    content = _make_jpeg_with_comment(PASSWORD.encode("ascii"))
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(content, "image/jpeg", "https://example.com/photo.jpg")

    # Pure-ASCII content decodes identically under more than one of the
    # four tried encodings -- a harmless duplicate at this level, same as
    # existing extractors' multi-encoding-attempt pattern; downstream
    # dedup groups by (source_url, value).
    assert matches
    assert {m.value for m in matches} == {PASSWORD}
    assert matches[0].source_type == SourceType.IMAGE_METADATA
    assert matches[0].locator == "jpeg:COM"


def test_finds_password_in_jpeg_com_segment_encoded_utf16():
    content = _make_jpeg_with_comment(PASSWORD.encode("utf-16-le"))
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(content, "image/jpeg", "https://example.com/photo.jpg")

    assert any(m.value == PASSWORD for m in matches)


def test_no_match_when_jpeg_has_no_com_segment():
    content = _make_jpeg_with_comment(b"nothing interesting here")
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(content, "image/jpeg", "https://example.com/photo.jpg")

    assert matches == []


def test_finds_password_in_png_text_chunk():
    content = _make_png_with_text(Comment=PASSWORD)
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(content, "image/png", "https://example.com/photo.png")

    assert matches
    assert {m.value for m in matches} == {PASSWORD}
    assert matches[0].locator == "png:tEXt:Comment"


def test_finds_password_in_ztxt_compressed_chunk():
    """The core new capability: zTXt is zlib-compressed, invisible to any
    plain-text/raw-byte scan until decompressed."""
    content = _make_png_with_compressed_text("Comment", PASSWORD)
    assert b"zTXt" in content
    assert PASSWORD.encode() not in content  # confirms it's genuinely compressed
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(content, "image/png", "https://example.com/photo.png")

    assert matches
    assert {m.value for m in matches} == {PASSWORD}
    assert matches[0].locator == "png:zTXt:Comment"


def test_finds_password_in_compressed_itxt_chunk():
    content = _make_png_with_compressed_itxt("Comment", PASSWORD)
    assert b"iTXt" in content
    assert PASSWORD.encode() not in content
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(content, "image/png", "https://example.com/photo.png")

    assert matches
    assert {m.value for m in matches} == {PASSWORD}
    assert matches[0].locator == "png:iTXt:Comment"


def test_finds_password_in_text_chunk_encoded_utf16():
    """Adversarial case: a tEXt-type chunk whose value is UTF-16 rather
    than the PNG-spec-mandated Latin-1 -- Pillow's own writer can't
    produce this, so the chunk is built by hand."""
    data = b"Comment\x00" + PASSWORD.encode("utf-16-le")
    content = _make_png_with_raw_text_chunk(b"tEXt", data)
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(content, "image/png", "https://example.com/photo.png")

    assert any(m.value == PASSWORD for m in matches)


def test_no_match_when_png_has_no_text_chunks():
    content = _make_png_with_text()
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(content, "image/png", "https://example.com/photo.png")

    assert matches == []


def test_ignores_non_image_content_type():
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(PASSWORD.encode(), "text/plain", "https://example.com/x")

    assert matches == []


def test_malformed_jpeg_degrades_to_no_matches_without_raising():
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(b"\xff\xd8\xff\xfe\x00", "image/jpeg", "https://example.com/x.jpg")

    assert matches == []


def test_malformed_png_degrades_to_no_matches_without_raising():
    extractor = ImageStructuralExtractor()

    matches = extractor.extract(
        PNG_SIGNATURE + b"not a real chunk stream", "image/png", "https://example.com/x.png"
    )

    assert matches == []
