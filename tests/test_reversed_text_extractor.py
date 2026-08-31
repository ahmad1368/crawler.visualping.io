from app.extractors.reversed_text import ReversedTextExtractor
from app.models import SourceType

PASSWORD = "VISUALPING{abcdef1234567890}"
URL = "https://example.com/page.html"


def test_finds_password_written_backwards():
    reversed_password = PASSWORD[::-1]
    content = f"<html><!-- {reversed_password} --></html>".encode()
    extractor = ReversedTextExtractor()

    matches = extractor.extract(content, "text/html", URL)

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.REVERSED_TEXT
    assert matches[0].locator.startswith("reversed:offset:")


def test_raw_text_never_contains_the_password_literally():
    reversed_password = PASSWORD[::-1]
    content = f"<html><!-- {reversed_password} --></html>".encode()

    assert PASSWORD not in content.decode()


def test_no_match_in_ordinary_forward_text():
    content = f"<html><!-- {PASSWORD} --></html>".encode()
    extractor = ReversedTextExtractor()

    # The extractor reverses the WHOLE text before searching, so a
    # forward (unreversed) password in the source does not match here --
    # that's HtmlExtractor's job.
    matches = extractor.extract(content, "text/html", URL)

    assert matches == []


def test_ignores_non_text_content_type():
    reversed_password = PASSWORD[::-1]
    extractor = ReversedTextExtractor()

    matches = extractor.extract(reversed_password.encode(), "image/png", URL)

    assert matches == []
