"""Tests for app.providers.ndbc.

Fetch tests go through httpx.MockTransport via BoundedHTTPClient — no live
network calls, per docs/R2_CI_BASELINE.md's no-live-provider-dependence
rule for CI.
"""

from __future__ import annotations

import httpx
import pytest

from app.infra.http_client import BoundedHTTPClient
from app.providers.ndbc import (
    BuoyObservation,
    NdbcDataUnavailableError,
    _deg_to_compass,
    fetch_buoy_observation,
    parse_realtime_text,
)

_HEADER = "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP"
_UNITS = "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC"


def _feed(*rows: str) -> str:
    return "\n".join([_HEADER, _UNITS, *rows])


def test_deg_to_compass_cardinal_and_intercardinal() -> None:
    assert _deg_to_compass(0) == "N"
    assert _deg_to_compass(90) == "E"
    assert _deg_to_compass(180) == "S"
    assert _deg_to_compass(270) == "W"
    assert _deg_to_compass(45) == "NE"
    assert _deg_to_compass(360) == "N"  # wraps


def test_parse_realtime_text_full_row() -> None:
    row = "2024 07 15 12 00 230 8.2  10.1  1.3   6  4.2 235 1015.2  24.0  25.1"
    result = parse_realtime_text(_feed(row))

    assert result.wind_speed_kt == round(8.2 * 1.94384, 1)
    assert result.wind_gust_kt == round(10.1 * 1.94384, 1)
    assert result.wind_direction == "SW"
    assert result.wave_height_ft == round(1.3 * 3.28084, 1)
    assert result.pressure_mb == 1015.2


def test_parse_realtime_text_all_missing_markers_raises() -> None:
    row = "2024 07 15 12 00 MM   MM   MM    MM     MM   MM  MM   MM     24.0  25.1"
    with pytest.raises(NdbcDataUnavailableError):
        parse_realtime_text(_feed(row))


def test_parse_realtime_text_partial_missing_markers_leave_only_those_fields_none() -> (
    None
):
    row = "2024 07 15 12 00 MM   MM   MM    1.3    6  4.2 235 1015.2  24.0  25.1"
    result = parse_realtime_text(_feed(row))

    assert result == BuoyObservation(
        wave_height_ft=round(1.3 * 3.28084, 1), pressure_mb=1015.2
    )


def test_parse_realtime_text_gust_falls_back_to_speed_when_missing() -> None:
    row = "2024 07 15 12 00 230 8.2  MM    MM     6  4.2 235 MM      24.0  25.1"
    result = parse_realtime_text(_feed(row))

    assert result.wind_speed_kt == round(8.2 * 1.94384, 1)
    assert result.wind_gust_kt == result.wind_speed_kt


def test_parse_realtime_text_missing_column_entirely() -> None:
    header = "#YY  MM DD hh mm WDIR WSPD GST   PRES  ATMP  WTMP"
    units = "#yr  mo dy hr mn degT m/s  m/s    hPa  degC  degC"
    row = "2024 07 15 12 00 230 8.2  10.1 1015.2 24.0  25.1"
    text = f"{header}\n{units}\n{row}"

    result = parse_realtime_text(text)

    assert result.wind_speed_kt is not None
    assert result.wave_height_ft is None  # WVHT column doesn't exist at all


def test_parse_realtime_text_takes_first_usable_row_per_field() -> None:
    rows = [
        "2024 07 15 12 00 MM   MM   MM    1.3    6  4.2 235 1015.2  24.0  25.1",
        "2024 07 15 11 00 230 8.2  10.1  1.5     6  4.2 235 1014.0  24.0  25.1",
    ]
    result = parse_realtime_text(_feed(*rows))

    # Wind comes from the second (older) row since the first row's wind was missing.
    assert result.wind_speed_kt == round(8.2 * 1.94384, 1)
    # Wave and pressure come from the first (most recent) row since both were present.
    assert result.wave_height_ft == round(1.3 * 3.28084, 1)
    assert result.pressure_mb == 1015.2


def test_parse_realtime_text_short_row_is_skipped() -> None:
    rows = [
        "2024 07 15 12 00 MM",  # too few fields, skipped
        "2024 07 15 11 00 230 8.2  10.1  1.3   6  4.2 235 1015.2  24.0  25.1",
    ]
    result = parse_realtime_text(_feed(*rows))

    assert result.wind_speed_kt == round(8.2 * 1.94384, 1)


def test_parse_realtime_text_too_few_lines_raises() -> None:
    with pytest.raises(NdbcDataUnavailableError):
        parse_realtime_text(_HEADER + "\n" + _UNITS)


def test_parse_realtime_text_all_rows_missing_raises() -> None:
    rows = [
        "2024 07 15 12 00 MM   MM   MM    MM     MM   MM  MM   MM     24.0  25.1",
        "2024 07 15 11 00 MM   MM   MM    MM     MM   MM  MM   MM     24.0  25.1",
    ]
    with pytest.raises(NdbcDataUnavailableError):
        parse_realtime_text(_feed(*rows))


@pytest.mark.asyncio
async def test_fetch_buoy_observation_success() -> None:
    row = "2024 07 15 12 00 230 8.2  10.1  1.3   6  4.2 235 1015.2  24.0  25.1"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://www.ndbc.noaa.gov/data/realtime2/41110.txt"
        return httpx.Response(200, text=_feed(row))

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        result = await fetch_buoy_observation(client, "41110")

    assert result.wind_direction == "SW"
