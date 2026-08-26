from app.crawler.pagination_guard import PaginationGuard, pagination_family_key


def test_pagination_family_key_matches_single_numeric_param():
    assert pagination_family_key("https://example.com/report?page=7") == "/report?page"


def test_pagination_family_key_ignores_non_numeric_value():
    assert pagination_family_key("https://example.com/report?page=abc") is None


def test_pagination_family_key_ignores_multiple_params():
    assert pagination_family_key("https://example.com/report?page=1&sort=asc") is None


def test_pagination_family_key_ignores_no_query_string():
    assert pagination_family_key("https://example.com/report") is None


def test_guard_does_not_stop_before_reaching_the_limit():
    guard = PaginationGuard(max_unproductive=3)

    for i in range(2):
        guard.record(f"https://example.com/report?page={i}", new_links=0, new_matches=0)

    assert guard.is_stopped("https://example.com/report?page=99") is False


def test_guard_stops_family_after_consecutive_unproductive_pages():
    guard = PaginationGuard(max_unproductive=3)

    for i in range(3):
        guard.record(f"https://example.com/report?page={i}", new_links=0, new_matches=0)

    assert guard.is_stopped("https://example.com/report?page=999") is True


def test_guard_resets_streak_on_a_productive_page():
    guard = PaginationGuard(max_unproductive=3)

    guard.record("https://example.com/report?page=0", new_links=0, new_matches=0)
    guard.record("https://example.com/report?page=1", new_links=0, new_matches=0)
    guard.record("https://example.com/report?page=2", new_links=1, new_matches=0)
    guard.record("https://example.com/report?page=3", new_links=0, new_matches=0)
    guard.record("https://example.com/report?page=4", new_links=0, new_matches=0)

    assert guard.is_stopped("https://example.com/report?page=999") is False


def test_guard_tracks_families_independently():
    guard = PaginationGuard(max_unproductive=2)

    guard.record("https://example.com/report?page=1", new_links=0, new_matches=0)
    guard.record("https://example.com/report?page=2", new_links=0, new_matches=0)
    guard.record("https://example.com/archive?page=1", new_links=1, new_matches=0)

    assert guard.is_stopped("https://example.com/report?page=3") is True
    assert guard.is_stopped("https://example.com/archive?page=2") is False


def test_guard_ignores_urls_with_no_pagination_shape():
    guard = PaginationGuard(max_unproductive=1)

    guard.record("https://example.com/about", new_links=0, new_matches=0)

    assert guard.is_stopped("https://example.com/about") is False
