from app.crawler.asset_scanner import find_static_asset_references


def test_finds_asset_referenced_via_script_src():
    html = '<script src="/static/js/app.js"></script>'
    assert find_static_asset_references(html) == {"/static/js/app.js"}


def test_finds_asset_referenced_via_link_href():
    html = '<link rel="stylesheet" href="/static/css/main.css">'
    assert find_static_asset_references(html) == {"/static/css/main.css"}


def test_finds_asset_referenced_via_img_source_and_embed():
    html = """
    <img src="/static/img/logo.png">
    <source src="/static/media/clip.mp4">
    <embed src="/static/plugins/widget.swf">
    """
    assert find_static_asset_references(html) == {
        "/static/img/logo.png",
        "/static/media/clip.mp4",
        "/static/plugins/widget.swf",
    }


def test_finds_asset_referenced_only_as_inline_js_string_via_fallback_regex():
    """The whole point of the fallback pattern (issue #99): a reference
    that never appears as a real tag attribute -- built up or used
    directly in a JS string -- still gets caught."""
    js = "fetch('/static/data/report.json?v=3').then(r => r.json());"
    assert find_static_asset_references(js) == {"/static/data/report.json?v=3"}


def test_ignores_non_static_paths():
    html = '<script src="/app/main.js"></script><a href="/static-ish/x">no</a>'
    assert find_static_asset_references(html) == set()


def test_dedupes_the_same_reference_found_by_both_patterns():
    html = '<script src="/static/js/app.js"></script>'
    # Same URL is matched by both the tag-attribute pattern and the
    # fallback regex -- must not appear twice.
    assert find_static_asset_references(html) == {"/static/js/app.js"}


def test_query_string_is_captured():
    html = '<link href="/static/css/main.css?v=1.2&build=abc">'
    assert find_static_asset_references(html) == {"/static/css/main.css?v=1.2&build=abc"}
