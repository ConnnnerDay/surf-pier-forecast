"""Tests for sprint 26's acceptance bar: "bounded parallel calls, no
duplicates, warm p95 under 750 ms."

- "Bounded parallel calls" is characterized in test_http_client.py
  (BoundedHTTPClient's httpx.Limits configuration).
- "No duplicates" under literal concurrent HTTP requests (not just
  sequential, already covered by test_forecasts_router.py) is
  characterized here with real `asyncio.gather`-driven concurrency via
  httpx.AsyncClient + httpx.ASGITransport — the modern replacement for
  driving FastAPI's ASGI app directly without a live server, needed
  because Starlette's synchronous TestClient can't produce genuine
  overlapping requests. ASGITransport doesn't run FastAPI's lifespan
  (no `lifespan` parameter in this httpx version), so — like every
  other router test in this codebase — app state is supplied via
  `app.dependency_overrides`, not the real lifespan.
- "Warm p95 under 750 ms" is measured here as the *warm* (already-cached)
  path's local processing latency: no real network call is possible in
  this sandboxed CI environment (docs/R2_CI_BASELINE.md's no-live-
  provider-dependence rule), but "warm" by definition means no network
  call should happen at all — a cache hit, then serialization. That is
  exactly what's measured, with real wall-clock timing, not a fake
  clock.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.deps import AppState, get_app_state
from app.api.internal_auth import require_internal_signature
from app.infra.http_client import BoundedHTTPClient
from app.infra.snapshot_cache import SnapshotCache
from app.main import app
from app.providers.stations import StationCatalogCache

_MARINE_ZONE_FORECAST = {
    "properties": {
        "periods": [{"detailedForecast": "SW wind 10 to 15 kt. Seas 2 to 3 ft."}]
    }
}
_WATER_TEMP_RESPONSE = {"data": [{"t": "2024-07-15 12:00", "v": "78.4"}]}
_TIDE_PREDICTIONS_RESPONSE = {
    "predictions": [
        {"t": "2024-07-15 06:32", "v": "5.234", "type": "H"},
        {"t": "2024-07-15 12:48", "v": "0.512", "type": "L"},
    ]
}
_NDBC_FEED = (
    "#YY  MM DD hh mm WDIR WSPD GST   WVHT   PRES\n"
    "#yr  mo dy hr mn degT m/s  m/s     m    hPa\n"
    "2024 07 15 12 00 230 8.2  10.1  1.3   1015.2\n"
)


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "zones/forecast" in url:
        return httpx.Response(200, json=_MARINE_ZONE_FORECAST)
    if "alerts/active" in url:
        return httpx.Response(200, json={"features": []})
    if "product=predictions" in url:
        return httpx.Response(200, json=_TIDE_PREDICTIONS_RESPONSE)
    if "datagetter" in url:
        return httpx.Response(200, json=_WATER_TEMP_RESPONSE)
    if "ndbc.noaa.gov" in url:
        return httpx.Response(200, text=_NDBC_FEED)
    raise AssertionError(f"unexpected URL: {url}")


def _install_mock_state(counts: dict[str, int]) -> None:
    def counting_handler(request: httpx.Request) -> httpx.Response:
        if "zones/forecast" in str(request.url):
            counts["marine_zone"] += 1
        return _handler(request)

    transport = httpx.MockTransport(counting_handler)
    mock_state = AppState(
        http_client=BoundedHTTPClient(transport=transport, max_retries=0),
        coops_tide_cache=StationCatalogCache(),
        coops_watertemp_cache=StationCatalogCache(),
        ndbc_cache=StationCatalogCache(),
        forecast_cache=SnapshotCache(),
    )
    app.dependency_overrides[get_app_state] = lambda: mock_state
    app.dependency_overrides[require_internal_signature] = lambda: None


@pytest.mark.asyncio
async def test_concurrent_requests_for_same_location_share_one_fetch() -> None:
    counts = {"marine_zone": 0}
    _install_mock_state(counts)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                client.get("/v1/forecasts/wrightsville-beach-nc"),
                client.get("/v1/forecasts/wrightsville-beach-nc"),
                client.get("/v1/forecasts/wrightsville-beach-nc"),
            )
    finally:
        app.dependency_overrides.pop(get_app_state, None)
        app.dependency_overrides.pop(require_internal_signature, None)

    assert all(r.status_code == 200 for r in responses)
    assert counts["marine_zone"] == 1


def test_warm_forecast_request_p95_under_750ms() -> None:
    counts = {"marine_zone": 0}
    _install_mock_state(counts)
    try:
        test_client = TestClient(app)
        test_client.get("/v1/forecasts/wrightsville-beach-nc")  # cold: populates cache
        assert counts["marine_zone"] == 1

        samples_ms: list[float] = []
        for _ in range(20):
            start = time.perf_counter()
            resp = test_client.get("/v1/forecasts/wrightsville-beach-nc")
            samples_ms.append((time.perf_counter() - start) * 1000)
            assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_app_state, None)
        app.dependency_overrides.pop(require_internal_signature, None)

    # No new upstream calls — every one of these 20 requests was warm.
    assert counts["marine_zone"] == 1

    samples_ms.sort()
    p95_index = int(len(samples_ms) * 0.95)
    p95_ms = samples_ms[min(p95_index, len(samples_ms) - 1)]
    assert p95_ms < 750.0, f"warm p95 was {p95_ms:.1f}ms, budget is 750ms"
