from app.extractors.client_storage import ClientStorageExtractor
from app.models import SourceType

PASSWORD = "VISUALPING{abcdef1234567890}"
URL = "https://example.com/"


def test_finds_password_in_document_cookie():
    extractor = ClientStorageExtractor()

    matches = extractor.extract(f"session=abc; debug={PASSWORD}", {}, {}, URL)

    assert len(matches) == 1
    assert matches[0].value == PASSWORD
    assert matches[0].source_type == SourceType.CLIENT_STORAGE
    assert matches[0].locator == "client-storage:cookie"


def test_finds_password_in_local_storage_value():
    extractor = ClientStorageExtractor()

    matches = extractor.extract("", {"debugFlag": PASSWORD}, {}, URL)

    assert len(matches) == 1
    assert matches[0].locator == "client-storage:localStorage:debugFlag"


def test_finds_password_in_session_storage_value():
    extractor = ClientStorageExtractor()

    matches = extractor.extract("", {}, {"temp": PASSWORD}, URL)

    assert len(matches) == 1
    assert matches[0].locator == "client-storage:sessionStorage:temp"


def test_finds_passwords_across_all_three_stores_at_once():
    extractor = ClientStorageExtractor()

    matches = extractor.extract(
        f"a={PASSWORD}",
        {"x": PASSWORD},
        {"y": PASSWORD},
        URL,
    )

    assert len(matches) == 3
    assert {m.locator for m in matches} == {
        "client-storage:cookie",
        "client-storage:localStorage:x",
        "client-storage:sessionStorage:y",
    }


def test_no_match_on_empty_storage():
    extractor = ClientStorageExtractor()

    matches = extractor.extract("", {}, {}, URL)

    assert matches == []


def test_no_match_when_storage_has_no_flag():
    extractor = ClientStorageExtractor()

    matches = extractor.extract("session=abc123", {"theme": "dark"}, {"nonce": "xyz"}, URL)

    assert matches == []
