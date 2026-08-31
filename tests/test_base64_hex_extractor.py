import base64

from app.extractors.base64_hex import Base64HexExtractor
from app.models import SourceType

PASSWORD = "VISUALPING{abcdef1234567890}"
URL = "https://example.com/app.js"


def test_finds_password_encoded_as_standard_base64():
    encoded = base64.b64encode(PASSWORD.encode()).decode()
    content = f"const token = '{encoded}';".encode()
    extractor = Base64HexExtractor()

    matches = extractor.extract(content, "application/javascript", URL)

    # Standard and URL-safe alphabets decode identically here (no +/-_
    # characters in this particular encoding) -- a harmless duplicate at
    # this level, same as other extractors' multi-attempt patterns;
    # downstream dedup groups by (source_url, value).
    assert matches
    assert {m.value for m in matches} == {PASSWORD}
    assert matches[0].source_type == SourceType.BASE64_HEX
    assert matches[0].locator.startswith("base64-hex:")


def test_finds_password_encoded_as_urlsafe_base64():
    encoded = base64.urlsafe_b64encode(PASSWORD.encode()).decode()
    content = f"const token = '{encoded}';".encode()
    extractor = Base64HexExtractor()

    matches = extractor.extract(content, "application/javascript", URL)

    assert any(m.value == PASSWORD for m in matches)


def test_finds_password_encoded_as_hex():
    encoded = PASSWORD.encode().hex()
    content = f"const token = '{encoded}';".encode()
    extractor = Base64HexExtractor()

    matches = extractor.extract(content, "text/html", URL)

    assert any(m.value == PASSWORD for m in matches)


def test_raw_text_never_contains_the_password_literally():
    encoded = base64.b64encode(PASSWORD.encode()).decode()
    content = f"const token = '{encoded}';".encode()

    assert PASSWORD not in content.decode()


def test_no_match_in_ordinary_text_with_no_encoded_payload():
    content = b"<html><body>nothing interesting here, just words</body></html>"
    extractor = Base64HexExtractor()

    matches = extractor.extract(content, "text/html", URL)

    assert matches == []


def test_ignores_non_text_content_type():
    encoded = base64.b64encode(PASSWORD.encode()).decode()
    extractor = Base64HexExtractor()

    matches = extractor.extract(encoded.encode(), "image/png", URL)

    assert matches == []


def test_malformed_content_degrades_to_no_matches_without_raising():
    extractor = Base64HexExtractor()

    matches = extractor.extract(b"\xff\xfe not valid utf-8 or base64", "text/plain", URL)

    assert matches == []
