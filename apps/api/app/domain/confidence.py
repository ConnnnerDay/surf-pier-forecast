"""Confidence model (sprint 23).

No legacy precedent: `docs/product-definition.md`'s Confidence section
("Confidence describes evidence quality... derived from source coverage,
observation age, station distance, and fallback use") is not implemented
anywhere in the legacy Flask app — `domain/forecast.py` has no confidence
concept at all. Sprint 11's `ConfidenceLevel`/`Confidence` models and
sprint 21's basic HIGH/MEDIUM/LOW-from-liveness stub are this recovery's
own design against the product contract, not a port. This sprint replaces
that interim stub with the fuller, standalone, four-factor policy sprint
21 explicitly deferred to "sprint 23."

Deliberately decoupled from `app.domain.assembly`, matching sprint 22's
scoring module's pattern: `assess_confidence` takes already-computed
per-source status, already-normalized observations (for age/fallback),
and optional station distances (for locality) as explicit parameters,
rather than reaching into `ForecastEnvelope`/`ForecastConditions`
itself. Wiring this into `assemble_forecast` in place of its interim
stub is a follow-up, not this sprint's job.

Four degrading factors, each independently scored and reported as a
plain-language reason code (`Confidence.reasons`) so the product
contract's "the interface must always show the reasons for reduced
confidence" requirement has something concrete to render:

- **Source coverage** — an `UNAVAILABLE` or `DEGRADED` `SourceStatus`
  degrades confidence per source; this generalizes sprint 21's
  all-live-or-not binary into per-source, per-severity penalties.
- **Observation age** — each observation's age against two thresholds
  (`_AGE_AGING_HOURS`/`_AGE_STALE_HOURS`); newly introduced here, since
  no `Observation` existed anywhere in this recovery before sprint 20.
- **Station distance** — each named station's distance from the
  resolved location against two thresholds (`_DISTANCE_FAR_MILES`/
  `_DISTANCE_VERY_FAR_MILES`); the "far" bound intentionally sits
  inside sprint 18's `gate_coastal_point`'s 60-mile hard cutoff — a
  station this recovery accepts as "coastal enough to use" can still
  be too far to fully trust.
- **Fallback use** — an observation flagged `is_fallback=True` degrades
  confidence independently of why its source is unavailable (the
  product-definition text lists fallback use as its own axis, not a
  restatement of source coverage): a labeled fallback is not an
  invented measurement, but it is measurably weaker evidence than a
  live reading, and both penalties can legitimately apply to the same
  observation at once.

All thresholds and point values are this sprint's own defensible
choices — there is no legacy behavior to match — documented individually
above and exercised by `apps/api/tests/test_confidence.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.models import (
    Confidence,
    ConfidenceLevel,
    Observation,
    SourceState,
    SourceStatus,
)

_STARTING_POINTS = 100.0

_SOURCE_UNAVAILABLE_PENALTY = 25.0
_SOURCE_DEGRADED_PENALTY = 10.0

_AGE_AGING_HOURS = 2.0
_AGE_STALE_HOURS = 5.0
_AGE_AGING_PENALTY = 8.0
_AGE_STALE_PENALTY = 18.0

_DISTANCE_FAR_MILES = 25.0
_DISTANCE_VERY_FAR_MILES = 50.0
_DISTANCE_FAR_PENALTY = 8.0
_DISTANCE_VERY_FAR_PENALTY = 18.0

_FALLBACK_PENALTY = 15.0

_LEVEL_HIGH_FLOOR = 85.0
_LEVEL_MEDIUM_FLOOR = 55.0


@dataclass
class AgedObservation:
    """An `Observation` paired with the label used to identify it in a
    confidence reason code (e.g. `"noaa_coops:water_temperature"`).
    """

    label: str
    observation: Observation


@dataclass
class StationDistance:
    """A named station's distance from the resolved location, in miles."""

    label: str
    miles: float


def assess_confidence(
    sources: Sequence[SourceStatus],
    *,
    now: datetime,
    observations: Sequence[AgedObservation] = (),
    station_distances: Sequence[StationDistance] = (),
) -> Confidence:
    """Score evidence quality from source coverage, observation age,
    station distance, and fallback use. Starts at 100 points and applies
    an independent penalty per degrading factor found; the total maps to
    a `ConfidenceLevel` via `_LEVEL_HIGH_FLOOR`/`_LEVEL_MEDIUM_FLOOR`.
    """
    points = _STARTING_POINTS
    reasons: list[str] = []

    for status in sources:
        if status.state is SourceState.UNAVAILABLE:
            points -= _SOURCE_UNAVAILABLE_PENALTY
            reasons.append(f"source_unavailable:{status.provider}")
        elif status.state is SourceState.DEGRADED:
            points -= _SOURCE_DEGRADED_PENALTY
            reasons.append(f"source_degraded:{status.provider}")

    for aged in observations:
        age_hours = (now - aged.observation.observed_at).total_seconds() / 3600.0
        if age_hours >= _AGE_STALE_HOURS:
            points -= _AGE_STALE_PENALTY
            reasons.append(f"stale_observation:{aged.label}")
        elif age_hours >= _AGE_AGING_HOURS:
            points -= _AGE_AGING_PENALTY
            reasons.append(f"aging_observation:{aged.label}")

        if aged.observation.is_fallback:
            points -= _FALLBACK_PENALTY
            reasons.append(f"fallback:{aged.label}")

    for station in station_distances:
        if station.miles >= _DISTANCE_VERY_FAR_MILES:
            points -= _DISTANCE_VERY_FAR_PENALTY
            reasons.append(f"distant_station:{station.label}")
        elif station.miles >= _DISTANCE_FAR_MILES:
            points -= _DISTANCE_FAR_PENALTY
            reasons.append(f"far_station:{station.label}")

    points = max(0.0, points)
    if points >= _LEVEL_HIGH_FLOOR:
        level = ConfidenceLevel.HIGH
    elif points >= _LEVEL_MEDIUM_FLOOR:
        level = ConfidenceLevel.MEDIUM
    else:
        level = ConfidenceLevel.LOW

    return Confidence(level=level, reasons=reasons)
