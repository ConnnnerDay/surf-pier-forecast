"""Tests for the /v1/locations router (sprint 25).

Network-touching cases go through httpx.MockTransport via a real
BoundedHTTPClient injected through app.dependency_overrides — no live
calls, per docs/R2_CI_BASELINE.md's no-live-provider-dependence rule.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.deps import AppState, get_app_state
from app.infra.http_client import BoundedHTTPClient
from app.main import app
from app.providers.stations import StationCatalogCache

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
    if "type=tidepredictions" in url or "type=watertemp" in url:
        return httpx.Response(200, json=_COOPS_CATALOG)
    if "activestations.xml" in url:
        return httpx.Response(200, text=_NDBC_CATALOG)
    raise AssertionError(f"unexpected URL: {url}")


@pytest.fixture
def client() -> Iterator[TestClient]:
    transport = httpx.MockTransport(_handler)
    mock_state = AppState(
        http_client=BoundedHTTPClient(transport=transport, max_retries=0),
        coops_tide_cache=StationCatalogCache(),
        coops_watertemp_cache=StationCatalogCache(),
        ndbc_cache=StationCatalogCache(),
    )
    app.dependency_overrides[get_app_state] = lambda: mock_state
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_app_state, None)


def test_search_matches_by_name(client: TestClient) -> None:
    resp = client.get("/v1/locations/search", params={"q": "wrightsville"})
    assert resp.status_code == 200
    ids = [loc["id"] for loc in resp.json()]
    assert "wrightsville-beach-nc" in ids


def test_search_no_match_returns_empty_list(client: TestClient) -> None:
    resp = client.get("/v1/locations/search", params={"q": "zzz-nonexistent-zzz"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_requires_nonempty_query(client: TestClient) -> None:
    resp = client.get("/v1/locations/search", params={"q": ""})
    assert resp.status_code == 422


def test_resolve_by_curated_id(client: TestClient) -> None:
    resp = client.post(
        "/v1/locations/resolve", json={"location_id": "wrightsville-beach-nc"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "wrightsville-beach-nc"
    assert body["is_dynamic"] is False


def test_resolve_unknown_curated_id_is_404(client: TestClient) -> None:
    resp = client.post("/v1/locations/resolve", json={"location_id": "not-a-real-id"})
    assert resp.status_code == 404


def test_resolve_by_point_near_wrightsville_beach(client: TestClient) -> None:
    resp = client.post("/v1/locations/resolve", json={"lat": 34.20, "lng": -77.80})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_dynamic"] is True
    assert body["id"].startswith("pt_")


def test_resolve_far_from_any_station_is_422(client: TestClient) -> None:
    resp = client.post("/v1/locations/resolve", json={"lat": 45.0, "lng": -95.0})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"location_id": "x", "lat": 1.0, "lng": 2.0},
        {"lat": 1.0},
    ],
)
def test_resolve_rejects_wrong_shaped_body(
    client: TestClient, body: dict[str, object]
) -> None:
    resp = client.post("/v1/locations/resolve", json=body)
    assert resp.status_code == 422


def test_resolve_rejects_out_of_range_coordinates(client: TestClient) -> None:
    resp = client.post("/v1/locations/resolve", json={"lat": 999.0, "lng": 0.0})
    assert resp.status_code == 422
