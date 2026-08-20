"""Observation normalization (sprint 20).

Wraps sprints 13-15's provider-specific typed outputs (NWS, NOAA CO-OPS,
NDBC) into `app.domain.models.Observation` — the canonical,
provider-agnostic vocabulary named in docs/architecture.md's ADR-003
("every decision-relevant value keeps its provider, station,
observed/issued/valid time, unit, freshness, and fallback status").

This is a domain-layer concern, not a provider-adapter one: the
providers (sprints 13-15) know how to fetch and parse a specific wire
format; normalization knows how to turn several different provider
shapes into one shared vocabulary so forecast assembly (sprint 21) can
reason about "a value with known provenance" uniformly, regardless of
which provider it came from.

Canonical units: knots (`kt`) for wind speed, feet (`ft`) for wave/tide
height, degrees Fahrenheit (`degF`) for temperature, millibars (`mb`)
for pressure. All three providers already emit these units directly
(a deliberate design choice made when they were built), so no unit
*conversion* happens here — "canonical units" in this sprint's scope
means consistently labeling values with the unit string the rest of the
system can rely on, not converting between unit systems.

Scope and what's deliberately not normalized here:

- Astronomy (sprint 16) is not wrapped into `Observation`: its outputs
  (sunrise/sunset, twilight boundaries, lunar details, solunar periods)
  are computed, not measured, so they have no "provider fetched a value
  with some freshness/fallback status" story to attribute — they're
  already fully typed and self-contained.
- Categorical fields (NWS/NDBC wind *direction*, e.g. `"SW"`) are not
  normalized into `Observation`, whose `value` is a `float` — direction
  has no meaningful canonical unit to pair it with.
- NWS's marine-zone wind/wave data is a 24-hour forecast *range*, not a
  single measured point, so it normalizes to an `ObservationRange`
  (paired low/high `Observation`s, both carrying the same provider/
  zone/observed_at) rather than collapsing to one value — picking a
  single representative number from a range is an interpretation
  decision that belongs to forecast assembly, which has the context
  (what the range is being used for) to make it, not this sprint.
- `is_fallback`/`fallback_reason` are left at their `Observation`
  defaults (`False`/`None`) by every function here: these are live
  provider readings, not fallback substitutions. Deciding when to
  substitute a fallback value (e.g. a historical monthly average when a
  live fetch fails) and marking it as such is forecast-assembly's job
  (sprint 21) — repeatedly deferred there since sprint 14, for the same
  reason: assembly has the full picture of which sources succeeded that
  a single normalization function doesn't.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.domain.models import Observation
from app.providers.ndbc import BuoyObservation
from app.providers.noaa_coops import TidePrediction, WaterTemperatureReading
from app.providers.nws import MarineZoneConditions

PROVIDER_NWS = "nws"
PROVIDER_NOAA_COOPS = "noaa_coops"
PROVIDER_NDBC = "ndbc"

UNIT_KNOTS = "kt"
UNIT_FEET = "ft"
UNIT_DEGREES_FAHRENHEIT = "degF"
UNIT_MILLIBARS = "mb"


class ObservationRange(BaseModel):
    """A forecast range (e.g. "10 to 15 kt") as two Observations sharing
    the same provider/station/observed_at, rather than one lossily
    collapsed value.
    """

    low: Observation
    high: Observation


class NormalizedBuoyReadings(BaseModel):
    """Mirrors `BuoyObservation`'s field-by-field optionality: a `None`
    here means that field wasn't in the station's feed or had no usable
    recent reading, same as in the source `BuoyObservation`.
    """

    wind_speed: Observation | None
    wind_gust: Observation | None
    wave_height: Observation | None
    pressure: Observation | None


def normalize_water_temperature(
    reading: WaterTemperatureReading, *, station_id: str
) -> Observation:
    return Observation(
        value=reading.value_f,
        unit=UNIT_DEGREES_FAHRENHEIT,
        provider=PROVIDER_NOAA_COOPS,
        station_id=station_id,
        observed_at=reading.observed_at,
    )


def normalize_tide_prediction(
    prediction: TidePrediction, *, station_id: str
) -> Observation:
    return Observation(
        value=prediction.height_ft,
        unit=UNIT_FEET,
        provider=PROVIDER_NOAA_COOPS,
        station_id=station_id,
        observed_at=prediction.time,
    )


def normalize_marine_zone_wind_range(
    conditions: MarineZoneConditions, *, zone: str, observed_at: datetime
) -> ObservationRange | None:
    """`None` if the source text had no parseable wind speed."""
    if conditions.wind_low_kt is None or conditions.wind_high_kt is None:
        return None
    return ObservationRange(
        low=Observation(
            value=conditions.wind_low_kt,
            unit=UNIT_KNOTS,
            provider=PROVIDER_NWS,
            station_id=zone,
            observed_at=observed_at,
        ),
        high=Observation(
            value=conditions.wind_high_kt,
            unit=UNIT_KNOTS,
            provider=PROVIDER_NWS,
            station_id=zone,
            observed_at=observed_at,
        ),
    )


def normalize_marine_zone_wave_range(
    conditions: MarineZoneConditions, *, zone: str, observed_at: datetime
) -> ObservationRange | None:
    """`None` if the source text had no parseable wave height."""
    if conditions.wave_low_ft is None or conditions.wave_high_ft is None:
        return None
    return ObservationRange(
        low=Observation(
            value=conditions.wave_low_ft,
            unit=UNIT_FEET,
            provider=PROVIDER_NWS,
            station_id=zone,
            observed_at=observed_at,
        ),
        high=Observation(
            value=conditions.wave_high_ft,
            unit=UNIT_FEET,
            provider=PROVIDER_NWS,
            station_id=zone,
            observed_at=observed_at,
        ),
    )


def normalize_buoy_readings(
    obs: BuoyObservation, *, station_id: str, observed_at: datetime
) -> NormalizedBuoyReadings:
    def obs_or_none(value: float | None, unit: str) -> Observation | None:
        if value is None:
            return None
        return Observation(
            value=value,
            unit=unit,
            provider=PROVIDER_NDBC,
            station_id=station_id,
            observed_at=observed_at,
        )

    return NormalizedBuoyReadings(
        wind_speed=obs_or_none(obs.wind_speed_kt, UNIT_KNOTS),
        wind_gust=obs_or_none(obs.wind_gust_kt, UNIT_KNOTS),
        wave_height=obs_or_none(obs.wave_height_ft, UNIT_FEET),
        pressure=obs_or_none(obs.pressure_mb, UNIT_MILLIBARS),
    )
