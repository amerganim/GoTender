"""HTTP client enforcing the crawler rules in §8.

Every outbound request in this project goes through PoliteClient. Rate
limiting, backoff and bot identification live here so no adapter can forget
them.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

import httpx

from tenderradar.config import settings

log = logging.getLogger(__name__)

# §8.7: back off on 429/503, never retry aggressively against a government host.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4


class RateLimiter:
    """Serializes requests to one host with a minimum gap between them."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            gap = time.monotonic() - self._last
            if gap < self._min_interval:
                await asyncio.sleep(self._min_interval - gap)
            self._last = time.monotonic()


class PoliteClient:
    """Rate-limited, backing-off, self-identifying HTTP client."""

    def __init__(
        self,
        base_url: str = "",
        *,
        request_delay: float | None = None,
        timeout: float | None = None,
        user_agent: str | None = None,
    ) -> None:
        self._limiter = RateLimiter(
            request_delay
            if request_delay is not None
            else settings.crawler_request_delay_sec
        )
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout if timeout is not None else settings.crawler_timeout_sec,
            follow_redirects=True,
            headers={
                # §8.1: identify the bot with a real contact email.
                "User-Agent": user_agent or settings.user_agent,
                "Accept-Language": "en-US,en;q=0.9,bn;q=0.8",
            },
            # The e-GP portal is a JSP app: it hands out a JSESSIONID on first
            # contact and rejects requests that arrive without one.
            cookies=httpx.Cookies(),
        )

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        last_exc: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.acquire()
            try:
                response = await self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                log.warning(
                    "%s %s failed (%s), attempt %d/%d",
                    method, url, type(exc).__name__, attempt, MAX_ATTEMPTS,
                )
            else:
                if response.status_code not in RETRY_STATUS:
                    return response
                last_exc = httpx.HTTPStatusError(
                    f"{response.status_code} from {url}",
                    request=response.request,
                    response=response,
                )
                log.warning(
                    "%s %s returned %d, attempt %d/%d",
                    method, url, response.status_code, attempt, MAX_ATTEMPTS,
                )
                # Honour Retry-After when the server sends one.
                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    await asyncio.sleep(min(float(retry_after), 120.0))
                    continue

            if attempt < MAX_ATTEMPTS:
                # Exponential backoff with jitter: 2s, 4s, 8s (+/- 25%).
                delay = (2.0**attempt) * (0.75 + random.random() * 0.5)
                await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        return await self.request("POST", url, **kwargs)
