"""Forecast assembly (sprint 21; scoring and confidence wiring added
post-sprint-25).

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

`ForecastState` here is intentionally simple:

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

`Confidence` (originally a basic liveness-only stub here) now comes
from sprint 23's `assess_confidence`, see the confidence-wiring section
below.

**Scoring wiring (added after sprint 25, still not a numbered sprint).**
`ForecastConditions.score` is sprint 22's `ForecastScore`, computed here
from a source-reconciliation policy this module owns: when both NWS's
marine-zone range and the NDBC buoy report wind/wave, the marine-zone
range is used — it already expresses genuine forecast uncertainty as a
low/high spread, where the buoy gives one live point-in-time reading
turned into a degenerate zero-width range (`(v, v)`) only when NWS
didn't report. Wind direction prefers NWS's parsed direction, falling
back to the buoy's. `score_conditions`'s `coast` parameter comes from
`app.domain.scoring.wind_orientation_for_region(location.conditions_region)`.
`score` is `None` (via `score_conditions`'s own contract) only when
neither source reported wind/wave at all — the same condition that
already produces `ForecastState.PARTIAL` above.

**Confidence wiring (added after the scoring-wiring PR, still not a
numbered sprint).** `ForecastEnvelope.confidence` is now sprint 23's
`assess_confidence`, given: `sources` (per-source liveness, unchanged
from sprint 21); one `AgedObservation` for `water_temperature` (its age
and `is_fallback` flag — the only `Observation` this module produces
outside `ForecastConditions`'s per-source fields, and the only one
worth a confidence penalty on its own, since wind/wave data isn't
represented as a single `Observation` at all here); and, when
`location.anchor_miles` is set (dynamic points only —
`resolve_dynamic_location`'s nearest-anchor distance, now embedded on
`ResolvedLocation` itself rather than dropped), one `StationDistance`
labeled `"location:anchor"`. This is a deliberate simplification, not a
full per-source distance breakdown: `resolve_dynamic_location` only
returns the *single nearest* anchor's distance (whichever of the
curated neighbor, CO-OPS station, or NDBC buoy is closest), not a
distance per individual source. A curated location's `anchor_miles` is
always `None` — it *is* the named station, so no distance factor
applies. Sprint 24's `SnapshotCache` is now wired *around* this
function (not inside it) by `app.domain.forecast_cache`, keyed by
location id — this module stays a pure "assemble one envelope right
now" function, with caching, freshness, and the resulting
`ForecastState.STALE` labeling entirely that module's concern. See its
docstring for the fresh/stale/miss/expiry/fallback policy and why
`STALE` is a documented-but-dormant path given this function's own
never-raises design.

**Gridpoint wind fallback (picked up from sprint 13's own deferred
scope once Phase 2 closed out).** When neither the marine-zone forecast
nor the NDBC buoy provide a wind reading (`_reconcile_range` for wind
returns `None`), this function makes one more, *sequential* (not part
of the initial `asyncio.gather`) call to `app.providers.nws.
fetch_gridpoint_wind` — the nearest land grid point's standard
forecast, wind-only. Deliberately not fetched on every request: it's a
fourth network call this module didn't need before, and per sprint 26's
"bounded parallel calls, no duplicates" performance-budget discipline,
paying for it only in the already-degraded case (both primary wind
sources down) is the right trade — the typical case's latency and
upstream-call count are unaffected. A successful gridpoint fetch adds a
`SourceStatus` (`nws:gridpoint_wind`) and a `fallback:gridpoint_wind`
warning, feeds its range/direction into `score_conditions` (only if
`wind_direction` isn't already set from marine-zone/buoy — gridpoint is
the last-resort direction source too), and counts toward
`wind_wave_available`/`ForecastState.FRESH`. A *failed* gridpoint fetch
also adds a `SourceStatus` (state `UNAVAILABLE`) so the confidence
model sees the attempt, even though `fetch_gridpoint_wind` itself
degrades to `None` rather than raising (non-critical enrichment, not a
decision-relevant source — see that function's docstring). No wave
fallback exists here: the gridpoint forecast is land-based, so it never
has wave data to offer — which means gridpoint wind alone can rescue
`ForecastState` (FRESH instead of PARTIAL) and per-source confidence,
but *not* `score`: `score_conditions` needs both wind and wave to
produce a number (sprint 22's contract, unchanged here), so `score`
stays `None`/`UNKNOWN` whenever wave is unavailable from every source,
gridpoint included.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

from app.domain.confidence import AgedObservation, StationDistance, assess_confidence
from app.domain.models import (
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
from app.domain.scoring import (
    ForecastScore,
    score_conditions,
    wind_orientation_for_region,
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
    GridpointWindForecast,
    MarineZoneConditions,
    WeatherAlert,
    fetch_gridpoint_wind,
    fetch_marine_zone_conditions,
    fetch_point_alerts,
)

_SOURCE_NWS_MARINE_ZONE = "nws:marine_zone"
_SOURCE_NOAA_COOPS_WATER_TEMP = "noaa_coops:water_temperature"
_SOURCE_NDBC_BUOY = "ndbc:buoy"
_SOURCE_NWS_GRIDPOINT_WIND = "nws:gridpoint_wind"

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
    callers that want per-source detail still have it. `score` is the
    one reconciled, single-number exception: `_reconcile_range` (this
    module) picks one wind/wave range per the policy in the module
    docstring's scoring-wiring section, and sprint 22's
    `score_conditions` turns that into the go/no-go index.
    """

    water_temperature: Observation
    marine_zone_wind: ObservationRange | None
    marine_zone_wave: ObservationRange | None
    buoy: NormalizedBuoy | None
    gridpoint_wind: GridpointWindForecast | None
    alerts: list[WeatherAlert]
    sun_times: SunTimes
    twilight: TwilightTimes
    lunar: LunarDetails
    solunar: SolunarTimes
    score: ForecastScore


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


def _reconcile_range(
    marine_zone_range: ObservationRange | None, buoy_point: Observation | None
) -> tuple[float, float] | None:
    """Prefer the NWS marine-zone range (a genuine low/high forecast
    spread) over the NDBC buoy's single live reading (a degenerate
    zero-width range) — see the module docstring's scoring-wiring
    section for why. `None` only when neither source reported it.
    """
    if marine_zone_range is not None:
        return (marine_zone_range.low.value, marine_zone_range.high.value)
    if buoy_point is not None:
        return (buoy_point.value, buoy_point.value)
    return None


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

    marine_zone_wind = (
        normalize_marine_zone_wind_range(
            marine_zone_conditions, zone=location.nws_zone, observed_at=now
        )
        if marine_zone_conditions is not None
        else None
    )
    marine_zone_wave = (
        normalize_marine_zone_wave_range(
            marine_zone_conditions, zone=location.nws_zone, observed_at=now
        )
        if marine_zone_conditions is not None
        else None
    )
    buoy = (
        normalize_buoy_readings(
            buoy_observation, station_id=location.ndbc_stations[0], observed_at=now
        )
        if buoy_observation is not None
        else None
    )
    sun_times = _compute_sun_times(now, location.lat, location.lng, location.timezone)
    solunar = _compute_solunar_times(now, location.lat, location.lng, location.timezone)

    wind_range = _reconcile_range(marine_zone_wind, buoy.wind_speed if buoy else None)
    wave_range = _reconcile_range(marine_zone_wave, buoy.wave_height if buoy else None)
    wind_direction = (
        marine_zone_conditions.wind_direction
        if marine_zone_conditions and marine_zone_conditions.wind_direction
        else (buoy_observation.wind_direction if buoy_observation else None)
    )

    gridpoint_wind: GridpointWindForecast | None = None
    if wind_range is None:
        # Last resort: neither the marine-zone forecast nor the NDBC
        # buoy provided wind. See the module docstring's gridpoint-wind
        # section — fetched only here, not on every request, per sprint
        # 26's "bounded parallel calls, no duplicates" discipline.
        gridpoint_wind = await fetch_gridpoint_wind(client, location.lat, location.lng)
        if gridpoint_wind is not None:
            sources.append(
                SourceStatus(
                    provider=_SOURCE_NWS_GRIDPOINT_WIND, state=SourceState.OK, as_of=now
                )
            )
            if gridpoint_wind.wind_low_kt is not None and gridpoint_wind.wind_high_kt:
                wind_range = (gridpoint_wind.wind_low_kt, gridpoint_wind.wind_high_kt)
            if wind_direction is None:
                wind_direction = gridpoint_wind.wind_direction
            warnings.append(
                Warning(
                    code="fallback:gridpoint_wind",
                    message=(
                        "Marine wind data unavailable; showing the nearest land "
                        "forecast's wind instead."
                    ),
                    severity=WarningSeverity.ADVISORY,
                )
            )
        else:
            sources.append(
                SourceStatus(
                    provider=_SOURCE_NWS_GRIDPOINT_WIND,
                    state=SourceState.UNAVAILABLE,
                    as_of=now,
                    detail="NWS gridpoint wind forecast unavailable",
                )
            )

    score = score_conditions(
        wind_range,
        wave_range,
        wind_direction=wind_direction,
        water_temperature=water_temperature,
        sun_times=sun_times,
        now=now,
        solunar=solunar,
        coast=wind_orientation_for_region(location.conditions_region),
    )

    conditions = ForecastConditions(
        water_temperature=water_temperature,
        marine_zone_wind=marine_zone_wind,
        marine_zone_wave=marine_zone_wave,
        buoy=buoy,
        gridpoint_wind=gridpoint_wind,
        alerts=alerts,
        sun_times=sun_times,
        twilight=_compute_twilight_times(
            now, location.lat, location.lng, location.timezone
        ),
        lunar=_compute_lunar_details(now, location.lng, location.timezone),
        solunar=solunar,
        score=score,
    )

    wind_wave_available = (
        conditions.marine_zone_wind is not None
        or (conditions.buoy is not None and conditions.buoy.wind_speed is not None)
        or wind_range is not None
    )
    state = ForecastState.FRESH if wind_wave_available else ForecastState.PARTIAL

    confidence = assess_confidence(
        sources,
        now=now,
        observations=[
            AgedObservation("noaa_coops:water_temperature", water_temperature)
        ],
        station_distances=(
            [StationDistance("location:anchor", location.anchor_miles)]
            if location.anchor_miles is not None
            else []
        ),
    )

    return ForecastEnvelope(
        location=_to_domain_location(location),
        generated_at=now,
        state=state,
        sources=sources,
        confidence=confidence,
        warnings=warnings,
        conditions=conditions.model_dump(mode="json"),
        tides=None,
        hourly_outlook=None,
        recommendations=None,
    )
