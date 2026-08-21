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
from app.domain.confidence import AgedObservation, assess_confidence
from app.domain.models import ConfidenceLevel, ForecastState, SourceState, SourceStatus
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
_GRIDPOINT_POINTS = {
    "properties": {"forecast": "https://api.weather.gov/gridpoints/ILM/1,1/forecast"}
}
_GRIDPOINT_FORECAST = {
    "properties": {"periods": [{"windSpeed": "10 mph", "windDirection": "SW"}]}
}
_COOPS_WIND_RESPONSE = {"data": [{"s": "10.5", "g": "14.2", "d": "SW"}]}
_TIDE_PREDICTIONS_RESPONSE = {
    "predictions": [
        {"t": "2024-07-15 06:32", "v": "5.234", "type": "H"},
        {"t": "2024-07-15 12:48", "v": "0.512", "type": "L"},
    ]
}


def _make_client(
    *,
    nws_ok: bool,
    coops_ok: bool,
    ndbc_ok: bool,
    gridpoint_ok: bool = True,
    coops_wind_ok: bool = True,
    tides_ok: bool = True,
) -> BoundedHTTPClient:
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
        if "product=wind" in url:
            return (
                httpx.Response(200, json=_COOPS_WIND_RESPONSE)
                if coops_wind_ok
                else httpx.Response(503)
            )
        if "product=predictions" in url:
            return (
                httpx.Response(200, json=_TIDE_PREDICTIONS_RESPONSE)
                if tides_ok
                else httpx.Response(503)
            )
        if "datagetter" in url:
            return (
                httpx.Response(200, json=_WATER_TEMP_RESPONSE)
                if coops_ok
                else httpx.Response(503)
            )
        if "gridpoints/ILM" in url:
            return (
                httpx.Response(200, json=_GRIDPOINT_FORECAST)
                if gridpoint_ok
                else httpx.Response(503)
            )
        if "/points/" in url:
            return (
                httpx.Response(200, json=_GRIDPOINT_POINTS)
                if gridpoint_ok
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
    # gridpoint_ok=False, coops_wind_ok=False: this test characterizes
    # only the original three fallible sources per its own docstring.
    # The wind-fallback chain (two more, conditionally-triggered
    # sources) has its own dedicated tests below.
    client = _make_client(
        nws_ok=nws_ok,
        coops_ok=coops_ok,
        ndbc_ok=ndbc_ok,
        gridpoint_ok=False,
        coops_wind_ok=False,
    )
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

    # Confidence: assembly wires per-source liveness and the water-temp
    # observation's age/fallback status into assess_confidence (sprint
    # 23) — reproduce the same inputs here to verify the wiring, not to
    # re-derive assess_confidence's own point arithmetic (that's
    # test_confidence.py's job). Wrightsville Beach is curated
    # (anchor_miles=None), so no station-distance factor applies here.
    expected_sources = [
        SourceStatus(
            provider="nws:marine_zone",
            state=SourceState.OK if nws_ok else SourceState.UNAVAILABLE,
            as_of=_NOW,
        ),
        SourceStatus(
            provider="noaa_coops:water_temperature",
            state=SourceState.OK if coops_ok else SourceState.UNAVAILABLE,
            as_of=_NOW,
        ),
        SourceStatus(
            provider="ndbc:buoy",
            state=SourceState.OK if ndbc_ok else SourceState.UNAVAILABLE,
            as_of=_NOW,
        ),
        # tides_ok defaults to True in _make_client regardless of the
        # three parametrized flags — tides is fetched independently.
        SourceStatus(
            provider="noaa_coops:tides",
            state=SourceState.OK,
            as_of=_NOW,
        ),
    ]
    if not nws_ok and not ndbc_ok:
        # Both primary wind sources down — assembly attempts the wind
        # fallback chain too (coops_wind_ok=False, gridpoint_ok=False
        # here, so both fail), adding two more SourceStatuses in that
        # priority order.
        expected_sources.append(
            SourceStatus(
                provider="noaa_coops:wind",
                state=SourceState.UNAVAILABLE,
                as_of=_NOW,
            )
        )
        expected_sources.append(
            SourceStatus(
                provider="nws:gridpoint_wind",
                state=SourceState.UNAVAILABLE,
                as_of=_NOW,
            )
        )
    expected_confidence = assess_confidence(
        expected_sources,
        now=_NOW,
        observations=[
            AgedObservation(
                "noaa_coops:water_temperature", conditions.water_temperature
            )
        ],
    )
    assert envelope.confidence.level == expected_confidence.level
    assert envelope.confidence.reasons == expected_confidence.reasons

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
    assert by_provider["noaa_coops:tides"].state == SourceState.OK

    # Tides is independent of the three parametrized sources — always
    # present here, since _make_client's tides_ok defaults to True.
    assert envelope.tides is not None
    assert envelope.tides["station_id"] == "8658163"
    assert len(envelope.tides["predictions"]) == 2

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
async def test_tides_and_hourly_outlook_populated_recommendations_left_none() -> None:
    """`tides` (sprint 34's backend half) and `hourly_outlook`
    (`app.domain.timing`, sprint 34's remaining "timing" scope) are both
    populated now; `recommendations` remains deferred to sprint 35.
    """
    client = _make_client(nws_ok=True, coops_ok=True, ndbc_ok=True)
    async with client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    assert envelope.tides is not None
    assert envelope.hourly_outlook is not None
    assert len(envelope.hourly_outlook["hours"]) == 24
    assert envelope.recommendations is None


@pytest.mark.asyncio
async def test_tides_request_uses_local_date_window() -> None:
    """_NOW is 2024-07-15 12:00 UTC; Wrightsville Beach is
    America/New_York (EDT, UTC-4 in July), so local "today" is still
    2024-07-15 -- begin_date/end_date should reflect the *location's*
    local date, not a naive UTC one that could be off by a day for
    other timezones.
    """
    seen_url = {"value": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "product=predictions" in url:
            seen_url["value"] = url
            return httpx.Response(200, json=_TIDE_PREDICTIONS_RESPONSE)
        if "zones/forecast" in url:
            return httpx.Response(200, json=_MARINE_ZONE_FORECAST)
        if "alerts/active" in url:
            return httpx.Response(200, json={"features": []})
        if "datagetter" in url:
            return httpx.Response(200, json=_WATER_TEMP_RESPONSE)
        if "ndbc.noaa.gov" in url:
            return httpx.Response(200, text=_NDBC_FEED)
        raise AssertionError(f"unexpected URL: {url}")

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    assert "begin_date=20240715" in seen_url["value"]
    assert "end_date=20240717" in seen_url["value"]
    assert "station=8658163" in seen_url["value"]


@pytest.mark.asyncio
async def test_tides_unavailable_degrades_to_none_with_warning() -> None:
    client = _make_client(nws_ok=True, coops_ok=True, ndbc_ok=True, tides_ok=False)
    async with client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    assert envelope.tides is None
    by_provider = {s.provider: s for s in envelope.sources}
    assert by_provider["noaa_coops:tides"].state == SourceState.UNAVAILABLE
    assert any(
        w.code == "source_unavailable:noaa_coops_tides" for w in envelope.warnings
    )
    # Everything else still resolves fine -- one source failing doesn't
    # blank the forecast.
    assert envelope.state == ForecastState.FRESH


@pytest.mark.asyncio
async def test_no_stations_assigned_treated_as_unavailable_not_a_crash() -> None:
    location = _WRIGHTSVILLE_BEACH.model_copy(
        update={
            "nws_zone": "",
            "ndbc_stations": [],
            "water_temp_station": "",
            "coops_station": "",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "alerts/active" in url:
            return httpx.Response(200, json={"features": []})
        # Wind is unavailable from both primary sources (no zone, no
        # buoy). CO-OPS wind is skipped entirely (no water_temp_station
        # to query), so only the gridpoint-wind fallback reaches the
        # network — also fails here, so this stays a total-degradation
        # scenario.
        assert "/points/" in url
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        envelope = await assemble_forecast(
            location, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    assert envelope.state == ForecastState.PARTIAL
    assert envelope.confidence.level == ConfidenceLevel.LOW
    conditions = ForecastConditions.model_validate(envelope.conditions)
    assert conditions.water_temperature.is_fallback is True
    assert conditions.score.verdict == ScoreVerdict.UNKNOWN
    by_provider = {s.provider: s for s in envelope.sources}
    assert by_provider["noaa_coops:wind"].state == SourceState.UNAVAILABLE
    assert by_provider["nws:gridpoint_wind"].state == SourceState.UNAVAILABLE
    assert by_provider["noaa_coops:tides"].state == SourceState.UNAVAILABLE
    assert envelope.tides is None


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
        if "product=predictions" in url:
            return httpx.Response(200, json=_TIDE_PREDICTIONS_RESPONSE)
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


@pytest.mark.asyncio
async def test_dynamic_location_anchor_distance_degrades_confidence() -> None:
    """A curated location's `anchor_miles` is always `None` (no distance
    factor — see the present/absent matrix test). A dynamic location
    with a real `anchor_miles` should feed a `StationDistance` into
    `assess_confidence`, producing a `distant_station:location:anchor`
    reason and dropping confidence below what the same source liveness
    would otherwise produce (all three sources live would otherwise be
    HIGH — see the present/absent matrix test's case 1).
    """
    dynamic_location = _WRIGHTSVILLE_BEACH.model_copy(
        update={"id": "pt_34.200_-77.800", "is_dynamic": True, "anchor_miles": 60.0}
    )
    client = _make_client(nws_ok=True, coops_ok=True, ndbc_ok=True)
    async with client:
        envelope = await assemble_forecast(
            dynamic_location, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    assert "distant_station:location:anchor" in envelope.confidence.reasons
    assert envelope.confidence.level != ConfidenceLevel.HIGH


@pytest.mark.asyncio
async def test_gridpoint_wind_rescues_forecast_state_when_both_primary_sources_down() -> (
    None
):
    """Both the marine-zone forecast and the NDBC buoy are unavailable —
    without the gridpoint-wind fallback this would be PARTIAL (see the
    present/absent matrix's all-three-down case). CO-OPS wind is also
    unavailable here (`coops_wind_ok=False`), isolating gridpoint's own
    behavior — the chain's own priority order is covered by
    `test_coops_wind_tried_before_gridpoint_wind` below. With
    `_GRIDPOINT_FORECAST` (10 mph / SW) available, the forecast recovers
    to FRESH. The go/no-go *score* stays UNKNOWN even so: the gridpoint
    forecast is land-only and never has wave data, and
    `score_conditions` needs both wind and wave to produce a number
    (sprint 22's contract, unchanged here) — gridpoint wind improves
    `ForecastState` and per-source confidence, but can't single-handedly
    rescue the score without a wave source too.
    """
    client = _make_client(
        nws_ok=False,
        coops_ok=True,
        ndbc_ok=False,
        gridpoint_ok=True,
        coops_wind_ok=False,
    )
    async with client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    assert envelope.state == ForecastState.FRESH
    conditions = ForecastConditions.model_validate(envelope.conditions)
    assert conditions.gridpoint_wind is not None
    assert conditions.gridpoint_wind.wind_direction == "SW"
    assert conditions.score.score is None
    assert conditions.score.verdict == ScoreVerdict.UNKNOWN
    by_provider = {s.provider: s for s in envelope.sources}
    assert by_provider["nws:gridpoint_wind"].state == SourceState.OK
    assert any(w.code == "fallback:gridpoint_wind" for w in envelope.warnings)


@pytest.mark.asyncio
async def test_wind_fallback_chain_not_fetched_when_marine_zone_available() -> None:
    """Marine-zone wind is available, so neither CO-OPS wind nor the
    gridpoint fallback should ever be attempted — proven by using a
    client that raises on any `product=wind` or `/points/` request
    rather than by absence-of-evidence.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "zones/forecast" in url:
            return httpx.Response(200, json=_MARINE_ZONE_FORECAST)
        if "alerts/active" in url:
            return httpx.Response(200, json={"features": []})
        if "product=wind" in url or "/points/" in url:
            raise AssertionError(f"wind fallback chain should not be called: {url}")
        if "product=predictions" in url:
            return httpx.Response(200, json=_TIDE_PREDICTIONS_RESPONSE)
        if "datagetter" in url:
            return httpx.Response(200, json=_WATER_TEMP_RESPONSE)
        if "ndbc.noaa.gov" in url:
            return httpx.Response(200, text=_NDBC_FEED)
        raise AssertionError(f"unexpected URL: {url}")

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    conditions = ForecastConditions.model_validate(envelope.conditions)
    assert conditions.coops_wind is None
    assert conditions.gridpoint_wind is None
    assert all(
        s.provider not in ("noaa_coops:wind", "nws:gridpoint_wind")
        for s in envelope.sources
    )


@pytest.mark.asyncio
async def test_coops_wind_rescues_forecast_when_both_primary_sources_down() -> None:
    """Both the marine-zone forecast and the NDBC buoy are unavailable,
    but CO-OPS wind (`_COOPS_WIND_RESPONSE`, 10.5-14.2 kt / SW) is —
    the forecast should recover using it, without ever reaching the
    gridpoint fallback (proven the same way as the marine-zone case:
    a client that raises on any `/points/` request).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "zones/forecast" in url:
            return httpx.Response(503)
        if "alerts/active" in url:
            return httpx.Response(200, json={"features": []})
        if "product=wind" in url:
            return httpx.Response(200, json=_COOPS_WIND_RESPONSE)
        if "product=predictions" in url:
            return httpx.Response(200, json=_TIDE_PREDICTIONS_RESPONSE)
        if "/points/" in url:
            raise AssertionError(f"gridpoint fallback should not be called: {url}")
        if "datagetter" in url:
            return httpx.Response(200, json=_WATER_TEMP_RESPONSE)
        if "ndbc.noaa.gov" in url:
            return httpx.Response(503)
        raise AssertionError(f"unexpected URL: {url}")

    transport = httpx.MockTransport(handler)
    async with BoundedHTTPClient(
        transport=transport, max_retries=0, backoff_base_seconds=0.0
    ) as client:
        envelope = await assemble_forecast(
            _WRIGHTSVILLE_BEACH, client, _WATER_TEMP_PROFILES, now=_NOW
        )

    assert envelope.state == ForecastState.FRESH
    conditions = ForecastConditions.model_validate(envelope.conditions)
    assert conditions.coops_wind is not None
    assert conditions.coops_wind.wind_direction == "SW"
    assert conditions.gridpoint_wind is None
    by_provider = {s.provider: s for s in envelope.sources}
    assert by_provider["noaa_coops:wind"].state == SourceState.OK
    assert "nws:gridpoint_wind" not in by_provider
    assert any(w.code == "fallback:coops_wind" for w in envelope.warnings)
