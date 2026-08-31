import io
import logging

import pytesseract
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.extractors.image_ocr import ImageOcrExtractor, _preprocess
from app.models import SourceType

PASSWORD = "VISUALPING{e1c2e40cf01c17cc}"
WHITEBOARD_URL = "https://example.com/static/img/whiteboard-scan.png"


def _tesseract_available() -> bool:
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _tesseract_available(), reason="Tesseract OCR binary not installed"
)


def _render_text_image(text: str, font_size: int = 48, pad: int = 20) -> bytes:
    """Draws `text` as pixels onto a plain image, sized to fit the text at
    the given font size plus padding -- the same shape as a photographed
    whiteboard or a screenshot of handwritten/typed text: the password
    exists only as a drawn glyph shape, never as bytes an ordinary
    text/metadata scan could find. (A fixed canvas size independent of
    font size risks clipping the text at small sizes, which garbles OCR
    for reasons unrelated to what this module is testing.)"""
    font = ImageFont.load_default(size=font_size)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    image = Image.new("RGB", (right - left + pad * 2, bottom - top + pad * 2), color="white")
    draw = ImageDraw.Draw(image)
    draw.text((pad, pad), text, fill="black", font=font)
    buf = io.BytesIO()
    image.save(buf, format="png")
    return buf.getvalue()


def _render_gradient_image(width: int = 200, height: int = 100) -> bytes:
    """A plain decorative gradient with no drawn text -- mirrors the real
    target's other, hidden-flag-free images (issue #109: 4 of 8 images on
    the target were plain gradient PNGs)."""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for x in range(width):
        shade = int(255 * x / max(width - 1, 1))
        for y in range(height):
            pixels[x, y] = (shade, shade, 255)
    buf = io.BytesIO()
    image.save(buf, format="png")
    return buf.getvalue()


def test_reads_password_drawn_as_pixels():
    content = _render_text_image(PASSWORD)
    extractor = ImageOcrExtractor()

    matches = extractor.extract(content, "image/png", WHITEBOARD_URL)

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.IMAGE_OCR
    assert matches[0].source_url == WHITEBOARD_URL
    assert matches[0].locator.startswith("ocr:offset:")


def test_ignores_non_image_content_type():
    content = _render_text_image(PASSWORD)
    extractor = ImageOcrExtractor()

    matches = extractor.extract(content, "text/html", WHITEBOARD_URL)

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


def test_preprocess_upscales_and_binarizes_to_black_and_white():
    image = Image.new("RGB", (30, 10), color=(120, 120, 120))
    processed = _preprocess(image)

    assert processed.size == (120, 40)  # 4x upscale, both dimensions
    assert set(processed.getdata()) <= {0, 255}  # thresholded to pure black/white


def test_reads_small_rendered_password_via_preprocessing_and_multi_psm():
    """Regression test for issue #109: a small, low-resolution rendered
    flag -- the same shape as the real target's static/img/whiteboard-
    scan.png (same password value and filename) -- that the pre-#109
    single-pass, no-preprocessing OCR call was prone to missing or
    garbling. We don't have the real target's file, so this uses the
    closest faithful synthetic substitute: the identical flag value at a
    small font size. font_size=18 was chosen empirically: run locally
    against a real Tesseract install, a bare `pytesseract.image_to_string`
    call on this exact image (no preprocessing) misreads a character as
    the unicode replacement char (e.g. 'VISUALPING[e1<?>2e40cf01<?>17cc}'),
    while the upscale+threshold preprocessing and --psm 6/--psm 11 merge
    below reads it correctly -- i.e. this fixture is small enough to
    actually exercise the fix, not just OCR's baseline accuracy."""
    content = _render_text_image(PASSWORD, font_size=18)
    extractor = ImageOcrExtractor()

    matches = extractor.extract(content, "image/png", WHITEBOARD_URL)

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.IMAGE_OCR


def test_matching_text_found_by_both_psm_passes_is_not_duplicated(monkeypatch):
    call_count = 0

    def fake_image_to_string(image, config):
        nonlocal call_count
        call_count += 1
        return PASSWORD

    monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)
    extractor = ImageOcrExtractor()

    matches = extractor.extract(_render_gradient_image(), "image/png", "https://example.com/x.png")

    assert call_count == 2  # both --psm 6 and --psm 11 were tried
    assert len(matches) == 1  # but the duplicate hit across both is merged, not doubled


def test_gradient_images_produce_no_false_positives():
    matches_a = ImageOcrExtractor().extract(
        _render_gradient_image(), "image/png", "https://example.com/gradient-a.png"
    )
    matches_b = ImageOcrExtractor().extract(
        _render_gradient_image(width=400, height=250),
        "image/png",
        "https://example.com/gradient-b.png",
    )

    assert matches_a == []
    assert matches_b == []


def test_ocr_failure_is_logged_and_degrades_to_no_matches(monkeypatch, caplog):
    def raise_tesseract_error(image, config):
        raise pytesseract.TesseractError(1, "simulated OCR engine failure")

    monkeypatch.setattr(pytesseract, "image_to_string", raise_tesseract_error)
    extractor = ImageOcrExtractor()

    with caplog.at_level(logging.WARNING):
        matches = extractor.extract(
            _render_gradient_image(), "image/png", "https://example.com/broken.png"
        )

    assert matches == []
    assert any("OCR pass" in record.message for record in caplog.records)


def test_tesseract_not_found_is_logged_once_and_degrades_to_no_matches(monkeypatch, caplog):
    call_count = 0

    def raise_not_found(image, config):
        nonlocal call_count
        call_count += 1
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_string", raise_not_found)
    extractor = ImageOcrExtractor()

    with caplog.at_level(logging.WARNING):
        matches = extractor.extract(
            _render_gradient_image(), "image/png", "https://example.com/no-binary.png"
        )

    assert matches == []
    assert call_count == 1  # short-circuits rather than retrying the second PSM config
    assert any("Tesseract binary not found" in record.message for record in caplog.records)


def test_vision_fallback_used_only_when_ocr_finds_nothing():
    calls: list[str] = []

    def vision_fallback(content: bytes, content_type: str, url: str) -> list[str]:
        calls.append(url)
        return [f"some text mentioning {PASSWORD} in the image"]

    # Blank image: OCR alone finds nothing, so the fallback is invoked and
    # its transcription is scanned for the password.
    blank_matches = ImageOcrExtractor(vision_fallback=vision_fallback).extract(
        _blank_png_bytes(), "image/png", "https://example.com/blank.png"
    )
    assert len(blank_matches) == 1
    assert blank_matches[0].value == PASSWORD
    assert blank_matches[0].locator.startswith("vision:offset:")
    assert calls == ["https://example.com/blank.png"]

    # A page where OCR already finds the password: the fallback must not
    # be invoked at all (it would raise here if called, proving it wasn't).
    def poison_fallback(content: bytes, content_type: str, url: str) -> list[str]:
        raise AssertionError("vision fallback should not run when OCR already found a match")

    ocr_matches = ImageOcrExtractor(vision_fallback=poison_fallback).extract(
        _render_text_image(PASSWORD), "image/png", WHITEBOARD_URL
    )
    assert len(ocr_matches) == 1
    assert ocr_matches[0].locator.startswith("ocr:offset:")


def test_vision_fallback_failure_is_logged_and_degrades_to_no_matches(caplog):
    def broken_fallback(content: bytes, content_type: str, url: str) -> list[str]:
        raise RuntimeError("simulated vision API failure")

    extractor = ImageOcrExtractor(vision_fallback=broken_fallback)

    with caplog.at_level(logging.WARNING):
        matches = extractor.extract(
            _blank_png_bytes(),
            "image/png",
            "https://example.com/blank.png",
        )

    assert matches == []
    assert any("vision fallback failed" in record.message for record in caplog.records)


def _blank_png_bytes() -> bytes:
    image = Image.new("RGB", (200, 100), color="white")
    buf = io.BytesIO()
    image.save(buf, format="png")
    return buf.getvalue()
