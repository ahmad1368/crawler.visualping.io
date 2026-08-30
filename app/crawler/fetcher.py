"""Async HTTP fetcher for static resources, with Basic Auth and retry/backoff.

The `httpx.AsyncClient` is injected via the constructor so tests can supply
a mock transport instead of hitting the network. Never log request headers
or the constructor's credentials -- the Authorization header carries the
target site's Basic Auth secret on every request this fetcher sends.

Follows redirects (`follow_redirects=True`): a real crawl needs the content
a 301/302 actually points at, not just the redirect response itself --
including redirects on URLs carrying a query string, which httpx's own
URL-resolution handles correctly. A redirect loop makes httpx raise
`TooManyRedirects` after its own internal hop limit, which is retried like
any other transient failure and eventually surfaces as
`TransientFetchError` -- never an unbounded hang.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx


@dataclass
class RedirectHop:
    """One intermediate response in a redirect chain -- issue #103.
    `follow_redirects=True` (below) means only the *final* response used
    to reach content; every hop's own Location header and status would
    otherwise be silently discarded."""

    url: str
    status_code: int
    location: str | None


@dataclass
class FetchResult:
    content: bytes
    content_type: str | None
    status_code: int
    headers: dict[str, str]
    cookies: dict[str, str]
    redirect_history: list[RedirectHop] = field(default_factory=list)


class TransientFetchError(Exception):
    """Raised when a fetch fails after exhausting all retries."""


class HttpFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        username: str,
        password: str,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self._client = client
        self._auth = httpx.BasicAuth(username, password)
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor

    async def fetch(self, url: str, extra_headers: dict[str, str] | None = None) -> FetchResult:
        """`extra_headers` (issue #103) lets a caller re-request the same
        URL with alternate negotiation headers (`Accept`,
        `X-Requested-With`, ...) -- see `app/crawler/content_negotiation.py`."""
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                response = await self._client.get(
                    url,
                    auth=self._auth,
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers=extra_headers,
                )
            except httpx.RequestError as exc:
                last_error = exc
            else:
                if response.status_code < 500:
                    return self._to_result(response)
                last_error = TransientFetchError(
                    f"server returned {response.status_code} for {url}"
                )

            if attempt < self._max_retries - 1:
                await asyncio.sleep(self._backoff_factor * (2**attempt))

        raise TransientFetchError(
            f"failed to fetch {url} after {self._max_retries} attempts"
        ) from last_error

    @staticmethod
    def _to_result(response: httpx.Response) -> FetchResult:
        return FetchResult(
            content=response.content,
            content_type=response.headers.get("content-type"),
            status_code=response.status_code,
            headers=dict(response.headers),
            cookies=dict(response.cookies),
            redirect_history=[
                RedirectHop(
                    url=str(hop.url),
                    status_code=hop.status_code,
                    location=hop.headers.get("location"),
                )
                for hop in response.history
            ],
        )
