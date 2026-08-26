"""Browser-based fetcher (Playwright) for JS-rendered pages.

Renders a page in Chromium and reports both the links present in the
rendered DOM and the URLs seen in network traffic during load (fetch/XHR
calls, dynamically injected resources a raw-HTML parser would miss).

The browser context is created with `http_credentials` so Basic Auth is
applied to every request automatically -- the credentials themselves are
never logged or included in the returned result.
"""

from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Browser, Request, Response


@dataclass
class BrowserFetchResult:
    html: str
    dom_links: list[str]
    network_urls: list[str]


class BrowserFetcher:
    def __init__(self, browser: Browser, username: str, password: str) -> None:
        self._browser = browser
        self._username = username
        self._password = password

    async def fetch(self, url: str) -> BrowserFetchResult:
        context = await self._browser.new_context(
            http_credentials={"username": self._username, "password": self._password}
        )
        network_urls: set[str] = set()

        def record_request(request: Request) -> None:
            network_urls.add(request.url)

        def record_response(response: Response) -> None:
            network_urls.add(response.url)

        try:
            page = await context.new_page()
            page.on("request", record_request)
            page.on("response", record_response)

            await page.goto(url, wait_until="networkidle")
            html = await page.content()
            dom_links = await page.eval_on_selector_all(
                "a[href]", "elements => elements.map(e => e.href)"
            )
        finally:
            await context.close()

        return BrowserFetchResult(
            html=html,
            dom_links=sorted(set(dom_links)),
            network_urls=sorted(network_urls),
        )
