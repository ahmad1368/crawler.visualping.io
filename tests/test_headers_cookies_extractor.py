import base64
import codecs

from app.extractors.headers_cookies import HeaderCookieExtractor
from app.models import SourceType

HEADER_PASSWORD = "VISUALPING{abcdef1234567890}"
COOKIE_PASSWORD = "VISUALPING{0123456789abcdef}"


def _mock_response():
    headers = {
        "Content-Type": "text/html",
        "X-Debug-Password": f"leaked={HEADER_PASSWORD}",
    }
    cookies = {
        "session_id": "abc123",
        "session_backup": f"token={COOKIE_PASSWORD}",
    }
    return headers, cookies


def test_extracts_password_from_custom_header():
    headers, cookies = _mock_response()
    extractor = HeaderCookieExtractor()

    matches = extractor.extract(headers, cookies, "https://example.com/page")

    header_matches = [m for m in matches if m.source_type == SourceType.HTTP_HEADER]
    assert len(header_matches) == 1
    assert header_matches[0].value == HEADER_PASSWORD
    assert header_matches[0].locator == "header:X-Debug-Password"
    assert header_matches[0].source_url == "https://example.com/page"


def test_extracts_password_from_cookie():
    headers, cookies = _mock_response()
    extractor = HeaderCookieExtractor()

    matches = extractor.extract(headers, cookies, "https://example.com/page")

    cookie_matches = [m for m in matches if m.source_type == SourceType.COOKIE]
    assert len(cookie_matches) == 1
    assert cookie_matches[0].value == COOKIE_PASSWORD
    assert cookie_matches[0].locator == "cookie:session_backup"


def test_ignores_headers_and_cookies_without_matches():
    headers, cookies = _mock_response()
    extractor = HeaderCookieExtractor()

    matches = extractor.extract(headers, cookies, "https://example.com/page")

    assert not any(m.locator == "header:Content-Type" for m in matches)
    assert not any(m.locator == "cookie:session_id" for m in matches)
    assert len(matches) == 2


def test_empty_headers_and_cookies_return_no_matches():
    extractor = HeaderCookieExtractor()

    assert extractor.extract({}, {}, "https://example.com/page") == []


def test_scans_every_header_name_not_a_fixed_allowlist():
    # Every response header is scanned by value, regardless of name --
    # there's no allowlist of "known" header names to check against.
    password = "VISUALPING{abcdef1234567890}"
    headers = {"X-Totally-Unexpected-Header-Name": password}
    extractor = HeaderCookieExtractor()

    matches = extractor.extract(headers, {}, "https://example.com/page")

    assert len(matches) == 1
    assert matches[0].value == password
    assert matches[0].locator == "header:X-Totally-Unexpected-Header-Name"


def test_finds_base64_encoded_password_in_a_header_value():
    password = "VISUALPING{aabbccddeeff0011}"
    encoded = base64.b64encode(password.encode()).decode()
    headers = {"X-Debug-Token": encoded}
    extractor = HeaderCookieExtractor()

    matches = extractor.extract(headers, {}, "https://example.com/page")

    assert any(m.value == password and m.source_type == SourceType.BASE64_HEX for m in matches)
    assert any(m.locator == "header:X-Debug-Token:base64-hex" for m in matches)


def test_finds_rot13_encoded_password_in_a_cookie_value():
    password = "VISUALPING{aabbccddeeff0011}"
    encoded = codecs.encode(password, "rot13")
    cookies = {"debug": encoded}
    extractor = HeaderCookieExtractor()

    matches = extractor.extract({}, cookies, "https://example.com/page")

    assert any(m.value == password and m.source_type == SourceType.ROT13 for m in matches)
    assert any(m.locator == "cookie:debug:rot13" for m in matches)


def test_finds_reversed_password_in_a_header_value():
    password = "VISUALPING{aabbccddeeff0011}"
    headers = {"X-Debug-Token": password[::-1]}
    extractor = HeaderCookieExtractor()

    matches = extractor.extract(headers, {}, "https://example.com/page")

    assert any(m.value == password and m.source_type == SourceType.REVERSED_TEXT for m in matches)
    assert any(m.locator == "header:X-Debug-Token:reversed" for m in matches)
