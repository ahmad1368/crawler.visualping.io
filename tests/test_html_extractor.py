from app.extractors.html import HtmlExtractor
from app.models import SourceType

TEXT_PASSWORD = "VISUALPING{abcdef1234567890}"
COMMENT_PASSWORD = "VISUALPING{0123456789abcdef}"
SCRIPT_PASSWORD = "VISUALPING{fedcba9876543210}"

FIXTURE_HTML = f"""
<html>
<body>
<p>Welcome! The password is {TEXT_PASSWORD} for testing.</p>
<!-- backup credential: {COMMENT_PASSWORD} -->
<script>var ignored = "{SCRIPT_PASSWORD}";</script>
</body>
</html>
""".encode()


def test_extracts_password_from_visible_text():
    extractor = HtmlExtractor()

    matches = extractor.extract(FIXTURE_HTML, "text/html", "https://example.com/page")

    text_matches = [m for m in matches if m.source_type == SourceType.HTML_TEXT]
    assert len(text_matches) == 1
    assert text_matches[0].value == TEXT_PASSWORD
    assert text_matches[0].source_url == "https://example.com/page"


def test_extracts_password_from_comment():
    extractor = HtmlExtractor()

    matches = extractor.extract(FIXTURE_HTML, "text/html", "https://example.com/page")

    comment_matches = [m for m in matches if m.source_type == SourceType.HTML_COMMENT]
    assert len(comment_matches) == 1
    assert comment_matches[0].value == COMMENT_PASSWORD


def test_ignores_script_content():
    extractor = HtmlExtractor()

    matches = extractor.extract(FIXTURE_HTML, "text/html", "https://example.com/page")

    assert all(m.value != SCRIPT_PASSWORD for m in matches)
    assert len(matches) == 2


def test_ignores_non_html_content_type():
    extractor = HtmlExtractor()

    matches = extractor.extract(FIXTURE_HTML, "application/json", "https://example.com/page")

    assert matches == []


def test_matches_include_context():
    extractor = HtmlExtractor(context_chars=10)

    matches = extractor.extract(FIXTURE_HTML, "text/html", "https://example.com/page")
    text_match = next(m for m in matches if m.source_type == SourceType.HTML_TEXT)

    assert text_match.context_before == "ssword is "
    assert text_match.context_after == " for testi"
