import codecs

from app.extractors.rot13 import Rot13Extractor
from app.models import SourceType

PASSWORD = "VISUALPING{abcdef1234567890}"
URL = "https://example.com/page.html"


def test_finds_password_encoded_with_rot13():
    rot13_password = codecs.encode(PASSWORD, "rot13")
    content = f"<html><!-- {rot13_password} --></html>".encode()
    extractor = Rot13Extractor()

    matches = extractor.extract(content, "text/html", URL)

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.ROT13
    assert matches[0].locator.startswith("rot13:offset:")


def test_raw_text_never_contains_the_password_literally():
    rot13_password = codecs.encode(PASSWORD, "rot13")
    content = f"<html><!-- {rot13_password} --></html>".encode()

    assert PASSWORD not in content.decode()


def test_no_match_in_ordinary_plain_text():
    content = f"<html><!-- {PASSWORD} --></html>".encode()
    extractor = Rot13Extractor()

    matches = extractor.extract(content, "text/html", URL)

    assert matches == []


def test_ignores_non_text_content_type():
    rot13_password = codecs.encode(PASSWORD, "rot13")
    extractor = Rot13Extractor()

    matches = extractor.extract(rot13_password.encode(), "image/png", URL)

    assert matches == []
