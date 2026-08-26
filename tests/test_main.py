def test_main_registers_both_rest_and_websocket_routes():
    from app.main import app

    paths = {route.path for route in app.routes}

    assert "/crawls" in paths
    assert "/crawls/{crawl_id}/status" in paths
    assert "/ws/crawls/{crawl_id}" in paths
