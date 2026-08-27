"""End-to-end proof that the whole pipeline works together: a real
Orchestrator, with real HttpFetcher/BrowserFetcher/extractors/repository,
crawling a real local HTTP server over Basic Auth, finds every planted
password and nothing else.

Unlike the unit and integration tests elsewhere in this suite (which mock
the layer they don't own), nothing here is mocked except the target site
itself -- a local fixture server standing in for a real one.
"""

import asyncio
import base64
import contextlib
import http.server
import io
import socketserver
import sqlite3
import threading

import httpx
from PIL import Image
from playwright.async_api import async_playwright

from app.crawler.browser_fetcher import BrowserFetcher
from app.crawler.fetcher import HttpFetcher
from app.crawler.frontier import UrlFrontier
from app.crawler.orchestrator import Orchestrator
from app.extractors.base import ExtractorRegistry
from app.extractors.binary_fallback import BinaryFallbackExtractor
from app.extractors.css_js import CssJsExtractor
from app.extractors.headers_cookies import HeaderCookieExtractor
from app.extractors.html import HtmlExtractor
from app.extractors.image_exif import ImageExifExtractor
from app.models import SourceType
from app.storage.sqlite import SqliteRepository

USERNAME = "alice"
PASSWORD = "s3cret"

PASSWORD_HTML_TEXT = "VISUALPING{e1e1e1e1e1e1e1e1}"
PASSWORD_HTML_COMMENT = "VISUALPING{e2e2e2e2e2e2e2e2}"
PASSWORD_CSS = "VISUALPING{e3e3e3e3e3e3e3e3}"
PASSWORD_JS = "VISUALPING{e4e4e4e4e4e4e4e4}"
PASSWORD_HTTP_HEADER = "VISUALPING{e5e5e5e5e5e5e5e5}"
PASSWORD_COOKIE = "VISUALPING{e6e6e6e6e6e6e6e6}"
PASSWORD_IMAGE_METADATA = "VISUALPING{e7e7e7e7e7e7e7e7}"
PASSWORD_BINARY = "VISUALPING{e8e8e8e8e8e8e8e8}"

EXPECTED_SOURCE_TYPES = {
    PASSWORD_HTML_TEXT: {SourceType.HTML_TEXT},
    PASSWORD_HTML_COMMENT: {SourceType.HTML_COMMENT},
    PASSWORD_CSS: {SourceType.CSS},
    PASSWORD_JS: {SourceType.JS},
    PASSWORD_HTTP_HEADER: {SourceType.HTTP_HEADER},
    # Pre-existing, out-of-scope-for-this-fix duplicate, uncovered by
    # switching this assertion from a value->source_type dict (which
    # silently dropped duplicates via last-write-wins) to a
    # value->set-of-source_types one: Set-Cookie is both a raw response
    # header HeaderCookieExtractor scans directly, and the source of the
    # parsed `cookies` dict it scans separately -- so a password planted
    # via Set-Cookie is genuinely found under both source types today.
    PASSWORD_COOKIE: {SourceType.COOKIE, SourceType.HTTP_HEADER},
    # The image's EXIF-embedded password is also literally present in the
    # image's raw file bytes, so BinaryFallbackExtractor's byte-string scan
    # (deliberately not excluded from image/* content -- see
    # app/extractors/binary_fallback.py) finds it too. Both are correct;
    # they collapse into one report row downstream via (source_url, value)
    # grouping, see app/api/routes.py::_build_match_rows.
    PASSWORD_IMAGE_METADATA: {SourceType.IMAGE_METADATA, SourceType.BINARY},
    PASSWORD_BINARY: {SourceType.BINARY},
}

INDEX_HTML = f"""
<!doctype html>
<html>
<body>
  <a href="/style.css">CSS</a>
  <a href="/app.js">JS</a>
  <a href="/photo.jpg">Photo</a>
  <a href="/data.bin">Data</a>
  <p>Welcome! The password is {PASSWORD_HTML_TEXT} for this site.</p>
  <!-- backup credential: {PASSWORD_HTML_COMMENT} -->
</body>
</html>
"""

CSS_BODY = f"""
.hidden::before {{
  content: "{PASSWORD_CSS}";
}}
"""

JS_BODY = f"""
const legacyToken = "{PASSWORD_JS}";
"""

BINARY_BODY = (
    b"\x00\x01\x02\xff\xfe garbage before "
    + PASSWORD_BINARY.encode("ascii")
    + b" garbage after \x80\x81"
)


def _make_jpeg_with_exif_password() -> bytes:
    image = Image.new("RGB", (2, 2), color="white")
    exif = image.getexif()
    exif[37510] = b"ASCII\x00\x00\x00" + PASSWORD_IMAGE_METADATA.encode("ascii")
    buf = io.BytesIO()
    image.save(buf, format="jpeg", exif=exif.tobytes())
    return buf.getvalue()


IMAGE_BODY = _make_jpeg_with_exif_password()

ROUTES = {
    "/": (INDEX_HTML.encode(), "text/html"),
    "/style.css": (CSS_BODY.encode(), "text/css"),
    "/app.js": (JS_BODY.encode(), "application/javascript"),
    "/photo.jpg": (IMAGE_BODY, "image/jpeg"),
    "/data.bin": (BINARY_BODY, "application/octet-stream"),
}


class FixtureSiteHandler(http.server.BaseHTTPRequestHandler):
    def _authorized(self) -> bool:
        expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        return self.headers.get("Authorization") == expected

    def do_GET(self):
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="test"')
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            return

        route = ROUTES.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            return

        body, content_type = route
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if self.path == "/":
            self.send_header("X-Debug-Password", f"leaked={PASSWORD_HTTP_HEADER}")
            self.send_header("Set-Cookie", f"session_backup=token={PASSWORD_COOKIE}")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextlib.contextmanager
def local_fixture_server():
    server = socketserver.TCPServer(("127.0.0.1", 0), FixtureSiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join()


def run(coro):
    return asyncio.run(coro)


def test_full_crawl_finds_every_planted_password_and_nothing_else():
    async def scenario():
        with local_fixture_server() as base_url:
            client = httpx.AsyncClient()
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    registry = ExtractorRegistry()
                    registry.register(HtmlExtractor())
                    registry.register(CssJsExtractor())
                    registry.register(ImageExifExtractor())
                    registry.register(BinaryFallbackExtractor())

                    repository = SqliteRepository(sqlite3.connect(":memory:"))
                    orchestrator = Orchestrator(
                        frontier=UrlFrontier(base_url + "/"),
                        http_fetcher=HttpFetcher(client, USERNAME, PASSWORD),
                        browser_fetcher=BrowserFetcher(browser, USERNAME, PASSWORD),
                        extractor_registry=registry,
                        header_cookie_extractor=HeaderCookieExtractor(),
                        repository=repository,
                    )
                    summary = await orchestrator.run()
                    return summary, repository.get_matches()
                finally:
                    await browser.close()
                    await client.aclose()

    summary, matches = run(scenario())

    found_source_types_by_value: dict[str, set[SourceType]] = {}
    for match in matches:
        found_source_types_by_value.setdefault(match.value, set()).add(match.source_type)

    assert found_source_types_by_value == EXPECTED_SOURCE_TYPES
    assert summary.resources_checked == len(ROUTES)
    assert summary.pages_visited == 1
    assert summary.queue_empty is True
