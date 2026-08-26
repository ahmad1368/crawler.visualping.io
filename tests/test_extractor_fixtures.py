"""Consolidated fixture-based coverage across every extractor.

Builds on the per-extractor tests added in issues #9-13 (which also cover
rejection/edge cases) -- this file's job is just to confirm, in one
parametrized pass, that every extractor finds its embedded password with
the right source_type and correct surrounding context, using one shared
fixture per source type under tests/fixtures/.
"""

import json
from pathlib import Path

import pytest

from app.extractors.binary_fallback import BinaryFallbackExtractor
from app.extractors.css_js import CssJsExtractor
from app.extractors.headers_cookies import HeaderCookieExtractor
from app.extractors.html import HtmlExtractor
from app.extractors.image_exif import ImageExifExtractor
from app.models import SourceType

FIXTURES_DIR = Path(__file__).parent / "fixtures"
URL = "https://example.com/resource"


def _html_matches():
    content = (FIXTURES_DIR / "html_sample.html").read_bytes()
    return HtmlExtractor().extract(content, "text/html", URL)


def _css_matches():
    content = (FIXTURES_DIR / "css_sample.css").read_bytes()
    return CssJsExtractor().extract(content, "text/css", URL)


def _js_matches():
    content = (FIXTURES_DIR / "js_sample.js").read_bytes()
    return CssJsExtractor().extract(content, "application/javascript", URL)


def _header_cookie_matches():
    fixture = json.loads((FIXTURES_DIR / "http_header_cookie_sample.json").read_text())
    return HeaderCookieExtractor().extract(fixture["headers"], fixture["cookies"], URL)


def _image_matches():
    content = (FIXTURES_DIR / "image_sample.jpg").read_bytes()
    return ImageExifExtractor().extract(content, "image/jpeg", URL)


def _binary_matches():
    content = (FIXTURES_DIR / "binary_sample.bin").read_bytes()
    return BinaryFallbackExtractor().extract(content, "application/octet-stream", URL)


CASES = [
    pytest.param(
        _html_matches,
        SourceType.HTML_TEXT,
        "VISUALPING{1111111111111111}",
        "password is ",
        " right here",
        id="html_text",
    ),
    pytest.param(
        _html_matches,
        SourceType.HTML_COMMENT,
        "VISUALPING{2222222222222222}",
        "backup: ",
        "",
        id="html_comment",
    ),
    pytest.param(
        _css_matches,
        SourceType.CSS,
        "VISUALPING{4444444444444444}",
        'content: "',
        '"',
        id="css",
    ),
    pytest.param(
        _js_matches,
        SourceType.JS,
        "VISUALPING{5555555555555555}",
        "backup credential: ",
        "",
        id="js_comment",
    ),
    pytest.param(
        _js_matches,
        SourceType.JS,
        "VISUALPING{6666666666666666}",
        'legacyToken = "',
        '"',
        id="js_string",
    ),
    pytest.param(
        _header_cookie_matches,
        SourceType.HTTP_HEADER,
        "VISUALPING{7777777777777777}",
        "leaked=",
        "",
        id="http_header",
    ),
    pytest.param(
        _header_cookie_matches,
        SourceType.COOKIE,
        "VISUALPING{8888888888888888}",
        "token=",
        "",
        id="cookie",
    ),
    pytest.param(
        _image_matches,
        SourceType.IMAGE_METADATA,
        "VISUALPING{a1a1a1a1a1a1a1a1}",
        "",
        "",
        id="image_metadata",
    ),
    pytest.param(
        _binary_matches,
        SourceType.BINARY,
        "VISUALPING{b2b2b2b2b2b2b2b2}",
        "garbage before ",
        " garbage after",
        id="binary",
    ),
]


@pytest.mark.parametrize(
    "matches_factory,expected_source_type,expected_value,before_substring,after_substring",
    CASES,
)
def test_extractor_finds_embedded_password_with_context(
    matches_factory, expected_source_type, expected_value, before_substring, after_substring
):
    matches = matches_factory()

    found = [
        m for m in matches if m.value == expected_value and m.source_type == expected_source_type
    ]
    assert len(found) == 1, f"expected exactly one match for {expected_value!r}, got {found}"

    match = found[0]
    assert before_substring in match.context_before
    assert after_substring in match.context_after
