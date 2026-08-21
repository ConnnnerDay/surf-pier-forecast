"""Tests for the /v1/forecasts router (sprint 25).

All network access goes through httpx.MockTransport via a real
BoundedHTTPClient injected through app.dependency_overrides — no live
calls, per docs/R2_CI_BASELINE.md's no-live-provider-dependence rule.

`require_internal_signature` is overridden to a no-op throughout: this
file is about router/domain behavior, not ADR-004 signature verification
(see test_internal_signature.py, test_internal_auth.py, and
test_internal_api_wiring.py for that).
"""

from __future__ import annotations

from collections.abc import Iterator

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
_NDBC_FEED = (
    "#YY  MM DD hh mm WDIR WSPD GST   WVHT   PRES\n"
    "#yr  mo dy hr mn degT m/s  m/s     m    hPa\n"
    "2024 07 15 12 00 230 8.2  10.1  1.3   1015.2\n"
)
_COOPS_CATALOG = {
    "stations": [
        {
            "id": "8658163",
            "name": "Wrightsville Beach",
            "lat": "34.2135",
            "lng": "-77.7865",
            "state": "NC",
        }
    ]
}
_NDBC_CATALOG = (
    "<stations>"
    '<station id="41110" lat="34.194" lon="-77.75" name="Masonboro Inlet" met="y" />'
    "</stations>"
)


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "zones/forecast" in url:
        return httpx.Response(200, json=_MARINE_ZONE_FORECAST)
    if "alerts/active" in url:
        return httpx.Response(200, json={"features": []})
    if "datagetter" in url:
        return httpx.Response(200, json=_WATER_TEMP_RESPONSE)
    if "activestations.xml" in url:
        return httpx.Response(200, text=_NDBC_CATALOG)
    if "type=tidepredictions" in url or "type=watertemp" in url:
        return httpx.Response(200, json=_COOPS_CATALOG)
    if "ndbc.noaa.gov" in url:
        return httpx.Response(200, text=_NDBC_FEED)
    raise AssertionError(f"unexpected URL: {url}")


@pytest.fixture
def client() -> Iterator[TestClient]:
    transport = httpx.MockTransport(_handler)
    mock_state = AppState(
        http_client=BoundedHTTPClient(transport=transport, max_retries=0),
        coops_tide_cache=StationCatalogCache(),
        coops_watertemp_cache=StationCatalogCache(),
        ndbc_cache=StationCatalogCache(),
        forecast_cache=SnapshotCache(),
    )
    app.dependency_overrides[get_app_state] = lambda: mock_state
    app.dependency_overrides[require_internal_signature] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_app_state, None)
        app.dependency_overrides.pop(require_internal_signature, None)


def test_get_forecast_for_curated_location(client: TestClient) -> None:
    resp = client.get("/v1/forecasts/wrightsville-beach-nc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["location"]["id"] == "wrightsville-beach-nc"
    assert body["state"] == "fresh"
    assert body["conditions"]["water_temperature"] is not None


def test_get_forecast_for_dynamic_point(client: TestClient) -> None:
    resp = client.get("/v1/forecasts/pt_34.200_-77.800")
    assert resp.status_code == 200
    body = resp.json()
    assert body["location"]["id"] == "pt_34.200_-77.800"


def test_get_forecast_unknown_location_is_404(client: TestClient) -> None:
    resp = client.get("/v1/forecasts/not-a-real-id")
    assert resp.status_code == 404


def test_get_forecast_non_coastal_point_is_422(client: TestClient) -> None:
    resp = client.get("/v1/forecasts/pt_45.000_-95.000")
    assert resp.status_code == 422


def test_refresh_forecast_returns_same_shape_as_get(client: TestClient) -> None:
    get_resp = client.get("/v1/forecasts/wrightsville-beach-nc")
    refresh_resp = client.post("/v1/forecasts/wrightsville-beach-nc/refresh")
    assert refresh_resp.status_code == 200
    assert set(refresh_resp.json().keys()) == set(get_resp.json().keys())
    assert refresh_resp.json()["location"] == get_resp.json()["location"]


def _counting_client() -> tuple[BoundedHTTPClient, dict[str, int]]:
    counts = {"marine_zone": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "zones/forecast" in url:
            counts["marine_zone"] += 1
        return _handler(request)

    transport = httpx.MockTransport(handler)
    return BoundedHTTPClient(transport=transport, max_retries=0), counts


def test_second_get_is_served_from_cache_not_refetched() -> None:
    http_client, counts = _counting_client()
    mock_state = AppState(
        http_client=http_client,
        coops_tide_cache=StationCatalogCache(),
        coops_watertemp_cache=StationCatalogCache(),
        ndbc_cache=StationCatalogCache(),
        forecast_cache=SnapshotCache(),
    )
    app.dependency_overrides[get_app_state] = lambda: mock_state
    app.dependency_overrides[require_internal_signature] = lambda: None
    try:
        test_client = TestClient(app)
        test_client.get("/v1/forecasts/wrightsville-beach-nc")
        test_client.get("/v1/forecasts/wrightsville-beach-nc")
    finally:
        app.dependency_overrides.pop(get_app_state, None)
        app.dependency_overrides.pop(require_internal_signature, None)

    assert counts["marine_zone"] == 1


def test_refresh_forces_a_live_fetch_even_right_after_a_get() -> None:
    http_client, counts = _counting_client()
    mock_state = AppState(
        http_client=http_client,
        coops_tide_cache=StationCatalogCache(),
        coops_watertemp_cache=StationCatalogCache(),
        ndbc_cache=StationCatalogCache(),
        forecast_cache=SnapshotCache(),
    )
    app.dependency_overrides[get_app_state] = lambda: mock_state
    app.dependency_overrides[require_internal_signature] = lambda: None
    try:
        test_client = TestClient(app)
        test_client.get("/v1/forecasts/wrightsville-beach-nc")
        test_client.post("/v1/forecasts/wrightsville-beach-nc/refresh")
    finally:
        app.dependency_overrides.pop(get_app_state, None)
        app.dependency_overrides.pop(require_internal_signature, None)

    assert counts["marine_zone"] == 2
