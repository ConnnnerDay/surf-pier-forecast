"""Canonical forecast domain models (sprint 11).

The shared vocabulary named in docs/architecture.md's ADR-003 ("canonical
location, observation, source, forecast, confidence, and warning models")
and docs/product-definition.md's "Product vocabulary" and "Attribution and
evidence rules" sections. These are typed contracts, not an implementation:
`ForecastEnvelope.conditions`/`tides`/`hourly_outlook`/`recommendations` are
deliberately left as opaque payloads here. Fleshing those out is Phase 2's
job for the sprints that own them (21 forecast assembly, 22 scoring, 34
tides/timing, 35 fishing guidance) — each behind its own characterization
tests per the canonical technical contract ("port Python logic only after
characterization tests capture defensible behavior"). Designing those
shapes now, without that evidence, would be exactly the kind of
unsupported invention this project is recovering from. `conditions`
(`app.domain.assembly.ForecastConditions`), `tides`
(`app.domain.assembly.ForecastTides`), and `hourly_outlook`
(`app.domain.timing.HourlyOutlook`) are now designed and populated by
those modules; this field stays an opaque `dict` here regardless, since
the typed shape belongs to the module that owns it, not this one.
`recommendations` remains unpopulated, sprint 35's job.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ForecastState(str, Enum):
    """docs/product-definition.md § Product vocabulary § Forecast state."""

    FRESH = "fresh"
    STALE = "stale"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class SourceState(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WarningSeverity(str, Enum):
    INFO = "info"
    ADVISORY = "advisory"
    CRITICAL = "critical"


class Location(BaseModel):
    """A resolved location. Coordinates are rounded before storage/transit
    per docs/CANONICAL_ROADMAP.md's data-minimization requirement — rounding
    itself happens at resolution time (sprint 18/19), not here.
    """

    id: str
    label: str
    lat: float = Field(description="Rounded, not precise — see module docstring.")
    lng: float = Field(description="Rounded, not precise — see module docstring.")
    timezone: str
    marine_zone: str | None = None
    station_ids: list[str] = Field(default_factory=list)


class Observation(BaseModel):
    """A single attributed value, per docs/product-definition.md's
    attribution rules: every decision-relevant value keeps its provider,
    station, observed/issued/valid time, unit, freshness, and fallback
    status.
    """

    value: float
    unit: str
    provider: str
    station_id: str | None = None
    observed_at: datetime
    is_fallback: bool = False
    fallback_reason: str | None = None


class SourceStatus(BaseModel):
    provider: str
    state: SourceState
    as_of: datetime
    detail: str | None = None


class Confidence(BaseModel):
    level: ConfidenceLevel
    reasons: list[str] = Field(default_factory=list)


class Warning(BaseModel):
    code: str
    message: str
    severity: WarningSeverity


class ForecastEnvelope(BaseModel):
    """The top-level response shape named in docs/CANONICAL_ROADMAP.md's
    "Required API surface" section. `conditions`, `tides`, `hourly_outlook`,
    and `recommendations` are opaque here on purpose — see module docstring.
    """

    location: Location
    generated_at: datetime
    state: ForecastState
    sources: list[SourceStatus] = Field(default_factory=list)
    confidence: Confidence
    warnings: list[Warning] = Field(default_factory=list)
    conditions: dict[str, Any] | None = None
    tides: dict[str, Any] | None = None
    hourly_outlook: dict[str, Any] | None = None
    recommendations: dict[str, Any] | None = None
