from app.extractors.js_charcode import JsCharCodeExtractor
from app.models import SourceType

ARRAY_PASSWORD = "VISUALPING{0011223344556677}"
FROM_CHARCODE_PASSWORD = "VISUALPING{8899aabbccddeeff}"


def _array_literal(text: str) -> str:
    return "[" + ", ".join(str(ord(c)) for c in text) + "]"


def _fromcharcode_call(text: str) -> str:
    return "String.fromCharCode(" + ", ".join(str(ord(c)) for c in text) + ")"


JS_FIXTURE = f"""
const codes = {_array_literal(ARRAY_PASSWORD)};
const secret = codes.map(c => String.fromCharCode(c)).join("");

function reveal() {{
  return {_fromcharcode_call(FROM_CHARCODE_PASSWORD)};
}}
""".encode()


def test_raw_text_never_contains_the_password_literally():
    # Sanity check the premise: only digits/commas are present in the
    # source, so a plain regex over the raw text can't find either
    # password -- decoding is required.
    text = JS_FIXTURE.decode()
    assert ARRAY_PASSWORD not in text
    assert FROM_CHARCODE_PASSWORD not in text


def test_decodes_password_from_array_literal_of_char_codes():
    extractor = JsCharCodeExtractor()

    matches = extractor.extract(JS_FIXTURE, "application/javascript", "https://example.com/app.js")

    values = {m.value for m in matches}
    assert ARRAY_PASSWORD in values
    array_match = next(m for m in matches if m.value == ARRAY_PASSWORD)
    assert array_match.source_type == SourceType.JS_CHARCODE
    assert array_match.source_url == "https://example.com/app.js"


def test_decodes_password_from_string_fromcharcode_call():
    extractor = JsCharCodeExtractor()

    matches = extractor.extract(
        JS_FIXTURE, "text/javascript; charset=utf-8", "https://example.com/app.js"
    )

    values = {m.value for m in matches}
    assert FROM_CHARCODE_PASSWORD in values


def test_ignores_short_numeric_arrays():
    extractor = JsCharCodeExtractor()

    matches = extractor.extract(
        b"const rgb = [255, 255, 255];", "application/javascript", "https://example.com/x.js"
    )

    assert matches == []


def test_ignores_unrelated_content_type():
    extractor = JsCharCodeExtractor()

    matches = extractor.extract(JS_FIXTURE, "text/html", "https://example.com/app.js")

    assert matches == []


def test_locator_points_at_the_literal_start_in_source():
    extractor = JsCharCodeExtractor()

    matches = extractor.extract(JS_FIXTURE, "application/javascript", "https://example.com/app.js")

    array_match = next(m for m in matches if m.value == ARRAY_PASSWORD)
    assert array_match.locator.startswith("line:2,col:")
