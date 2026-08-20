"""Tests for app.providers.noaa_coops.

Fetch tests go through httpx.MockTransport via BoundedHTTPClient — no live
network calls, per docs/R2_CI_BASELINE.md's no-live-provider-dependence
rule for CI.
"""

from __future__ import annotations

import httpx
import pytest

from app.infra.http_client import BoundedHTTPClient
from app.providers.noaa_coops import (
    NoaaDataUnavailableError,
    fetch_tide_predictions,
    fetch_water_temperature,
)


def _datagetter_response(**payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload)


@pytest.mark.asyncio
async def test_fetch_water_temperature_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "product=water_temperature" in str(request.url)
        assert "station=8658163" in str(request.url)
        return _datagetter_response(data=[{"t": "2024-07-15 12:00", "v": "78.4"}])

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        reading = await fetch_water_temperature(client, "8658163")

    assert reading.value_f == 78.4
    assert reading.observed_at.year == 2024
    assert reading.observed_at.month == 7
    assert reading.observed_at.day == 15


@pytest.mark.asyncio
async def test_fetch_water_temperature_summer_is_edt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _datagetter_response(data=[{"t": "2024-07-15 12:00", "v": "78.4"}])

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        reading = await fetch_water_temperature(client, "8658163")

    # Eastern Daylight Time is UTC-4.
    offset = reading.observed_at.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == -4 * 3600


@pytest.mark.asyncio
async def test_fetch_water_temperature_winter_is_est() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _datagetter_response(data=[{"t": "2024-01-15 12:00", "v": "48.1"}])

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        reading = await fetch_water_temperature(client, "8658163")

    # Eastern Standard Time is UTC-5.
    offset = reading.observed_at.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == -5 * 3600


@pytest.mark.asyncio
async def test_fetch_water_temperature_empty_data_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _datagetter_response(data=[])

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        with pytest.raises(NoaaDataUnavailableError):
            await fetch_water_temperature(client, "8658163")


@pytest.mark.asyncio
async def test_fetch_water_temperature_missing_value_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _datagetter_response(data=[{"t": "2024-07-15 12:00", "v": ""}])

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        with pytest.raises(NoaaDataUnavailableError):
            await fetch_water_temperature(client, "8658163")


@pytest.mark.asyncio
async def test_fetch_water_temperature_missing_data_key_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _datagetter_response(error={"message": "No data was found"})

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        with pytest.raises(NoaaDataUnavailableError):
            await fetch_water_temperature(client, "8658163")


@pytest.mark.asyncio
async def test_fetch_tide_predictions_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "product=predictions" in str(request.url)
        assert "begin_date=20240715" in str(request.url)
        return _datagetter_response(
            predictions=[
                {"t": "2024-07-15 06:32", "v": "5.234", "type": "H"},
                {"t": "2024-07-15 12:48", "v": "0.512", "type": "L"},
            ]
        )

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        tides = await fetch_tide_predictions(client, "8658163", "20240715", "20240716")

    assert len(tides) == 2
    assert tides[0].kind == "high"
    assert tides[0].height_ft == 5.234
    assert tides[1].kind == "low"


@pytest.mark.asyncio
async def test_fetch_tide_predictions_skips_rows_missing_time_or_height() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _datagetter_response(
            predictions=[
                {"t": "2024-07-15 06:32", "v": "5.234", "type": "H"},
                {"t": "", "v": "0.512", "type": "L"},
                {"t": "2024-07-15 18:12", "v": "", "type": "H"},
            ]
        )

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        tides = await fetch_tide_predictions(client, "8658163", "20240715", "20240716")

    assert len(tides) == 1
    assert tides[0].kind == "high"


@pytest.mark.asyncio
async def test_fetch_tide_predictions_empty_list_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _datagetter_response(predictions=[])

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        with pytest.raises(NoaaDataUnavailableError):
            await fetch_tide_predictions(client, "8658163", "20240715", "20240716")


@pytest.mark.asyncio
async def test_fetch_tide_predictions_all_rows_unusable_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _datagetter_response(predictions=[{"t": "", "v": "", "type": "H"}])

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        with pytest.raises(NoaaDataUnavailableError):
            await fetch_tide_predictions(client, "8658163", "20240715", "20240716")


@pytest.mark.asyncio
async def test_fetch_tide_predictions_dst_fall_back_does_not_raise() -> None:
    """2024-11-03 is the US fall-back DST transition in America/New_York;
    01:30 occurs twice that day. zoneinfo resolves the ambiguity
    deterministically (fold=0, the first/earlier occurrence) rather than
    raising, unlike pytz's bare .replace(tzinfo=...) which silently
    picks the wrong offset half the time.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return _datagetter_response(
            predictions=[{"t": "2024-11-03 01:30", "v": "3.1", "type": "H"}]
        )

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        tides = await fetch_tide_predictions(client, "8658163", "20241103", "20241104")

    assert len(tides) == 1
    assert tides[0].time.fold == 0
