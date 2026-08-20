"""Tests for app.domain.normalize. Pure functions — no network mocking."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.normalize import (
    PROVIDER_NDBC,
    PROVIDER_NOAA_COOPS,
    PROVIDER_NWS,
    UNIT_DEGREES_FAHRENHEIT,
    UNIT_FEET,
    UNIT_KNOTS,
    UNIT_MILLIBARS,
    normalize_buoy_readings,
    normalize_marine_zone_wave_range,
    normalize_marine_zone_wind_range,
    normalize_tide_prediction,
    normalize_water_temperature,
)
from app.providers.ndbc import BuoyObservation
from app.providers.noaa_coops import TidePrediction, WaterTemperatureReading
from app.providers.nws import MarineZoneConditions

_NOW = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)


def test_normalize_water_temperature() -> None:
    reading = WaterTemperatureReading(value_f=78.4, observed_at=_NOW)

    obs = normalize_water_temperature(reading, station_id="8658163")

    assert obs.value == 78.4
    assert obs.unit == UNIT_DEGREES_FAHRENHEIT
    assert obs.provider == PROVIDER_NOAA_COOPS
    assert obs.station_id == "8658163"
    assert obs.observed_at == _NOW
    assert obs.is_fallback is False
    assert obs.fallback_reason is None


def test_normalize_tide_prediction() -> None:
    prediction = TidePrediction(time=_NOW, kind="high", height_ft=5.2)

    obs = normalize_tide_prediction(prediction, station_id="8658163")

    assert obs.value == 5.2
    assert obs.unit == UNIT_FEET
    assert obs.provider == PROVIDER_NOAA_COOPS
    assert obs.observed_at == _NOW


def test_normalize_marine_zone_wind_range() -> None:
    conditions = MarineZoneConditions(
        wind_low_kt=10.0, wind_high_kt=15.0, wind_direction="SW"
    )

    result = normalize_marine_zone_wind_range(
        conditions, zone="AMZ158", observed_at=_NOW
    )

    assert result is not None
    assert result.low.value == 10.0
    assert result.high.value == 15.0
    assert result.low.unit == result.high.unit == UNIT_KNOTS
    assert result.low.provider == result.high.provider == PROVIDER_NWS
    assert result.low.station_id == result.high.station_id == "AMZ158"
    assert result.low.value <= result.high.value


def test_normalize_marine_zone_wind_range_none_when_no_wind_parsed() -> None:
    conditions = MarineZoneConditions()

    result = normalize_marine_zone_wind_range(
        conditions, zone="AMZ158", observed_at=_NOW
    )

    assert result is None


def test_normalize_marine_zone_wave_range() -> None:
    conditions = MarineZoneConditions(wave_low_ft=2.0, wave_high_ft=4.0)

    result = normalize_marine_zone_wave_range(
        conditions, zone="AMZ158", observed_at=_NOW
    )

    assert result is not None
    assert result.low.value == 2.0
    assert result.high.value == 4.0
    assert result.low.unit == UNIT_FEET


def test_normalize_marine_zone_wave_range_none_when_no_waves_parsed() -> None:
    conditions = MarineZoneConditions(wind_low_kt=10.0, wind_high_kt=15.0)

    result = normalize_marine_zone_wave_range(
        conditions, zone="AMZ158", observed_at=_NOW
    )

    assert result is None


def test_normalize_buoy_readings_full() -> None:
    obs = BuoyObservation(
        wind_speed_kt=8.2,
        wind_gust_kt=10.1,
        wind_direction="SW",
        wave_height_ft=4.3,
        pressure_mb=1015.2,
    )

    result = normalize_buoy_readings(obs, station_id="41110", observed_at=_NOW)

    assert result.wind_speed is not None
    assert result.wind_speed.value == 8.2
    assert result.wind_speed.unit == UNIT_KNOTS
    assert result.wind_speed.provider == PROVIDER_NDBC
    assert result.wind_speed.station_id == "41110"

    assert result.wind_gust is not None
    assert result.wind_gust.value == 10.1

    assert result.wave_height is not None
    assert result.wave_height.value == 4.3
    assert result.wave_height.unit == UNIT_FEET

    assert result.pressure is not None
    assert result.pressure.value == 1015.2
    assert result.pressure.unit == UNIT_MILLIBARS


def test_normalize_buoy_readings_partial_leaves_missing_fields_none() -> None:
    obs = BuoyObservation(wind_speed_kt=8.2, wind_gust_kt=8.2)

    result = normalize_buoy_readings(obs, station_id="41110", observed_at=_NOW)

    assert result.wind_speed is not None
    assert result.wave_height is None
    assert result.pressure is None


def test_normalize_buoy_readings_all_missing() -> None:
    obs = BuoyObservation()

    result = normalize_buoy_readings(obs, station_id="41110", observed_at=_NOW)

    assert result.wind_speed is None
    assert result.wind_gust is None
    assert result.wave_height is None
    assert result.pressure is None
