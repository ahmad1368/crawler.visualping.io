from app.crawler.pagination_guard import PaginationGuard, pagination_family_key


def test_pagination_family_key_matches_single_numeric_param():
    assert pagination_family_key("https://example.com/report?page=7") == "/report?page"


def test_pagination_family_key_ignores_non_numeric_value():
    assert pagination_family_key("https://example.com/report?page=abc") is None


def test_pagination_family_key_ignores_multiple_params():
    assert pagination_family_key("https://example.com/report?page=1&sort=asc") is None


def test_pagination_family_key_ignores_no_query_string():
    assert pagination_family_key("https://example.com/report") is None


def test_pagination_family_key_unifies_a_trailing_slash_mismatch():
    """Regression test, this session: a real target's own pager links
    used `/report/?page=N` (trailing slash) while the crawler fetches
    (and thus family-keys) the same page as `/report?page=N` (no
    trailing slash, per UrlFrontier.normalize_url()). The two spellings
    must collapse to the same family key -- see pagination_guard.py's
    module docstring for the full real-target trap this let through."""
    assert pagination_family_key(
        "https://example.com/report?page=7"
    ) == pagination_family_key("https://example.com/report/?page=7")


def test_guard_does_not_stop_before_reaching_the_limit():
    guard = PaginationGuard(max_unproductive=3, max_family_pages=None)

    for i in range(2):
        guard.record(f"https://example.com/report?page={i}", new_matches=0)

    assert guard.is_stopped("https://example.com/report?page=99") is False


def test_guard_stops_family_after_consecutive_unproductive_pages():
    guard = PaginationGuard(max_unproductive=3, max_family_pages=None)

    for i in range(3):
        guard.record(f"https://example.com/report?page={i}", new_matches=0)

    assert guard.is_stopped("https://example.com/report?page=999") is True


def test_guard_resets_streak_on_a_page_with_a_new_match():
    guard = PaginationGuard(max_unproductive=3, max_family_pages=None)

    guard.record("https://example.com/report?page=0", new_matches=0)
    guard.record("https://example.com/report?page=1", new_matches=0)
    guard.record("https://example.com/report?page=2", new_matches=1)
    guard.record("https://example.com/report?page=3", new_matches=0)
    guard.record("https://example.com/report?page=4", new_matches=0)

    assert guard.is_stopped("https://example.com/report?page=999") is False


def test_guard_tracks_families_independently():
    guard = PaginationGuard(max_unproductive=2, max_family_pages=None)

    guard.record("https://example.com/report?page=1", new_matches=0)
    guard.record("https://example.com/report?page=2", new_matches=0)
    guard.record("https://example.com/archive?page=1", new_matches=1)

    assert guard.is_stopped("https://example.com/report?page=3") is True
    assert guard.is_stopped("https://example.com/archive?page=2") is False


def test_guard_ignores_urls_with_no_pagination_shape():
    guard = PaginationGuard(max_unproductive=1, max_family_pages=None)

    guard.record("https://example.com/about", new_matches=0)

    assert guard.is_stopped("https://example.com/about") is False


def test_guard_is_not_defeated_by_a_family_that_always_reports_zero_matches_but_never_zero_links():
    """Regression test for issue #78: the streak used to reset on
    new_links too, and ordinary sequential pagination always discovers a
    "new" next-page link the first time it's seen -- a trap serving
    randomized-but-password-free content on every page exploited exactly
    this to look "productive" forever. record() no longer takes a
    new_links argument at all; passing only new_matches=0 every time
    (regardless of how much "new" content/links a page appeared to have)
    must still trip the unproductive streak."""
    guard = PaginationGuard(max_unproductive=5, max_family_pages=None)

    for i in range(5):
        guard.record(f"https://example.com/report?page={i}", new_matches=0)

    assert guard.is_stopped("https://example.com/report?page=999") is True


def test_guard_hard_ceiling_stops_a_family_that_keeps_finding_matches():
    """The unconditional backstop: even a family that never goes
    unproductive (a match on every single page, so the streak logic alone
    would let it run forever) is still capped at max_family_pages total
    pages."""
    guard = PaginationGuard(max_unproductive=1000, max_family_pages=10)

    for i in range(10):
        guard.record(f"https://example.com/report?page={i}", new_matches=1)

    assert guard.is_stopped("https://example.com/report?page=999") is True


def test_guard_hard_ceiling_does_not_trip_before_reached():
    guard = PaginationGuard(max_unproductive=1000, max_family_pages=10)

    for i in range(9):
        guard.record(f"https://example.com/report?page={i}", new_matches=1)

    assert guard.is_stopped("https://example.com/report?page=999") is False


def test_guard_hard_ceiling_disabled_when_none():
    guard = PaginationGuard(max_unproductive=1000, max_family_pages=None)

    for i in range(200):
        guard.record(f"https://example.com/report?page={i}", new_matches=1)

    assert guard.is_stopped("https://example.com/report?page=999") is False


def test_guard_hard_ceiling_is_on_by_default():
    guard = PaginationGuard(max_unproductive=1000)

    for i in range(200):
        guard.record(f"https://example.com/report?page={i}", new_matches=1)

    assert guard.is_stopped("https://example.com/report?page=999") is True


def test_guard_resets_streak_on_a_page_with_a_new_external_link():
    """Regression test for issue #88: a real target's crawl coverage
    dropped from ~680 to ~480 pages because an index/listing family that
    never itself contains a password (only links out to individual
    content pages that do) was wrongly treated as unproductive by
    new_matches alone. A new_external_links signal must independently
    keep the streak from tripping."""
    guard = PaginationGuard(max_unproductive=3, max_family_pages=None)

    guard.record("https://example.com/report?page=0", new_matches=0, new_external_links=1)
    guard.record("https://example.com/report?page=1", new_matches=0, new_external_links=1)
    guard.record("https://example.com/report?page=2", new_matches=0, new_external_links=1)
    guard.record("https://example.com/report?page=3", new_matches=0, new_external_links=1)

    assert guard.is_stopped("https://example.com/report?page=999") is False


def test_guard_still_stops_when_neither_matches_nor_external_links_are_new():
    """A family with a same-family-only link every page (e.g. just the
    next page in the chain, never anything else) must still trip the
    streak -- new_external_links=0 provides no productivity signal on
    its own, same as new_matches=0."""
    guard = PaginationGuard(max_unproductive=3, max_family_pages=None)

    for i in range(3):
        guard.record(f"https://example.com/report?page={i}", new_matches=0, new_external_links=0)

    assert guard.is_stopped("https://example.com/report?page=999") is True
