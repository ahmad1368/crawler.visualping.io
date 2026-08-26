import asyncio
import base64
import contextlib
import http.server
import socketserver
import threading

from playwright.async_api import async_playwright

from app.crawler.browser_fetcher import BrowserFetcher

VALID_USERNAME = "alice"
VALID_PASSWORD = "s3cret"

INDEX_HTML = """
<!doctype html>
<html>
<body>
  <a href="/static-page">Static Link</a>
  <script>
    fetch('/api/data');
    const a = document.createElement('a');
    a.href = '/dynamic-page';
    a.textContent = 'Dynamic Link';
    document.body.appendChild(a);
  </script>
</body>
</html>
"""


class BasicAuthHandler(http.server.BaseHTTPRequestHandler):
    def _authorized(self) -> bool:
        expected = (
            "Basic " + base64.b64encode(f"{VALID_USERNAME}:{VALID_PASSWORD}".encode()).decode()
        )
        return self.headers.get("Authorization") == expected

    def do_GET(self):
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="test"')
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            return

        if self.path == "/":
            body = INDEX_HTML.encode()
            content_type = "text/html"
        elif self.path == "/api/data":
            body = b'{"ok": true}'
            content_type = "application/json"
        else:
            body = b"ok"
            content_type = "text/plain"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextlib.contextmanager
def local_auth_server():
    server = socketserver.TCPServer(("127.0.0.1", 0), BasicAuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join()


def run(coro):
    return asyncio.run(coro)


def test_fetch_extracts_dom_links_and_network_urls():
    async def scenario():
        with local_auth_server() as port:
            base_url = f"http://127.0.0.1:{port}"
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                try:
                    fetcher = BrowserFetcher(
                        browser, username=VALID_USERNAME, password=VALID_PASSWORD
                    )
                    return await fetcher.fetch(base_url + "/")
                finally:
                    await browser.close()

    result = run(scenario())

    assert "Static Link" in result.html
    assert any(link.endswith("/static-page") for link in result.dom_links)
    assert any(link.endswith("/dynamic-page") for link in result.dom_links)
    assert any(url.endswith("/api/data") for url in result.network_urls)


def test_fetch_applies_basic_auth_credentials_to_context():
    async def scenario():
        with local_auth_server() as port:
            base_url = f"http://127.0.0.1:{port}"
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                try:
                    fetcher = BrowserFetcher(
                        browser, username=VALID_USERNAME, password="wrong-password"
                    )
                    return await fetcher.fetch(base_url + "/")
                finally:
                    await browser.close()

    result = run(scenario())

    assert "Unauthorized" in result.html
