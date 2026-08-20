"""Forecast assembly (sprint 21).

Concurrently fans out to the three independent, fallible live sources
(NWS marine-zone conditions, NOAA CO-OPS water temperature, NDBC buoy
readings) plus astronomy (pure computation, no failure mode given a
resolved location), and assembles the result into a typed
`ForecastEnvelope` (sprint 11's ADR-003 model) — the actual "one source
failing doesn't blank the forecast" mechanism named in
docs/CANONICAL_ROADMAP.md's product contract's Reliability bullet.

**This sprint designs `ForecastEnvelope.conditions`**, per sprint 11's
domain-models docstring, which named sprints 21/22/34/35 as the owners
of `conditions`/`tides`/`hourly_outlook`/`recommendations`. `tides` is
NOT touched here — the legacy architecture and the roadmap's sprint 34
("Tides and timing") both treat tide predictions as a distinct
time-series/schedule concern from "current conditions," so this sprint
doesn't fetch or shape tide data at all; `hourly_outlook` and
`recommendations` stay opaque for their respective owning sprints too.

**Every present/absent matrix.** The three fallible sources give
2**3 = 8 combinations, each exercised in `tests/test_assembly.py`.
Wind/wave data is redundant across NWS (marine-zone forecast range) and
NDBC (buoy reading) — either alone is enough, matching the Reliability
bullet. Water temperature has no redundant live source, so it falls
back to the monthly climatological average for the location's
`temp_region` (sprint 19) when the live CO-OPS fetch fails — the
fallback-substitution policy repeatedly deferred to "forecast
assembly" since sprint 14 finally lands here. The substitution is
never presented as a live reading: `Observation.is_fallback=True` and
`fallback_reason` are set, per the product contract's Integrity bullet
("missing observations are never turned into invented measurements") —
a labeled fallback is not an invented measurement.

`ForecastState`/`Confidence` here are intentionally simple:

- `ForecastState.FRESH` if wind/wave data is available from either
  source (live or fallback water temp doesn't affect this); `PARTIAL`
  if neither NWS nor NDBC succeeded (temperature-only); `UNAVAILABLE`
  is defined but not reachable through this matrix in practice, since
  the water-temperature fallback and astronomy always resolve — it
  exists for type completeness (e.g. a future scenario where even
  climatology data is missing), not because this sprint's inputs
  produce it.
- `ForecastState.STALE` is not produced here at all: staleness is a
  caching-layer concept (serving a previous fetch past its freshness
  window) and there is no cache yet — that's sprint 24's job.
- `Confidence` is HIGH only when all three sources are live, LOW when
  wind/wave data is missing entirely, MEDIUM otherwise (some
  degradation, including a water-temperature fallback, but wind/wave
  data still available). This is a basic, provisional policy — sprint
  23 ("Confidence model") owns the fuller distance/age/fallback
  degradation policy; this sprint only needed something defensible
  enough to react to its own present/absent matrix.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

from app.domain.models import (
    Confidence,
    ConfidenceLevel,
    ForecastEnvelope,
    ForecastState,
    Location,
    Observation,
    SourceState,
    SourceStatus,
    Warning,
    WarningSeverity,
)
from app.domain.normalize import NormalizedBuoyReadings as NormalizedBuoy
from app.domain.normalize import (
    ObservationRange,
    normalize_buoy_readings,
    normalize_marine_zone_wave_range,
    normalize_marine_zone_wind_range,
    normalize_water_temperature,
)
from app.infra.http_client import BoundedHTTPClient, ProviderError
from app.providers.astronomy import LunarDetails, SolunarTimes, SunTimes, TwilightTimes
from app.providers.astronomy import compute_lunar_details as _compute_lunar_details
from app.providers.astronomy import compute_solunar_times as _compute_solunar_times
from app.providers.astronomy import compute_sun_times as _compute_sun_times
from app.providers.astronomy import compute_twilight_times as _compute_twilight_times
from app.providers.locations import ResolvedLocation, monthly_water_temps_for_region
from app.providers.ndbc import BuoyObservation, fetch_buoy_observation
from app.providers.noaa_coops import WaterTemperatureReading, fetch_water_temperature
from app.providers.nws import (
    MarineZoneConditions,
    WeatherAlert,
    fetch_marine_zone_conditions,
    fetch_point_alerts,
)

_SOURCE_NWS_MARINE_ZONE = "nws:marine_zone"
_SOURCE_NOAA_COOPS_WATER_TEMP = "noaa_coops:water_temperature"
_SOURCE_NDBC_BUOY = "ndbc:buoy"

T = TypeVar("T")


@dataclass
class _FetchResult(Generic[T]):
    """The outcome of one fallible source fetch: exactly one of `value`
    (success) or `error` (a human-readable reason, from either a
    `ProviderError` or "this location has no station assigned for this
    source") is set.
    """

    value: T | None
    error: str | None


class ForecastConditions(BaseModel):
    """The sprint-21-designed shape of `ForecastEnvelope.conditions`.

    Each provider's raw normalized output is kept as its own field
    rather than merged into one reconciled "the wind is X" value —
    reconciling multiple sources into a single defensible number is
    forecast *scoring*'s job (sprint 22), not assembly's.
    """

    water_temperature: Observation
    marine_zone_wind: ObservationRange | None
    marine_zone_wave: ObservationRange | None
    buoy: NormalizedBuoy | None
    alerts: list[WeatherAlert]
    sun_times: SunTimes
    twilight: TwilightTimes
    lunar: LunarDetails
    solunar: SolunarTimes


def _to_domain_location(location: ResolvedLocation) -> Location:
    station_ids = [
        s
        for s in (
            location.coops_station,
            location.water_temp_station,
            *location.ndbc_stations,
        )
        if s
    ]
    return Location(
        id=location.id,
        label=location.name,
        lat=location.lat,
        lng=location.lng,
        timezone=location.timezone,
        marine_zone=location.nws_zone or None,
        station_ids=list(dict.fromkeys(station_ids)),
    )


async def _fetch_marine_zone(
    client: BoundedHTTPClient, zone: str
) -> _FetchResult[MarineZoneConditions]:
    if not zone:
        return _FetchResult(
            value=None, error="no NWS marine zone assigned to this location"
        )
    try:
        value = await fetch_marine_zone_conditions(client, zone)
    except ProviderError as exc:
        return _FetchResult(value=None, error=str(exc))
    return _FetchResult(value=value, error=None)


async def _fetch_water_temp(
    client: BoundedHTTPClient, station_id: str
) -> _FetchResult[WaterTemperatureReading]:
    if not station_id:
        return _FetchResult(
            value=None,
            error="no CO-OPS water-temperature station assigned to this location",
        )
    try:
        value = await fetch_water_temperature(client, station_id)
    except ProviderError as exc:
        return _FetchResult(value=None, error=str(exc))
    return _FetchResult(value=value, error=None)


async def _fetch_buoy(
    client: BoundedHTTPClient, station_ids: list[str]
) -> _FetchResult[BuoyObservation]:
    if not station_ids:
        return _FetchResult(value=None, error="no NDBC buoy assigned to this location")
    try:
        value = await fetch_buoy_observation(client, station_ids[0])
    except ProviderError as exc:
        return _FetchResult(value=None, error=str(exc))
    return _FetchResult(value=value, error=None)


def _source_status(name: str, result: _FetchResult[T], now: datetime) -> SourceStatus:
    if result.error is None:
        return SourceStatus(provider=name, state=SourceState.OK, as_of=now)
    return SourceStatus(
        provider=name, state=SourceState.UNAVAILABLE, as_of=now, detail=result.error
    )


async def assemble_forecast(
    location: ResolvedLocation,
    client: BoundedHTTPClient,
    water_temp_profiles: dict[str, dict[int, float]],
    *,
    now: datetime,
) -> ForecastEnvelope:
    marine_zone_result, water_temp_result, buoy_result, alerts = await asyncio.gather(
        _fetch_marine_zone(client, location.nws_zone),
        _fetch_water_temp(client, location.water_temp_station),
        _fetch_buoy(client, location.ndbc_stations),
        fetch_point_alerts(client, location.lat, location.lng),
    )

    sources = [
        _source_status(_SOURCE_NWS_MARINE_ZONE, marine_zone_result, now),
        _source_status(_SOURCE_NOAA_COOPS_WATER_TEMP, water_temp_result, now),
        _source_status(_SOURCE_NDBC_BUOY, buoy_result, now),
    ]

    warnings: list[Warning] = []
    if marine_zone_result.error is not None:
        warnings.append(
            Warning(
                code="source_unavailable:nws_marine_zone",
                message=f"NWS marine zone forecast unavailable: {marine_zone_result.error}",
                severity=WarningSeverity.ADVISORY,
            )
        )
    if buoy_result.error is not None:
        warnings.append(
            Warning(
                code="source_unavailable:ndbc_buoy",
                message=f"NDBC buoy observation unavailable: {buoy_result.error}",
                severity=WarningSeverity.ADVISORY,
            )
        )

    if water_temp_result.value is not None:
        water_temperature = normalize_water_temperature(
            water_temp_result.value, station_id=location.water_temp_station
        )
    else:
        fallback_temps = monthly_water_temps_for_region(
            location.temp_region, location.temp_offset, water_temp_profiles
        )
        water_temperature = Observation(
            value=fallback_temps[now.month],
            unit="degF",
            provider="noaa_coops",
            station_id=None,
            observed_at=now,
            is_fallback=True,
            fallback_reason=(
                f"live water temperature unavailable ({water_temp_result.error}); "
                f"substituted the {location.temp_region} monthly average"
            ),
        )
        warnings.append(
            Warning(
                code="fallback:water_temperature",
                message=(
                    "Live water temperature unavailable; showing the monthly "
                    "average instead."
                ),
                severity=WarningSeverity.ADVISORY,
            )
        )

    marine_zone_conditions = marine_zone_result.value
    buoy_observation = buoy_result.value

    conditions = ForecastConditions(
        water_temperature=water_temperature,
        marine_zone_wind=(
            normalize_marine_zone_wind_range(
                marine_zone_conditions, zone=location.nws_zone, observed_at=now
            )
            if marine_zone_conditions is not None
            else None
        ),
        marine_zone_wave=(
            normalize_marine_zone_wave_range(
                marine_zone_conditions, zone=location.nws_zone, observed_at=now
            )
            if marine_zone_conditions is not None
            else None
        ),
        buoy=(
            normalize_buoy_readings(
                buoy_observation, station_id=location.ndbc_stations[0], observed_at=now
            )
            if buoy_observation is not None
            else None
        ),
        alerts=alerts,
        sun_times=_compute_sun_times(
            now, location.lat, location.lng, location.timezone
        ),
        twilight=_compute_twilight_times(
            now, location.lat, location.lng, location.timezone
        ),
        lunar=_compute_lunar_details(now, location.lng, location.timezone),
        solunar=_compute_solunar_times(
            now, location.lat, location.lng, location.timezone
        ),
    )

    wind_wave_available = conditions.marine_zone_wind is not None or (
        conditions.buoy is not None and conditions.buoy.wind_speed is not None
    )
    all_three_live = (
        marine_zone_result.error is None
        and water_temp_result.error is None
        and buoy_result.error is None
    )

    if wind_wave_available:
        state = ForecastState.FRESH
        confidence_level = (
            ConfidenceLevel.HIGH if all_three_live else ConfidenceLevel.MEDIUM
        )
    else:
        state = ForecastState.PARTIAL
        confidence_level = ConfidenceLevel.LOW

    confidence_reasons = [w.code for w in warnings]

    return ForecastEnvelope(
        location=_to_domain_location(location),
        generated_at=now,
        state=state,
        sources=sources,
        confidence=Confidence(level=confidence_level, reasons=confidence_reasons),
        warnings=warnings,
        conditions=conditions.model_dump(mode="json"),
        tides=None,
        hourly_outlook=None,
        recommendations=None,
    )
