import io

import pytesseract
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.extractors.image_ocr import ImageOcrExtractor
from app.models import SourceType

PASSWORD = "VISUALPING{e1c2e40cf01c17cc}"


def _tesseract_available() -> bool:
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _tesseract_available(), reason="Tesseract OCR binary not installed"
)


def _render_text_image(text: str, width: int = 900, height: int = 150) -> bytes:
    """Draws `text` as pixels onto a plain image -- the same shape as a
    photographed whiteboard or a screenshot of handwritten/typed text:
    the password exists only as a drawn glyph shape, never as bytes an
    ordinary text/metadata scan could find."""
    image = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=48)
    draw.text((20, 20), text, fill="black", font=font)
    buf = io.BytesIO()
    image.save(buf, format="png")
    return buf.getvalue()


def test_reads_password_drawn_as_pixels():
    content = _render_text_image(PASSWORD)
    extractor = ImageOcrExtractor()

    matches = extractor.extract(content, "image/png", "https://example.com/whiteboard-scan.png")

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.IMAGE_OCR
    assert matches[0].source_url == "https://example.com/whiteboard-scan.png"
    assert matches[0].locator.startswith("ocr:offset:")


def test_ignores_non_image_content_type():
    content = _render_text_image(PASSWORD)
    extractor = ImageOcrExtractor()

    matches = extractor.extract(content, "text/html", "https://example.com/whiteboard-scan.png")

    assert matches == []


def test_image_with_no_drawn_text_returns_no_matches():
    image = Image.new("RGB", (200, 100), color="white")
    buf = io.BytesIO()
    image.save(buf, format="png")
    extractor = ImageOcrExtractor()

    matches = extractor.extract(buf.getvalue(), "image/png", "https://example.com/blank.png")

    assert matches == []


def test_handles_unparseable_image_content_gracefully():
    extractor = ImageOcrExtractor()

    matches = extractor.extract(b"not a real image", "image/png", "https://example.com/photo.png")

    assert matches == []
