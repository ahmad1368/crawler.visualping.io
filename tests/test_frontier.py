from app.crawler.frontier import UrlFrontier, normalize_url


def test_normalize_strips_fragment():
    assert normalize_url("https://example.com/page#section") == "https://example.com/page"


def test_normalize_consistent_trailing_slash():
    assert normalize_url("https://example.com/page/") == normalize_url("https://example.com/page")


def test_normalize_root_path_keeps_single_slash():
    assert normalize_url("https://example.com") == "https://example.com/"
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_normalize_lowercases_scheme_and_host():
    assert normalize_url("HTTPS://Example.COM/Page") == "https://example.com/Page"


def test_seed_url_is_queued_on_init():
    frontier = UrlFrontier("https://example.com/")

    assert len(frontier) == 1
    assert frontier.next() == "https://example.com/"


def test_add_dedupes_normalized_duplicates():
    frontier = UrlFrontier("https://example.com/")
    frontier.next()

    added_first = frontier.add("https://example.com/page")
    added_again_with_fragment = frontier.add("https://example.com/page#top")
    added_again_with_slash = frontier.add("https://example.com/page/")

    assert added_first is True
    assert added_again_with_fragment is False
    assert added_again_with_slash is False
    assert len(frontier) == 1


def test_add_rejects_external_origin():
    frontier = UrlFrontier("https://example.com/")

    added_different_host = frontier.add("https://other.com/page")
    added_different_scheme = frontier.add("http://example.com/page")

    assert added_different_host is False
    assert added_different_scheme is False
    assert len(frontier) == 1


def test_add_many_returns_count_of_newly_queued():
    frontier = UrlFrontier("https://example.com/")
    frontier.next()

    added_count = frontier.add_many(
        [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/a",
            "https://other.com/c",
        ]
    )

    assert added_count == 2
    assert len(frontier) == 2


def test_cycle_safety_no_infinite_requeue():
    frontier = UrlFrontier("https://example.com/")

    visited = []
    for _ in range(5):
        if not frontier.has_next():
            break
        url = frontier.next()
        visited.append(url)
        frontier.add_many([url, "https://example.com/"])

    assert visited == ["https://example.com/"]
    assert not frontier.has_next()


def test_queue_is_fifo():
    frontier = UrlFrontier("https://example.com/")
    frontier.next()
    frontier.add_many(["https://example.com/first", "https://example.com/second"])

    assert frontier.next() == "https://example.com/first"
    assert frontier.next() == "https://example.com/second"


def test_mark_visited_prevents_future_add():
    frontier = UrlFrontier("https://example.com/")
    frontier.next()

    frontier.mark_visited("https://example.com/already-done")
    added = frontier.add("https://example.com/already-done")

    assert added is False
    assert len(frontier) == 0


def test_mark_visited_normalizes_before_marking():
    frontier = UrlFrontier("https://example.com/")
    frontier.next()

    frontier.mark_visited("https://example.com/page/")
    added = frontier.add("https://example.com/page#fragment")

    assert added is False


def test_normalize_strips_allowlisted_decorative_query_params():
    assert normalize_url("https://example.com/page?ref=email") == normalize_url(
        "https://example.com/page"
    )
    assert normalize_url("https://example.com/page?utm_source=x") == normalize_url(
        "https://example.com/page"
    )
    assert normalize_url("https://example.com/page?v=2&hl=en") == normalize_url(
        "https://example.com/page"
    )


def test_normalize_keeps_non_allowlisted_query_params():
    assert normalize_url("https://example.com/page?id=5") == "https://example.com/page?id=5"


def test_normalize_keeps_unrecognized_params_while_dropping_allowlisted_ones():
    normalized = normalize_url("https://example.com/page?id=5&ref=email")
    assert "id=5" in normalized
    assert "ref" not in normalized


def test_normalize_does_not_collapse_pagination_style_params():
    # `page` (and any other param not explicitly allowlisted) must stay
    # significant -- real pagination is distinct content per value, unlike
    # ref/utm_source/v/hl.
    assert normalize_url("https://example.com/report?page=1") != normalize_url(
        "https://example.com/report?page=2"
    )
    assert normalize_url("https://example.com/report?page=1") != normalize_url(
        "https://example.com/report"
    )


def test_normalize_does_not_collapse_formerly_denylisted_params():
    # These were stripped by the old denylist approach; the new allowlist
    # only strips {ref, utm_source, v, hl} and must not guess about others.
    assert normalize_url("https://example.com/page?utm_campaign=x") != normalize_url(
        "https://example.com/page"
    )
    assert normalize_url("https://example.com/page?fbclid=x") != normalize_url(
        "https://example.com/page"
    )


def test_add_dedupes_urls_differing_only_by_allowlisted_query_params():
    frontier = UrlFrontier("https://example.com/")
    frontier.next()

    added_first = frontier.add("https://example.com/page?ref=email")
    added_second = frontier.add("https://example.com/page?utm_source=newsletter&hl=en")

    assert added_first is True
    assert added_second is False
    assert len(frontier) == 1


def test_add_treats_pagination_query_param_values_as_distinct_pages():
    frontier = UrlFrontier("https://example.com/")
    frontier.next()

    added_first = frontier.add("https://example.com/report?page=1")
    added_second = frontier.add("https://example.com/report?page=2")

    assert added_first is True
    assert added_second is True
    assert len(frontier) == 2
