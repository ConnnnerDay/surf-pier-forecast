"""Shared HTTP client policy for external providers (sprint 12; `get_text`
added in sprint 15 for NDBC's plain-text responses).

Per docs/architecture.md's ADR-003: "Provider adapters use an async HTTP
client with bounded concurrency, explicit timeouts, limited retries, and
response-size limits." This module is that policy, used by every future
provider adapter (NWS, NOAA CO-OPS, NDBC — sprints 13-16). It has no
knowledge of any specific provider's payload shape.

Design:

- Explicit connect/read/write/pool timeouts (no unbounded waits).
- Bounded retries with exponential backoff, only for transient failures
  (connection errors, timeouts, 429, 502/503/504) — never for 4xx client
  errors, which won't succeed on retry.
- A response-size limit enforced by streaming and counting bytes, not by
  trusting a possibly-absent or wrong Content-Length header.
- A fixed, identifying User-Agent — every provider we call should be able
  to see what's making requests.
- Structured exceptions (ProviderTimeoutError, ProviderHTTPStatusError,
  ProviderResponseTooLargeError, ProviderConnectionError) instead of
  leaking raw httpx exceptions, so calling code can handle "this provider
  is unavailable" as one concern regardless of *why*.
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Self

import httpx

USER_AGENT = (
    "surf-pier-forecast-api/0.1 (+https://github.com/ConnnnerDay/surf-pier-forecast)"
)

_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


class ProviderError(Exception):
    """Base class for all structured errors this client raises."""


class ProviderConnectionError(ProviderError):
    """The request could not reach the provider at all."""


class ProviderTimeoutError(ProviderError):
    """The provider did not respond within the configured timeout."""


class ProviderHTTPStatusError(ProviderError):
    """The provider responded with a non-2xx status after any retries."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderResponseTooLargeError(ProviderError):
    """The provider's response exceeded the configured byte limit."""


class BoundedHTTPClient:
    """An async context manager wrapping httpx.AsyncClient with the policy
    described in the module docstring.

    Usage::

        async with BoundedHTTPClient() as client:
            data = await client.get_json("https://api.weather.gov/...")
    """

    def __init__(
        self,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.5,
        max_response_bytes: int = 2_000_000,
        user_agent: str = USER_AGENT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._backoff_base_seconds = backoff_base_seconds
        self._max_response_bytes = max_response_bytes
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent},
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    async def get_json(self, url: str, **kwargs: object) -> object:
        """GET `url`, retrying transient failures, and return the parsed
        JSON body. Raises a ProviderError subclass on any failure that
        survives retries.
        """
        response = await self._request_with_retries("GET", url, **kwargs)
        return response.json()

    async def get_text(self, url: str, **kwargs: object) -> str:
        """GET `url`, retrying transient failures, and return the decoded
        response body as text (e.g. NDBC's fixed-width realtime2 format,
        which is not JSON). Raises a ProviderError subclass on any
        failure that survives retries.
        """
        response = await self._request_with_retries("GET", url, **kwargs)
        return response.text

    async def _request_with_retries(
        self, method: str, url: str, **kwargs: object
    ) -> httpx.Response:
        attempt = 0
        while True:
            try:
                return await self._request_once(method, url, **kwargs)
            except (
                ProviderConnectionError,
                ProviderTimeoutError,
                ProviderHTTPStatusError,
            ) as exc:
                is_retryable_status = (
                    isinstance(exc, ProviderHTTPStatusError)
                    and exc.status_code in _RETRYABLE_STATUS_CODES
                )
                is_retryable = (
                    isinstance(exc, (ProviderConnectionError, ProviderTimeoutError))
                    or is_retryable_status
                )
                if not is_retryable or attempt >= self._max_retries:
                    raise
                await asyncio.sleep(self._backoff_base_seconds * (2**attempt))
                attempt += 1

    async def _request_once(
        self, method: str, url: str, **kwargs: object
    ) -> httpx.Response:
        try:
            async with self._client.stream(method, url, **kwargs) as response:  # type: ignore[arg-type]
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_response_bytes:
                        raise ProviderResponseTooLargeError(
                            f"{url} exceeded {self._max_response_bytes} byte limit"
                        )
                if response.status_code >= 400:
                    raise ProviderHTTPStatusError(
                        f"{url} returned HTTP {response.status_code}",
                        status_code=response.status_code,
                    )
                # Rebuild a non-streaming Response so .json()/.text work normally.
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    content=bytes(body),
                    request=response.request,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{url} timed out") from exc
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(f"could not connect to {url}") from exc
