from app.matching import KNOWN_EXAMPLE, find_passwords

VALID = "VISUALPING{0123456789abcdef}"


def test_finds_valid_match_with_context():
    content = f"prefix text here {VALID} suffix text here"
    matches = find_passwords(content, before=6, after=6)

    assert len(matches) == 1
    match = matches[0]
    assert match.value == VALID
    assert match.context_before == " here "
    assert match.context_after == " suffi"


def test_rejects_wrong_length_hex_too_short():
    content = "VISUALPING{0123456789abcd}"
    assert find_passwords(content, before=10, after=10) == []


def test_rejects_wrong_length_hex_too_long():
    content = "VISUALPING{0123456789abcdef00}"
    assert find_passwords(content, before=10, after=10) == []


def test_rejects_uppercase_hex():
    content = "VISUALPING{0123456789ABCDEF}"
    assert find_passwords(content, before=10, after=10) == []


def test_rejects_malformed_braces():
    assert find_passwords("VISUALPING[0123456789abcdef]", before=10, after=10) == []
    assert find_passwords("VISUALPING(0123456789abcdef)", before=10, after=10) == []
    assert find_passwords("VISUALPING{0123456789abcdef", before=10, after=10) == []
    assert find_passwords("VISUALPING0123456789abcdef}", before=10, after=10) == []


def test_context_at_start_of_string_does_not_crash():
    content = f"{VALID} trailing text"
    matches = find_passwords(content, before=10, after=5)

    assert matches[0].context_before == ""
    assert matches[0].context_after == " trai"


def test_context_at_end_of_string_does_not_crash():
    content = f"leading text {VALID}"
    matches = find_passwords(content, before=5, after=10)

    assert matches[0].context_before == "text "
    assert matches[0].context_after == ""


def test_handles_multiple_matches_in_one_blob():
    second_value = "VISUALPING{fedcba9876543210}"
    content = f"start {VALID} middle {second_value} end"

    matches = find_passwords(content, before=6, after=6)

    assert len(matches) == 2
    assert matches[0].value == VALID
    assert matches[1].value == second_value
    assert matches[0].end <= matches[1].start


def test_known_example_value_is_never_reported():
    content = f"real: {VALID}, worked example: {KNOWN_EXAMPLE}, also real"

    matches = find_passwords(content, before=10, after=10)

    values = [m.value for m in matches]
    assert KNOWN_EXAMPLE not in values
    assert values == [VALID]


def test_known_example_alone_yields_no_matches():
    assert find_passwords(KNOWN_EXAMPLE, before=10, after=10) == []
