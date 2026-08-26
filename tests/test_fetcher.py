import asyncio

import httpx

from app.crawler.fetcher import HttpFetcher, TransientFetchError


def run(coro):
    return asyncio.run(coro)


def test_fetch_sends_basic_auth_header_and_returns_result():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            content=b"hello",
            headers={"content-type": "text/plain"},
        )

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            fetcher = HttpFetcher(client, username="alice", password="s3cret")
            return await fetcher.fetch("https://example.com/page")
        finally:
            await client.aclose()

    result = run(scenario())

    assert captured["authorization"] is not None
    assert captured["authorization"].startswith("Basic ")
    assert result.content == b"hello"
    assert result.content_type == "text/plain"
    assert result.status_code == 200


def test_fetch_returns_headers_and_cookies():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"ok",
            headers={"content-type": "text/css", "set-cookie": "session=abc123"},
        )

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            fetcher = HttpFetcher(client, username="alice", password="s3cret")
            return await fetcher.fetch("https://example.com/style.css")
        finally:
            await client.aclose()

    result = run(scenario())

    assert result.headers["content-type"] == "text/css"
    assert result.cookies["session"] == "abc123"


def test_fetch_retries_on_5xx_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(503, content=b"")
        return httpx.Response(200, content=b"recovered")

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            fetcher = HttpFetcher(
                client,
                username="alice",
                password="s3cret",
                max_retries=3,
                backoff_factor=0.001,
            )
            return await fetcher.fetch("https://example.com/flaky")
        finally:
            await client.aclose()

    result = run(scenario())

    assert calls["count"] == 3
    assert result.content == b"recovered"


def test_fetch_raises_after_exhausting_retries_on_persistent_5xx():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(500, content=b"")

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            fetcher = HttpFetcher(
                client,
                username="alice",
                password="s3cret",
                max_retries=3,
                backoff_factor=0.001,
            )
            await fetcher.fetch("https://example.com/down")
        finally:
            await client.aclose()

    try:
        run(scenario())
        assert False, "expected TransientFetchError"
    except TransientFetchError:
        pass

    assert calls["count"] == 3


def test_fetch_retries_on_timeout_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 2:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return httpx.Response(200, content=b"back up")

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            fetcher = HttpFetcher(
                client,
                username="alice",
                password="s3cret",
                max_retries=3,
                backoff_factor=0.001,
            )
            return await fetcher.fetch("https://example.com/slow")
        finally:
            await client.aclose()

    result = run(scenario())

    assert calls["count"] == 2
    assert result.content == b"back up"


def test_fetch_raises_after_exhausting_retries_on_persistent_timeout():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ReadTimeout("simulated timeout", request=request)

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            fetcher = HttpFetcher(
                client,
                username="alice",
                password="s3cret",
                max_retries=2,
                backoff_factor=0.001,
            )
            await fetcher.fetch("https://example.com/unreachable")
        finally:
            await client.aclose()

    try:
        run(scenario())
        assert False, "expected TransientFetchError"
    except TransientFetchError:
        pass

    assert calls["count"] == 2


def test_fetch_follows_a_redirect_on_a_clean_path():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/docs":
            return httpx.Response(301, headers={"location": "/docs/index"})
        return httpx.Response(200, content=b"real docs content")

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            fetcher = HttpFetcher(client, username="alice", password="s3cret")
            return await fetcher.fetch("https://example.com/docs")
        finally:
            await client.aclose()

    result = run(scenario())

    assert result.content == b"real docs content"
    assert result.status_code == 200


def test_fetch_follows_a_redirect_on_a_url_with_a_query_string():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/report" and request.url.params.get("page") == "1":
            return httpx.Response(301, headers={"location": "/report/1"})
        return httpx.Response(200, content=b"report page 1 content")

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            fetcher = HttpFetcher(client, username="alice", password="s3cret")
            return await fetcher.fetch("https://example.com/report?page=1")
        finally:
            await client.aclose()

    result = run(scenario())

    assert result.content == b"report page 1 content"
    assert result.status_code == 200


def test_fetch_raises_transient_error_on_redirect_loop_without_hanging():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        target = "/b" if request.url.path == "/a" else "/a"
        return httpx.Response(302, headers={"location": target})

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            fetcher = HttpFetcher(
                client,
                username="alice",
                password="s3cret",
                max_retries=1,
                backoff_factor=0.001,
            )
            await fetcher.fetch("https://example.com/a")
        finally:
            await client.aclose()

    try:
        run(asyncio.wait_for(scenario(), timeout=5))
        assert False, "expected TransientFetchError"
    except TransientFetchError:
        pass

    assert calls["count"] > 0


def test_fetch_reuses_a_session_cookie_across_requests():
    # One AsyncClient is created per crawl (app/api/routes.py) and reused for
    # every HttpFetcher.fetch() call -- issue #63 item 7 asks us to confirm
    # httpx's own cookie jar on that shared client carries a session cookie
    # set on an early request into the Cookie header of later ones, since a
    # site gating real content behind a session (e.g. after a login-style
    # redirect) would otherwise look logged-out on every request after the
    # first.
    received_cookie = {"value": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(
                200, content=b"logged in", headers={"set-cookie": "session=abc123"}
            )
        received_cookie["value"] = request.headers.get("cookie")
        return httpx.Response(200, content=b"secret content")

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            fetcher = HttpFetcher(client, username="alice", password="s3cret")
            await fetcher.fetch("https://example.com/login")
            return await fetcher.fetch("https://example.com/dashboard")
        finally:
            await client.aclose()

    result = run(scenario())

    assert received_cookie["value"] == "session=abc123"
    assert result.content == b"secret content"
