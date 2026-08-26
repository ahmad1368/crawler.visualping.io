from app.extractors.base import Extractor, ExtractorRegistry
from app.models import PasswordMatch, SourceType


class DummyExtractor:
    def __init__(self, matches):
        self._matches = matches
        self.calls = []

    def extract(self, content, content_type, url):
        self.calls.append((content, content_type, url))
        return self._matches


def _match():
    return PasswordMatch(
        value="hunter2",
        source_type=SourceType.HTML,
        source_url="https://example.com",
        context_before="",
        context_after="",
        locator="line:1",
    )


def test_dummy_extractor_satisfies_protocol():
    assert isinstance(DummyExtractor([]), Extractor)


def test_registry_dispatches_to_registered_extractor():
    registry = ExtractorRegistry()
    match = _match()
    dummy = DummyExtractor([match])
    registry.register(dummy)

    results = registry.run_all(b"<html></html>", "text/html", "https://example.com/page")

    assert results == [match]
    assert dummy.calls == [(b"<html></html>", "text/html", "https://example.com/page")]


def test_registry_aggregates_matches_from_multiple_extractors():
    registry = ExtractorRegistry()
    match_a = _match()
    match_b = _match()
    registry.register(DummyExtractor([match_a]))
    registry.register(DummyExtractor([match_b]))

    results = registry.run_all(b"content", "text/plain", "https://example.com")

    assert results == [match_a, match_b]


def test_registry_with_no_extractors_returns_empty_list():
    registry = ExtractorRegistry()

    assert registry.run_all(b"content", "text/plain", "https://example.com") == []
