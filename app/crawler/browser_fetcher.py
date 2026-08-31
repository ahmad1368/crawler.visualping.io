"""Browser-based fetcher (Playwright) for JS-rendered pages.

Renders a page in Chromium and reports three kinds of discovered URLs:

- `dom_links`: `<a href>` elements present in the rendered DOM (post-JS).
- `network_urls`: URLs seen in network traffic during the initial page
  load (fetch/XHR calls, dynamically injected resources a raw-HTML
  parser would miss).
- `interaction_urls`: URLs that only appear after actually clicking a
  non-anchor interactive control (a `<button>`, a `[role=button]`, an
  element with an `onclick` handler) -- e.g. a JS router wired to a
  `<button onclick="...">` instead of a real `<a href>`, or content
  that only renders (and pulls in its own resources) after a click. A
  crawler that only reads the DOM/network log from a single passive
  `goto()` can never reach these; per the challenge brief this fetcher
  is built against, "a real browser can click its way to every page,"
  which means literally clicking, not just parsing what loaded on its
  own.

The browser context is created with `http_credentials` so Basic Auth is
applied to every request automatically -- the credentials themselves are
never logged or included in the returned result.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from playwright.async_api import Browser, Dialog, Request, Response

# Elements matched for click-driven discovery. Real anchors are already
# covered by `dom_links` and deliberately excluded here (`:not(a)`) so
# they aren't clicked a second time. `button[type=submit]`/`input[type=
# submit]` are excluded too -- submitting a form is a mutating action
# (could log the crawl out, send junk data, trigger a side effect on the
# target site), not a safe read-only "reveal more content" interaction,
# so this fetcher never does it.
_CLICK_CANDIDATE_SELECTOR = (
    "button:not([type='submit']):not(a), "
    "[role='button']:not(a):not(button[type='submit']), "
    "[onclick]:not(a):not(button[type='submit'])"
)

# Substring match (case-insensitive) against an element's visible text or
# aria-label. Clicking something that reads like a destructive/mutating
# action is out of scope for a read-only crawler -- skip it rather than
# risk logging the crawl out or changing state on the target site.
_UNSAFE_TEXT_KEYWORDS = (
    "delete",
    "remove",
    "logout",
    "log out",
    "sign out",
    "signout",
    "unsubscribe",
    "deactivate",
    "cancel account",
)

# Hard cap on how many elements get clicked per page. Each click re-loads
# the page in a fresh context, so this bounds how much extra work a single
# page with many interactive controls can add to a crawl.
_DEFAULT_MAX_CLICK_CANDIDATES = 15
_DEFAULT_CLICK_TIMEOUT_MS = 3000


@dataclass
class BrowserFetchResult:
    html: str
    dom_links: list[str]
    network_urls: list[str]
    interaction_urls: list[str] = field(default_factory=list)
    # Client-side storage snapshot (issue #103), captured once per page
    # right after load. A fresh, isolated browser context is created for
    # every page fetch (`_new_authenticated_context()` below) -- there is
    # no persistent cross-page browser session in this design, so this is
    # a per-page snapshot, not a whole-crawl before/after diff.
    cookies: str = ""
    local_storage: dict[str, str] = field(default_factory=dict)
    session_storage: dict[str, str] = field(default_factory=dict)


class BrowserFetcher:
    def __init__(
        self,
        browser: Browser,
        username: str,
        password: str,
        max_click_candidates: int = _DEFAULT_MAX_CLICK_CANDIDATES,
        click_timeout_ms: int = _DEFAULT_CLICK_TIMEOUT_MS,
    ) -> None:
        self._browser = browser
        self._username = username
        self._password = password
        self._max_click_candidates = max_click_candidates
        self._click_timeout_ms = click_timeout_ms

    async def fetch(self, url: str) -> BrowserFetchResult:
        html, dom_links, network_urls, storage = await self._load(url)
        candidate_indices = await self._find_click_candidates(url)
        interaction_urls = await self._discover_via_clicks(url, candidate_indices)

        return BrowserFetchResult(
            html=html,
            dom_links=sorted(set(dom_links)),
            network_urls=sorted(network_urls),
            interaction_urls=sorted(interaction_urls),
            cookies=storage["cookies"],
            local_storage=storage["localStorage"],
            session_storage=storage["sessionStorage"],
        )

    async def _new_authenticated_context(self):
        return await self._browser.new_context(
            http_credentials={"username": self._username, "password": self._password}
        )

    async def _load(self, url: str) -> tuple[str, list[str], set[str], dict]:
        """Passive load: goto + networkidle, same as before click-driven
        discovery existed. Returns (html, dom_links, network_urls,
        storage_snapshot)."""
        context = await self._new_authenticated_context()
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
            storage = await self._read_client_storage(page)
        finally:
            await context.close()

        return html, dom_links, network_urls, storage

    @staticmethod
    async def _read_client_storage(page) -> dict:
        """Snapshot document.cookie/localStorage/sessionStorage right
        after load (issue #103). Degrades to an empty snapshot rather
        than failing the whole page fetch -- storage access can throw in
        edge cases (a sandboxed frame, a site that blocks it outright)."""
        empty = {"cookies": "", "localStorage": {}, "sessionStorage": {}}
        try:
            result = await page.evaluate(
                """() => {
                    const toObj = (storage) => {
                        const obj = {};
                        for (let i = 0; i < storage.length; i++) {
                            const key = storage.key(i);
                            obj[key] = storage.getItem(key);
                        }
                        return obj;
                    };
                    return {
                        cookies: document.cookie,
                        localStorage: toObj(window.localStorage),
                        sessionStorage: toObj(window.sessionStorage),
                    };
                }"""
            )
        except Exception:
            return empty
        return result if isinstance(result, dict) else empty

    async def _find_click_candidates(self, url: str) -> list[int]:
        """Return the indices (into `_CLICK_CANDIDATE_SELECTOR`'s match
        list, in document order) of elements safe and worth clicking:
        visible, and not matching an unsafe-action keyword. Indices --
        not element handles -- because each candidate gets clicked on its
        own fresh page later; an index replays reliably via `.nth(i)`
        against the same selector, an element handle would not."""
        context = await self._new_authenticated_context()
        try:
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle")
            locator = page.locator(_CLICK_CANDIDATE_SELECTOR)
            count = await locator.count()

            indices: list[int] = []
            for i in range(count):
                if len(indices) >= self._max_click_candidates:
                    break
                element = locator.nth(i)
                try:
                    if not await element.is_visible():
                        continue
                    text = ((await element.inner_text()) or "").strip().lower()
                    aria_label = ((await element.get_attribute("aria-label")) or "").lower()
                except Exception:
                    continue
                is_unsafe = any(
                    keyword in text or keyword in aria_label for keyword in _UNSAFE_TEXT_KEYWORDS
                )
                if is_unsafe:
                    continue
                indices.append(i)
            return indices
        finally:
            await context.close()

    async def _discover_via_clicks(self, url: str, candidate_indices: list[int]) -> set[str]:
        discovered: set[str] = set()
        for index in candidate_indices:
            try:
                discovered.update(await self._click_one(url, index))
            except Exception:
                # One control failing to click/navigate (detached element,
                # unexpected dialog, a click that hangs) must not abort
                # discovery for the rest of the page's candidates.
                continue
        return discovered

    async def _click_one(self, url: str, index: int) -> set[str]:
        """Reload the page fresh, click just the `index`-th candidate,
        and report every URL that appeared as a result: new network
        requests, new `a[href]` links revealed in the DOM, and the page's
        own URL if the click caused a top-level navigation."""
        context = await self._new_authenticated_context()
        network_urls: set[str] = set()

        def record_request(request: Request) -> None:
            network_urls.add(request.url)

        def record_response(response: Response) -> None:
            network_urls.add(response.url)

        def dismiss_dialog(dialog: Dialog) -> None:
            # A click-triggered alert()/confirm()/prompt() would otherwise
            # block the page indefinitely -- dismiss it and move on rather
            # than let one candidate hang the whole crawl.
            asyncio.ensure_future(dialog.dismiss())

        try:
            page = await context.new_page()
            page.on("request", record_request)
            page.on("response", record_response)
            page.on("dialog", dismiss_dialog)

            await page.goto(url, wait_until="networkidle")
            links_before = set(
                await page.eval_on_selector_all("a[href]", "elements => elements.map(e => e.href)")
            )

            await page.locator(_CLICK_CANDIDATE_SELECTOR).nth(index).click(
                timeout=self._click_timeout_ms
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=self._click_timeout_ms)
            except Exception:
                # Not every click triggers network activity (e.g. a
                # purely client-side DOM reveal) -- that's not a failure.
                pass

            links_after = set(
                await page.eval_on_selector_all("a[href]", "elements => elements.map(e => e.href)")
            )

            found = network_urls | (links_after - links_before)
            if page.url != url:
                found.add(page.url)
            return found
        finally:
            await context.close()
