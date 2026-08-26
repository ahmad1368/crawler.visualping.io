from app.extractors.css_js import CssJsExtractor
from app.models import SourceType

CSS_PASSWORD = "VISUALPING{abcdef1234567890}"
JS_STRING_PASSWORD = "VISUALPING{0123456789abcdef}"
JS_COMMENT_PASSWORD = "VISUALPING{fedcba9876543210}"

CSS_FIXTURE = f"""
.hidden::before {{
  content: "{CSS_PASSWORD}";
}}
""".encode()

JS_FIXTURE = f"""
// backup credential: {JS_COMMENT_PASSWORD}
const legacyToken = "{JS_STRING_PASSWORD}";
""".encode()


def test_extracts_password_from_css_content_property():
    extractor = CssJsExtractor()

    matches = extractor.extract(CSS_FIXTURE, "text/css", "https://example.com/style.css")

    assert len(matches) == 1
    assert matches[0].value == CSS_PASSWORD
    assert matches[0].source_type == SourceType.CSS
    assert matches[0].source_url == "https://example.com/style.css"


def test_extracts_password_from_js_string_and_comment():
    extractor = CssJsExtractor()

    matches = extractor.extract(JS_FIXTURE, "application/javascript", "https://example.com/app.js")

    values = {m.value for m in matches}
    assert values == {JS_STRING_PASSWORD, JS_COMMENT_PASSWORD}
    assert all(m.source_type == SourceType.JS for m in matches)


def test_accepts_text_javascript_content_type_variant():
    extractor = CssJsExtractor()

    matches = extractor.extract(
        JS_FIXTURE, "text/javascript; charset=utf-8", "https://example.com/app.js"
    )

    assert len(matches) == 2
    assert all(m.source_type == SourceType.JS for m in matches)


def test_ignores_unrelated_content_type():
    extractor = CssJsExtractor()

    matches = extractor.extract(CSS_FIXTURE, "text/html", "https://example.com/style.css")

    assert matches == []


def test_locator_reports_line_and_column():
    extractor = CssJsExtractor()

    matches = extractor.extract(CSS_FIXTURE, "text/css", "https://example.com/style.css")

    assert matches[0].locator.startswith("line:3,col:")
