import base64

from app.extractors.deobfuscation import (
    base64_hex_candidates,
    is_text_like,
    reverse_text,
    rot13_text,
)

PASSWORD = "VISUALPING{abcdef1234567890}"


def test_is_text_like_accepts_html_css_js_json():
    assert is_text_like("text/html")
    assert is_text_like("text/css")
    assert is_text_like("application/javascript")
    assert is_text_like("application/json; charset=utf-8")


def test_is_text_like_rejects_images_and_binary():
    assert not is_text_like("image/png")
    assert not is_text_like("application/octet-stream")


def test_base64_candidates_decodes_standard_alphabet():
    encoded = base64.b64encode(PASSWORD.encode()).decode()
    text = f"const token = '{encoded}';"

    candidates = base64_hex_candidates(text)

    assert any(PASSWORD in candidate for candidate in candidates)


def test_base64_candidates_decodes_urlsafe_alphabet():
    encoded = base64.urlsafe_b64encode(PASSWORD.encode()).decode()
    text = f"const token = '{encoded}';"

    candidates = base64_hex_candidates(text)

    assert any(PASSWORD in candidate for candidate in candidates)


def test_base64_candidates_decodes_hex_stream():
    encoded = PASSWORD.encode().hex()
    text = f"const token = '{encoded}';"

    candidates = base64_hex_candidates(text)

    assert any(PASSWORD in candidate for candidate in candidates)


def test_base64_candidates_ignores_short_tokens():
    # Below the length threshold -- must not attempt a decode at all.
    candidates = base64_hex_candidates("dGVzdA==")  # "test", short base64
    assert candidates == []


def test_base64_candidates_skips_malformed_input_without_raising():
    text = "a" * 40  # base64-alphabet-shaped but not valid padded base64 content
    candidates = base64_hex_candidates(text)
    # Must not raise; whatever comes back (possibly empty, possibly
    # garbage decodes) is fine as long as it doesn't crash.
    assert isinstance(candidates, list)


def test_reverse_text_round_trips():
    assert reverse_text(reverse_text(PASSWORD)) == PASSWORD
    assert PASSWORD not in reverse_text(PASSWORD)


def test_rot13_text_is_self_inverse():
    assert rot13_text(rot13_text(PASSWORD)) == PASSWORD
    assert PASSWORD not in rot13_text(PASSWORD)
