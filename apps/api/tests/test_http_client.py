"""Tests for app.infra.http_client.BoundedHTTPClient.

All requests go through httpx.MockTransport — no live network calls, per
docs/R2_CI_BASELINE.md's no-live-provider-dependence rule for CI.
"""

from __future__ import annotations

import httpx
import pytest

from app.infra.http_client import (
    BoundedHTTPClient,
    ProviderConnectionError,
    ProviderHTTPStatusError,
    ProviderResponseTooLargeError,
    ProviderTimeoutError,
)


@pytest.mark.asyncio
async def test_get_json_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        result = await client.get_json("https://example.test/data")

    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_get_text_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="#YY MM DD hh mm WDIR WSPD\n#yr mo dy hr mn degT m/s\n"
        )

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        result = await client.get_text("https://example.test/data.txt")

    assert result.startswith("#YY MM DD")


@pytest.mark.asyncio
async def test_get_text_propagates_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        with pytest.raises(ProviderHTTPStatusError):
            await client.get_text("https://example.test/data.txt")


@pytest.mark.asyncio
async def test_get_json_sends_user_agent() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        await client.get_json("https://example.test/data")

    assert "surf-pier-forecast-api" in seen_headers["user-agent"]


@pytest.mark.asyncio
async def test_transient_503_then_success_is_retried() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"attempt": attempts["count"]})

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, backoff_base_seconds=0.0
    ) as client:
        result = await client.get_json("https://example.test/flaky")

    assert result == {"attempt": 3}
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_persistent_5xx_exhausts_retries_and_raises() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=2, backoff_base_seconds=0.0
    ) as client:
        with pytest.raises(ProviderHTTPStatusError) as exc_info:
            await client.get_json("https://example.test/down")

    assert exc_info.value.status_code == 503
    assert attempts["count"] == 3  # initial attempt + 2 retries


@pytest.mark.asyncio
async def test_404_is_not_retried() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=2, backoff_base_seconds=0.0
    ) as client:
        with pytest.raises(ProviderHTTPStatusError) as exc_info:
            await client.get_json("https://example.test/missing")

    assert exc_info.value.status_code == 404
    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_timeout_raises_provider_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        with pytest.raises(ProviderTimeoutError):
            await client.get_json("https://example.test/slow")


@pytest.mark.asyncio
async def test_connect_error_raises_provider_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect", request=request)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        with pytest.raises(ProviderConnectionError):
            await client.get_json("https://example.test/unreachable")


@pytest.mark.asyncio
async def test_oversized_response_raises_and_is_not_retried() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(200, content=b"x" * 100)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport,
        max_response_bytes=10,
        max_retries=2,
        backoff_base_seconds=0.0,
    ) as client:
        with pytest.raises(ProviderResponseTooLargeError):
            await client.get_json("https://example.test/huge")

    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_timeout_is_retried_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=2, backoff_base_seconds=0.0
    ) as client:
        result = await client.get_json("https://example.test/eventually-ok")

    assert result == {"ok": True}
    assert attempts["count"] == 2
