"""Tests for app.providers.nws.

Parsing tests use literal fixture text/JSON, matching known NWS response
shapes. Fetch tests go through httpx.MockTransport via BoundedHTTPClient —
no live network calls, per docs/R2_CI_BASELINE.md's
no-live-provider-dependence rule for CI.
"""

from __future__ import annotations

import httpx
import pytest

from app.infra.http_client import BoundedHTTPClient, ProviderHTTPStatusError
from app.providers.nws import (
    MarineZoneConditions,
    fetch_marine_zone_conditions,
    fetch_point_alerts,
    fetch_state_alerts,
    parse_marine_zone_conditions,
)


def _period(text: str) -> dict[str, str]:
    return {"detailedForecast": text}


def test_parse_marine_zone_conditions_abbreviated_direction() -> None:
    periods = [_period("SW wind 10 to 15 kt. Seas 2 to 3 ft.")]

    result = parse_marine_zone_conditions(periods)

    assert result == MarineZoneConditions(
        wind_low_kt=10.0,
        wind_high_kt=15.0,
        wind_direction="SW",
        wave_low_ft=2.0,
        wave_high_ft=3.0,
    )


def test_parse_marine_zone_conditions_spelled_out_direction_and_around() -> None:
    periods = [_period("Southwest wind around 10 knots. Seas around 2 feet.")]

    result = parse_marine_zone_conditions(periods)

    assert result.wind_direction == "SW"
    assert result.wind_low_kt == 10.0
    assert result.wind_high_kt == 10.0
    assert result.wave_low_ft == 2.0
    assert result.wave_high_ft == 2.0


def test_parse_marine_zone_conditions_takes_min_max_across_first_three_periods() -> (
    None
):
    periods = [
        _period("N wind 5 to 10 kt. Seas 1 to 2 ft."),
        _period("NE wind 15 to 20 kt. Seas 3 to 4 ft."),
        _period("E wind 8 kt. Seas 2 ft."),
        _period("S wind 30 kt. Seas 8 ft."),  # outside the first-3 window, ignored
    ]

    result = parse_marine_zone_conditions(periods)

    assert result.wind_direction == "N"
    assert result.wind_low_kt == 5.0
    assert result.wind_high_kt == 20.0
    assert result.wave_low_ft == 1.0
    assert result.wave_high_ft == 4.0


def test_parse_marine_zone_conditions_missing_fields_are_none() -> None:
    periods = [_period("Patchy fog after midnight.")]

    result = parse_marine_zone_conditions(periods)

    assert result == MarineZoneConditions()


def test_parse_marine_zone_conditions_empty_periods() -> None:
    assert parse_marine_zone_conditions([]) == MarineZoneConditions()


@pytest.mark.asyncio
async def test_fetch_marine_zone_conditions_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/zones/forecast/AMZ158/forecast"
        return httpx.Response(
            200,
            json={
                "properties": {
                    "periods": [_period("SW wind 10 to 15 kt. Seas 2 to 3 ft.")]
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        result = await fetch_marine_zone_conditions(client, zone="AMZ158")

    assert result.wind_low_kt == 10.0
    assert result.wave_high_ft == 3.0


@pytest.mark.asyncio
async def test_fetch_marine_zone_conditions_propagates_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        with pytest.raises(ProviderHTTPStatusError):
            await fetch_marine_zone_conditions(client)


def test_parse_alerts_geojson_shape() -> None:
    from app.providers.nws import _parse_alerts

    payload = {
        "features": [
            {
                "properties": {
                    "event": "Small Craft Advisory",
                    "severity": "Moderate",
                    "headline": "Small Craft Advisory in effect",
                    "description": "x" * 400,
                }
            },
            {"properties": {"event": ""}},  # no event, dropped
        ]
    }

    alerts = _parse_alerts(payload, limit=5)

    assert len(alerts) == 1
    assert alerts[0].event == "Small Craft Advisory"
    assert len(alerts[0].description) == 300


def test_parse_alerts_json_ld_shape() -> None:
    from app.providers.nws import _parse_alerts

    payload = {
        "@graph": [
            {
                "event": "Gale Warning",
                "severity": "Severe",
                "headline": "Gale Warning in effect",
                "description": "strong winds expected",
            }
        ]
    }

    alerts = _parse_alerts(payload, limit=5)

    assert len(alerts) == 1
    assert alerts[0].event == "Gale Warning"


def test_parse_alerts_respects_limit() -> None:
    from app.providers.nws import _parse_alerts

    payload = {"features": [{"properties": {"event": f"Alert {i}"}} for i in range(10)]}

    alerts = _parse_alerts(payload, limit=3)

    assert len(alerts) == 3


@pytest.mark.asyncio
async def test_fetch_point_alerts_returns_empty_on_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        alerts = await fetch_point_alerts(client, 34.2, -77.8)

    assert alerts == []


@pytest.mark.asyncio
async def test_fetch_point_alerts_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "point=34.2,-77.8" in str(request.url)
        return httpx.Response(
            200,
            json={"features": [{"properties": {"event": "Rip Current Statement"}}]},
        )

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        alerts = await fetch_point_alerts(client, 34.2, -77.8)

    assert len(alerts) == 1
    assert alerts[0].event == "Rip Current Statement"


@pytest.mark.asyncio
async def test_fetch_state_alerts_empty_state_code_short_circuits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called for an empty state code")

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        alerts = await fetch_state_alerts(client, "")

    assert alerts == []


@pytest.mark.asyncio
async def test_fetch_state_alerts_uppercases_state_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "area=NC" in str(request.url)
        return httpx.Response(200, json={"features": []})

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        alerts = await fetch_state_alerts(client, "nc")

    assert alerts == []
