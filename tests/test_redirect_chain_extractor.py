from app.crawler.fetcher import RedirectHop
from app.extractors.redirect_chain import RedirectExtractor
from app.models import SourceType

PASSWORD = "VISUALPING{abcdef1234567890}"
URL = "https://example.com/final"


def test_finds_password_in_a_redirect_location_header():
    history = [
        RedirectHop(
            url="https://example.com/start",
            status_code=302,
            location=f"/next?debug={PASSWORD}",
        )
    ]
    extractor = RedirectExtractor()

    matches = extractor.extract(history, URL)

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.REDIRECT
    assert matches[0].source_url == URL
    assert matches[0].locator == "redirect:0:location"


def test_finds_password_across_multiple_hops():
    history = [
        RedirectHop(url="https://example.com/a", status_code=301, location="/b"),
        RedirectHop(url="https://example.com/b", status_code=302, location=f"/c?x={PASSWORD}"),
    ]
    extractor = RedirectExtractor()

    matches = extractor.extract(history, URL)

    assert len(matches) == 1
    assert matches[0].locator == "redirect:1:location"


def test_no_match_when_no_redirects_occurred():
    extractor = RedirectExtractor()

    matches = extractor.extract([], URL)

    assert matches == []


def test_no_match_when_redirect_has_no_flag():
    history = [RedirectHop(url="https://example.com/a", status_code=301, location="/b")]
    extractor = RedirectExtractor()

    matches = extractor.extract(history, URL)

    assert matches == []


def test_handles_a_hop_with_no_location_header():
    history = [RedirectHop(url="https://example.com/a", status_code=302, location=None)]
    extractor = RedirectExtractor()

    matches = extractor.extract(history, URL)

    assert matches == []
