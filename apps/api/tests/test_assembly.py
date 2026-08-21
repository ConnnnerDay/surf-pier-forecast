"""Tests for app.domain.assembly.assemble_forecast.

The core of this test file is the "every present/absent matrix"
requirement: all 2**3 = 8 combinations of (NWS marine zone, NOAA CO-OPS
water temperature, NDBC buoy) succeeding or failing, each asserting the
resulting ForecastState/Confidence/warnings/fallback behavior.

All network access goes through httpx.MockTransport via BoundedHTTPClient
— no live calls, per docs/R2_CI_BASELINE.md's no-live-provider-dependence
rule for CI.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.domain.assembly import ForecastConditions, assemble_forecast
from app.domain.models import ConfidenceLevel, ForecastState, SourceState
from app.domain.scoring import ScoreVerdict
from app.infra.http_client import BoundedHTTPClient
from app.providers.locations import ResolvedLocation, load_water_temp_profiles

_NOW = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)

_WRIGHTSVILLE_BEACH = ResolvedLocation(
    id="wrightsville-beach-nc",
    name="Wrightsville Beach",
    state="NC",
    lat=34.2104,
    lng=-77.7964,
    timezone="America/New_York",
    coops_station="8658163",
    water_temp_station="8658163",
    ndbc_stations=["41110", "41037"],
    nws_zone="AMZ158",
    temp_region="nc_south",
    conditions_region="atlantic_mid",
    temp_offset=0,
    is_dynamic=False,
)

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


def _make_client(*, nws_ok: bool, coops_ok: bool, ndbc_ok: bool) -> BoundedHTTPClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "zones/forecast" in url:
            return (
                httpx.Response(200, json=_MARINE_ZONE_FORECAST)
                if nws_ok
                else httpx.Response(503)
            )
        if "alerts/active" in url:
            return httpx.Response(200, json={"features": []})
        if "datagetter" in url:
            return (
                httpx.Response(200, json=_WATER_TEMP_RESPONSE)
                if coops_ok
                else httpx.Response(503)
            )
        if "ndbc.noaa.gov" in url:
            return (
                httpx.Response(200, text=_NDBC_FEED) if ndbc_ok else httpx.Response(503)
            )
        raise AssertionError(f"unexpected URL: {url}")

    transport = httpx.MockTransport(handler)
    return BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    )


_WATER_TEMP_PROFILES = load_water_temp_profiles()


@pytest.mark.asyncio
@pytest.mark.parametrize("nws_ok", [True, False])
@pytest.mark.parametrize("coops_ok", [True, False])
@pytest.mark.parametrize("ndbc_ok", [True, False])
async def test_present_absent_matrix(
    nws_ok: bool, coops_ok: bool, ndbc_ok: bool
) -> None:
    client = _make_client(nws_ok=nws_ok, coops_ok=coops_ok, ndbc_ok=ndbc_ok)
    async with client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    wind_wave_available = nws_ok or ndbc_ok
    conditions = ForecastConditions.model_validate(envelope.conditions)

    # State: FRESH whenever wind/wave data is available from either
    # source; PARTIAL (temperature-only) when neither is.
    if wind_wave_available:
        assert envelope.state == ForecastState.FRESH
    else:
        assert envelope.state == ForecastState.PARTIAL

    # Confidence: HIGH only when all three sources are live; LOW when
    # wind/wave data is missing entirely; MEDIUM otherwise.
    if nws_ok and coops_ok and ndbc_ok:
        assert envelope.confidence.level == ConfidenceLevel.HIGH
    elif not wind_wave_available:
        assert envelope.confidence.level == ConfidenceLevel.LOW
    else:
        assert envelope.confidence.level == ConfidenceLevel.MEDIUM

    # Water temperature always resolves — live if CO-OPS succeeded,
    # otherwise a labeled fallback. Never silently missing.
    assert conditions.water_temperature is not None
    assert conditions.water_temperature.is_fallback is (not coops_ok)
    if not coops_ok:
        assert conditions.water_temperature.fallback_reason is not None
        assert any(w.code == "fallback:water_temperature" for w in envelope.warnings)

    # Per-source status reflects exactly what happened.
    by_provider = {s.provider: s for s in envelope.sources}
    assert by_provider["nws:marine_zone"].state == (
        SourceState.OK if nws_ok else SourceState.UNAVAILABLE
    )
    assert by_provider["noaa_coops:water_temperature"].state == (
        SourceState.OK if coops_ok else SourceState.UNAVAILABLE
    )
    assert by_provider["ndbc:buoy"].state == (
        SourceState.OK if ndbc_ok else SourceState.UNAVAILABLE
    )

    # Data presence matches source outcome, per-field.
    assert (conditions.marine_zone_wind is not None) == nws_ok
    assert (conditions.marine_zone_wave is not None) == nws_ok
    assert (conditions.buoy is not None) == ndbc_ok

    if not nws_ok:
        assert any(
            w.code == "source_unavailable:nws_marine_zone" for w in envelope.warnings
        )
    if not ndbc_ok:
        assert any(w.code == "source_unavailable:ndbc_buoy" for w in envelope.warnings)

    # Score: present whenever wind/wave data is available from either
    # source (matching FRESH above); UNKNOWN-verdict/None-score only in
    # the PARTIAL (temperature-only) case.
    if wind_wave_available:
        assert conditions.score.score is not None
    else:
        assert conditions.score.score is None
        assert conditions.score.verdict == ScoreVerdict.UNKNOWN


@pytest.mark.asyncio
async def test_astronomy_always_present_regardless_of_other_sources() -> None:
    client = _make_client(nws_ok=False, coops_ok=False, ndbc_ok=False)
    async with client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    conditions = ForecastConditions.model_validate(envelope.conditions)
    assert conditions.sun_times.sunrise < conditions.sun_times.sunset
    assert conditions.solunar.moon_phase is not None


@pytest.mark.asyncio
async def test_location_domain_fields_mapped_correctly() -> None:
    client = _make_client(nws_ok=True, coops_ok=True, ndbc_ok=True)
    async with client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    assert envelope.location.id == "wrightsville-beach-nc"
    assert envelope.location.label == "Wrightsville Beach"
    assert envelope.location.marine_zone == "AMZ158"
    assert "8658163" in envelope.location.station_ids
    assert "41110" in envelope.location.station_ids
    assert "41037" in envelope.location.station_ids


@pytest.mark.asyncio
async def test_tides_hourly_outlook_recommendations_left_none() -> None:
    client = _make_client(nws_ok=True, coops_ok=True, ndbc_ok=True)
    async with client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    assert envelope.tides is None
    assert envelope.hourly_outlook is None
    assert envelope.recommendations is None


@pytest.mark.asyncio
async def test_no_stations_assigned_treated_as_unavailable_not_a_crash() -> None:
    location = _WRIGHTSVILLE_BEACH.model_copy(
        update={"nws_zone": "", "ndbc_stations": [], "water_temp_station": ""}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "alerts/active" in str(request.url)
        return httpx.Response(200, json={"features": []})

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(transport=transport) as client:
        envelope = await assemble_forecast(
            location, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    assert envelope.state == ForecastState.PARTIAL
    assert envelope.confidence.level == ConfidenceLevel.LOW
    conditions = ForecastConditions.model_validate(envelope.conditions)
    assert conditions.water_temperature.is_fallback is True
    assert conditions.score.verdict == ScoreVerdict.UNKNOWN


@pytest.mark.asyncio
async def test_score_prefers_marine_zone_range_over_buoy_when_both_present() -> None:
    """`_MARINE_ZONE_FORECAST` says "10 to 15 kt"/"2 to 3 ft"; `_NDBC_FEED`
    reports a single live wind/wave reading. Both present — the score's
    wind/wave factor descriptions should reflect the marine-zone range,
    not the buoy's single value degenerated to a range.
    """
    client = _make_client(nws_ok=True, coops_ok=True, ndbc_ok=True)
    async with client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    conditions = ForecastConditions.model_validate(envelope.conditions)
    descriptions = " ".join(f.description for f in conditions.score.factors)
    assert "10-15 kt" in descriptions
    assert "2-3 ft" in descriptions


@pytest.mark.asyncio
async def test_score_falls_back_to_buoy_range_when_marine_zone_unavailable() -> None:
    """`_NDBC_FEED`'s single reading (8.2 m/s wind, 1.3 m wave) becomes a
    degenerate zero-width range once NWS's marine-zone range is
    unavailable.
    """
    client = _make_client(nws_ok=False, coops_ok=True, ndbc_ok=True)
    async with client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    conditions = ForecastConditions.model_validate(envelope.conditions)
    assert conditions.buoy is not None
    assert conditions.buoy.wind_speed is not None
    assert conditions.buoy.wave_height is not None
    # score_conditions labels ranges with int() truncation, not round().
    wind_value = int(conditions.buoy.wind_speed.value)
    wave_value = int(conditions.buoy.wave_height.value)
    descriptions = " ".join(f.description for f in conditions.score.factors)
    assert f"{wind_value}-{wind_value} kt" in descriptions
    assert f"{wave_value}-{wave_value} ft" in descriptions


@pytest.mark.asyncio
async def test_score_wind_direction_prefers_marine_zone_over_buoy() -> None:
    """NWS's text says "W wind" (a recognized east-coast offshore
    direction); the buoy reports 000 degrees ("N", not in either
    east-coast direction set — no bonus/penalty). If assembly used the
    buoy's direction instead of NWS's, the offshore bonus would be
    missing.
    """
    marine_zone_w_wind = {
        "properties": {
            "periods": [{"detailedForecast": "W wind 10 to 15 kt. Seas 2 to 3 ft."}]
        }
    }
    ndbc_n_wind = (
        "#YY  MM DD hh mm WDIR WSPD GST   WVHT   PRES\n"
        "#yr  mo dy hr mn degT m/s  m/s     m    hPa\n"
        "2024 07 15 12 00 000 8.2  10.1  1.3   1015.2\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "zones/forecast" in url:
            return httpx.Response(200, json=marine_zone_w_wind)
        if "alerts/active" in url:
            return httpx.Response(200, json={"features": []})
        if "datagetter" in url:
            return httpx.Response(200, json=_WATER_TEMP_RESPONSE)
        if "ndbc.noaa.gov" in url:
            return httpx.Response(200, text=ndbc_n_wind)
        raise AssertionError(f"unexpected URL: {url}")

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    conditions = ForecastConditions.model_validate(envelope.conditions)
    assert any(
        "Clean offshore wind (W)" in f.description for f in conditions.score.factors
    )
