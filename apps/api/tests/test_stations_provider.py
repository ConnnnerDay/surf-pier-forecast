"""Tests for app.providers.stations.

Fetch tests go through httpx.MockTransport via BoundedHTTPClient — no live
network calls, per docs/R2_CI_BASELINE.md's no-live-provider-dependence
rule for CI. StationCatalogCache tests use an injected fake clock instead
of sleeping, so TTL behavior is deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.infra.http_client import BoundedHTTPClient
from app.providers.stations import (
    CoopsStationCatalogEntry,
    NdbcStationCatalogEntry,
    StationCatalogCache,
    fetch_coops_tide_catalog,
    fetch_ndbc_catalog,
    nearest_coops_station,
    nearest_ndbc_stations,
)


def _coops(
    id_: str, lat: float, lng: float, name: str = "", state: str = "NC"
) -> CoopsStationCatalogEntry:
    return CoopsStationCatalogEntry(id=id_, name=name, lat=lat, lng=lng, state=state)


def _ndbc(
    id_: str, lat: float, lng: float, has_met: bool = True
) -> NdbcStationCatalogEntry:
    return NdbcStationCatalogEntry(id=id_, lat=lat, lng=lng, has_met=has_met)


def test_nearest_coops_station_picks_closest() -> None:
    stations = [
        _coops("A", 34.30, -77.90, name="Far"),
        _coops("B", 34.21, -77.80, name="Near"),
        _coops("C", 35.00, -76.00, name="Farther"),
    ]

    result = nearest_coops_station(stations, 34.2104, -77.7964)

    assert result is not None
    assert result.id == "B"
    assert result.name == "Near"
    assert result.distance_miles >= 0


def test_nearest_coops_station_empty_catalog_returns_none() -> None:
    assert nearest_coops_station([], 34.2104, -77.7964) is None


def test_nearest_ndbc_stations_excludes_non_met_buoys() -> None:
    stations = [
        _ndbc("41110", 34.19, -77.75, has_met=True),
        _ndbc("99999", 34.20, -77.76, has_met=False),  # closer, but no met sensors
        _ndbc("41037", 34.50, -77.00, has_met=True),
    ]

    result = nearest_ndbc_stations(stations, 34.2104, -77.7964, n=2)

    ids = [s.id for s in result]
    assert "99999" not in ids
    assert ids == ["41110", "41037"]


def test_nearest_ndbc_stations_respects_limit() -> None:
    stations = [_ndbc(str(i), 34.0 + i * 0.01, -77.0, has_met=True) for i in range(5)]

    result = nearest_ndbc_stations(stations, 34.2104, -77.7964, n=2)

    assert len(result) == 2


def test_nearest_ndbc_stations_empty_catalog_returns_empty() -> None:
    assert nearest_ndbc_stations([], 34.2104, -77.7964) == []


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_cache_reuses_fresh_result_without_refetching() -> None:
    clock = _FakeClock(datetime(2024, 1, 1, tzinfo=UTC))
    cache: StationCatalogCache[str] = StationCatalogCache(clock=clock)
    calls = {"count": 0}

    async def fetch() -> list[str]:
        calls["count"] += 1
        return ["a", "b"]

    first = await cache.get_or_refresh(fetch)
    clock.advance(60)  # well within the 24h TTL
    second = await cache.get_or_refresh(fetch)

    assert first == second == ["a", "b"]
    assert calls["count"] == 1
    assert cache.last_fetched_at == datetime(2024, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_cache_refetches_after_ttl_expires() -> None:
    clock = _FakeClock(datetime(2024, 1, 1, tzinfo=UTC))
    cache: StationCatalogCache[str] = StationCatalogCache(ttl_seconds=100, clock=clock)
    calls = {"count": 0}

    async def fetch() -> list[str]:
        calls["count"] += 1
        return [f"result-{calls['count']}"]

    await cache.get_or_refresh(fetch)
    clock.advance(101)
    result = await cache.get_or_refresh(fetch)

    assert calls["count"] == 2
    assert result == ["result-2"]


@pytest.mark.asyncio
async def test_cache_uses_short_negative_ttl_after_failed_fetch() -> None:
    clock = _FakeClock(datetime(2024, 1, 1, tzinfo=UTC))
    cache: StationCatalogCache[str] = StationCatalogCache(
        ttl_seconds=1000, negative_ttl_seconds=10, clock=clock
    )
    calls = {"count": 0}

    async def fetch() -> list[str]:
        calls["count"] += 1
        return []  # degraded fetch, empty result

    await cache.get_or_refresh(fetch)
    clock.advance(11)  # past the 10s negative TTL, well within the 1000s positive TTL
    await cache.get_or_refresh(fetch)

    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_cache_reuses_empty_result_within_negative_ttl() -> None:
    clock = _FakeClock(datetime(2024, 1, 1, tzinfo=UTC))
    cache: StationCatalogCache[str] = StationCatalogCache(
        negative_ttl_seconds=60, clock=clock
    )
    calls = {"count": 0}

    async def fetch() -> list[str]:
        calls["count"] += 1
        return []

    await cache.get_or_refresh(fetch)
    clock.advance(30)
    await cache.get_or_refresh(fetch)

    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_fetch_coops_tide_catalog_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "type=tidepredictions" in str(request.url)
        return httpx.Response(
            200,
            json={
                "stations": [
                    {
                        "id": "8658163",
                        "name": "Wrightsville Beach",
                        "lat": "34.2135",
                        "lng": "-77.7865",
                        "state": "NC",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        result = await fetch_coops_tide_catalog(client)

    assert len(result) == 1
    assert result[0].id == "8658163"
    assert result[0].state == "NC"


@pytest.mark.asyncio
async def test_fetch_coops_tide_catalog_skips_malformed_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "stations": [
                    {
                        "id": "8658163",
                        "name": "Good",
                        "lat": "34.2",
                        "lng": "-77.8",
                        "state": "NC",
                    },
                    {"id": "missing-lat", "name": "Bad"},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        result = await fetch_coops_tide_catalog(client)

    assert len(result) == 1
    assert result[0].id == "8658163"


@pytest.mark.asyncio
async def test_fetch_coops_tide_catalog_degrades_to_empty_on_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        result = await fetch_coops_tide_catalog(client)

    assert result == []


_NDBC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station id="41110" lat="34.194" lon="-77.75" name="Masonboro Inlet" met="y" />
  <station id="99999" lat="35.0" lon="-76.0" name="No Sensors" met="n" />
  <station id="bad" lat="not-a-number" lon="-76.0" met="y" />
</stations>
"""


@pytest.mark.asyncio
async def test_fetch_ndbc_catalog_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_NDBC_XML)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        result = await fetch_ndbc_catalog(client)

    assert len(result) == 2  # the malformed lat row is skipped
    by_id = {s.id: s for s in result}
    assert by_id["41110"].has_met is True
    assert by_id["99999"].has_met is False


@pytest.mark.asyncio
async def test_fetch_ndbc_catalog_malformed_xml_degrades_to_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not xml at all <<<")

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        result = await fetch_ndbc_catalog(client)

    assert result == []


@pytest.mark.asyncio
async def test_fetch_ndbc_catalog_degrades_to_empty_on_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        result = await fetch_ndbc_catalog(client)

    assert result == []
