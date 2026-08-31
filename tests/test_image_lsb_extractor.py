import io

from PIL import Image

from app.extractors.image_lsb import ImageLsbExtractor
from app.models import SourceType

PASSWORD = "VISUALPING{aabbccddeeff0011}"


def _encode_lsb_message(image: Image.Image, message: bytes) -> Image.Image:
    """Embed `message` into the image's pixel LSBs using the exact same
    order the extractor reads them back in (RGBA.tobytes(), row-major,
    per-pixel R,G,B,A, MSB-first bit packing) -- the reference encoder
    for these tests."""
    rgba = image.convert("RGBA")
    channel_bytes = bytearray(rgba.tobytes())

    bits = []
    for byte in message:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)

    if len(bits) > len(channel_bytes):
        raise ValueError("image too small to carry this message")

    for i, bit in enumerate(bits):
        channel_bytes[i] = (channel_bytes[i] & 0xFE) | bit

    # Stay in RGBA (not converted back to RGB) so the embedded alpha-
    # channel bits survive the save -- PNG supports RGBA losslessly, and
    # the extractor's own image.convert("RGBA") on read must see exactly
    # these bytes back, not a freshly synthesized opaque alpha channel.
    return Image.frombytes("RGBA", rgba.size, bytes(channel_bytes))


def _png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="png")
    return buf.getvalue()


def test_finds_password_hidden_in_pixel_lsbs():
    # Large enough (100x100x4 = 40000 channel bytes = 40000 bits
    # available) to carry the short embedded message comfortably.
    base = Image.new("RGB", (100, 100), color=(120, 130, 140))
    message = f"noise {PASSWORD} noise".encode()
    encoded = _encode_lsb_message(base, message)
    content = _png_bytes(encoded)

    extractor = ImageLsbExtractor()
    matches = extractor.extract(content, "image/png", "https://example.com/whiteboard-scan.png")

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.IMAGE_LSB
    assert matches[0].locator.startswith("lsb:offset:")


def test_plain_image_with_no_hidden_message_is_ruled_out():
    """No false positives: an ordinary, unmodified image's low-order
    pixel bits are just the image's real color data, not a message --
    must not coincidentally match the flag pattern."""
    image = Image.new("RGB", (50, 50), color=(200, 50, 90))
    content = _png_bytes(image)

    extractor = ImageLsbExtractor()
    matches = extractor.extract(content, "image/png", "https://example.com/photo.png")

    assert matches == []


def test_ignores_non_image_content_type():
    extractor = ImageLsbExtractor()

    matches = extractor.extract(PASSWORD.encode(), "text/plain", "https://example.com/x")

    assert matches == []


def test_malformed_image_degrades_to_no_matches_without_raising():
    extractor = ImageLsbExtractor()

    matches = extractor.extract(b"not a real image", "image/png", "https://example.com/x.png")

    assert matches == []


def test_works_on_images_without_an_alpha_channel():
    """RGB-only source images are converted to RGBA before extraction --
    a message can still be encoded/decoded even though the source never
    had a real alpha channel of its own."""
    base = Image.new("RGB", (80, 80), color=(10, 20, 30))
    encoded = _encode_lsb_message(base, PASSWORD.encode())
    content = _png_bytes(encoded)

    extractor = ImageLsbExtractor()
    matches = extractor.extract(content, "image/png", "https://example.com/photo.png")

    assert any(m.value == PASSWORD for m in matches)
