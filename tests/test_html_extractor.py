from app.extractors.html import HtmlExtractor
from app.models import SourceType

TEXT_PASSWORD = "VISUALPING{abcdef1234567890}"
COMMENT_PASSWORD = "VISUALPING{0123456789abcdef}"
SCRIPT_PASSWORD = "VISUALPING{fedcba9876543210}"
ATTRIBUTE_PASSWORD = "VISUALPING{1111222233334444}"

FIXTURE_HTML = f"""
<html>
<body data-vp-archive="{ATTRIBUTE_PASSWORD}">
<p>Welcome! The password is {TEXT_PASSWORD} for testing.</p>
<!-- backup credential: {COMMENT_PASSWORD} -->
<script>var ignored = "{SCRIPT_PASSWORD}";</script>
</body>
</html>
""".encode()


def test_extracts_password_from_visible_text():
    extractor = HtmlExtractor()

    matches = extractor.extract(FIXTURE_HTML, "text/html", "https://example.com/page")

    text_matches = [m for m in matches if m.value == TEXT_PASSWORD]
    assert len(text_matches) == 1
    assert text_matches[0].source_type == SourceType.HTML_TEXT
    assert text_matches[0].source_url == "https://example.com/page"


def test_extracts_password_from_comment():
    extractor = HtmlExtractor()

    matches = extractor.extract(FIXTURE_HTML, "text/html", "https://example.com/page")

    comment_matches = [m for m in matches if m.source_type == SourceType.HTML_COMMENT]
    assert len(comment_matches) == 1
    assert comment_matches[0].value == COMMENT_PASSWORD


def test_finds_password_hidden_in_a_tag_attribute():
    extractor = HtmlExtractor()

    matches = extractor.extract(FIXTURE_HTML, "text/html", "https://example.com/page")

    attribute_matches = [m for m in matches if m.value == ATTRIBUTE_PASSWORD]
    assert len(attribute_matches) == 1
    assert attribute_matches[0].source_type == SourceType.HTML_TEXT


def test_finds_password_in_inline_script_tagged_as_html_text():
    # Raw-source scanning deliberately covers "any other markup content",
    # per the extractor's own docstring -- inline <script>/<style> content
    # is no longer specially excluded, unlike the old DOM-text-only scan.
    extractor = HtmlExtractor()

    matches = extractor.extract(FIXTURE_HTML, "text/html", "https://example.com/page")

    script_matches = [m for m in matches if m.value == SCRIPT_PASSWORD]
    assert len(script_matches) == 1
    assert script_matches[0].source_type == SourceType.HTML_TEXT


def test_ignores_non_html_content_type():
    extractor = HtmlExtractor()

    matches = extractor.extract(FIXTURE_HTML, "application/json", "https://example.com/page")

    assert matches == []


def test_matches_include_context():
    extractor = HtmlExtractor(context_chars=10)

    matches = extractor.extract(FIXTURE_HTML, "text/html", "https://example.com/page")
    text_match = next(m for m in matches if m.value == TEXT_PASSWORD)

    assert text_match.context_before == "ssword is "
    assert text_match.context_after == " for testi"
