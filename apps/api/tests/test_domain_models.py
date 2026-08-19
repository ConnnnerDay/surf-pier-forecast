"""Round-trip serialization and schema-snapshot tests for the sprint-11
canonical domain models (app/domain/models.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
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

SNAPSHOT_DIR = Path(__file__).resolve().parent / "schema_snapshots"

SAMPLE_LOCATION = Location(
    id="pt_34.21_-77.80",
    label="Wrightsville Beach, NC",
    lat=34.21,
    lng=-77.80,
    timezone="America/New_York",
    marine_zone="AMZ254",
    station_ids=["8658163", "41110"],
)

SAMPLE_OBSERVATION = Observation(
    value=68.5,
    unit="degF",
    provider="NOAA CO-OPS",
    station_id="8658163",
    observed_at=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
    is_fallback=False,
)

SAMPLE_SOURCE_STATUS = SourceStatus(
    provider="NWS",
    state=SourceState.DEGRADED,
    as_of=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
    detail="grid endpoint returned 503, using cached alert only",
)

SAMPLE_CONFIDENCE = Confidence(
    level=ConfidenceLevel.MEDIUM,
    reasons=["NDBC buoy 41110 observation is 3 hours old"],
)

SAMPLE_WARNING = Warning(
    code="small_craft_advisory",
    message="Small craft advisory in effect until 6 PM",
    severity=WarningSeverity.ADVISORY,
)

SAMPLE_ENVELOPE = ForecastEnvelope(
    location=SAMPLE_LOCATION,
    generated_at=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
    state=ForecastState.PARTIAL,
    sources=[SAMPLE_SOURCE_STATUS],
    confidence=SAMPLE_CONFIDENCE,
    warnings=[SAMPLE_WARNING],
)

ROUND_TRIP_CASES: list[tuple[str, type[BaseModel], BaseModel]] = [
    ("Location", Location, SAMPLE_LOCATION),
    ("Observation", Observation, SAMPLE_OBSERVATION),
    ("SourceStatus", SourceStatus, SAMPLE_SOURCE_STATUS),
    ("Confidence", Confidence, SAMPLE_CONFIDENCE),
    ("Warning", Warning, SAMPLE_WARNING),
    ("ForecastEnvelope", ForecastEnvelope, SAMPLE_ENVELOPE),
]


@pytest.mark.parametrize(
    "name,model_cls,instance", ROUND_TRIP_CASES, ids=[c[0] for c in ROUND_TRIP_CASES]
)
def test_serialization_round_trip(
    name: str, model_cls: type[BaseModel], instance: BaseModel
) -> None:
    dumped = instance.model_dump_json()
    restored = model_cls.model_validate_json(dumped)
    assert restored == instance


@pytest.mark.parametrize(
    "name,model_cls,instance", ROUND_TRIP_CASES, ids=[c[0] for c in ROUND_TRIP_CASES]
)
def test_schema_matches_snapshot(
    name: str, model_cls: type[BaseModel], instance: BaseModel
) -> None:
    snapshot_path = SNAPSHOT_DIR / f"{name}.json"
    assert snapshot_path.exists(), (
        f"missing schema snapshot for {name} — run "
        "`python -m scripts.generate_schema_snapshots` from apps/api and commit the result"
    )
    expected = json.loads(snapshot_path.read_text())
    actual = model_cls.model_json_schema()
    assert actual == expected, (
        f"{name}'s JSON schema drifted from the committed snapshot. If this is a "
        "deliberate model change, run `python -m scripts.generate_schema_snapshots` "
        "from apps/api, review the diff, and commit it."
    )


def test_forecast_envelope_optional_sections_default_to_none() -> None:
    assert SAMPLE_ENVELOPE.conditions is None
    assert SAMPLE_ENVELOPE.tides is None
    assert SAMPLE_ENVELOPE.hourly_outlook is None
    assert SAMPLE_ENVELOPE.recommendations is None
