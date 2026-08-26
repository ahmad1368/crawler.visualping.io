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
