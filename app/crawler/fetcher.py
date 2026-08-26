"""Async HTTP fetcher for static resources, with Basic Auth and retry/backoff.

The `httpx.AsyncClient` is injected via the constructor so tests can supply
a mock transport instead of hitting the network. Never log request headers
or the constructor's credentials -- the Authorization header carries the
target site's Basic Auth secret on every request this fetcher sends.

Does not follow redirects (httpx's default): a 3xx response is returned
as-is, so there is nothing here that could chase a redirect loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx


@dataclass
class FetchResult:
    content: bytes
    content_type: str | None
    status_code: int
    headers: dict[str, str]
    cookies: dict[str, str]


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

    async def fetch(self, url: str) -> FetchResult:
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                response = await self._client.get(
                    url, auth=self._auth, timeout=self._timeout
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
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
        )
