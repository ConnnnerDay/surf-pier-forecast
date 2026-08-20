"""Tests for app.domain.confidence.assess_confidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.confidence import AgedObservation, StationDistance, assess_confidence
from app.domain.models import (
    Confidence,
    ConfidenceLevel,
    Observation,
    SourceState,
    SourceStatus,
)

_NOW = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)


def _source(provider: str, state: SourceState) -> SourceStatus:
    return SourceStatus(provider=provider, state=state, as_of=_NOW)


def _observation(*, age_hours: float = 0.0, is_fallback: bool = False) -> Observation:
    return Observation(
        value=70.0,
        unit="degF",
        provider="noaa_coops",
        station_id="8658163",
        observed_at=_NOW - timedelta(hours=age_hours),
        is_fallback=is_fallback,
        fallback_reason="test" if is_fallback else None,
    )


class TestBaseline:
    def test_no_inputs_is_high_with_no_reasons(self) -> None:
        result = assess_confidence([], now=_NOW)
        assert result.level == ConfidenceLevel.HIGH
        assert result.reasons == []

    def test_all_sources_ok_is_high_with_no_reasons(self) -> None:
        result = assess_confidence(
            [
                _source("nws:marine_zone", SourceState.OK),
                _source("noaa_coops:water_temperature", SourceState.OK),
                _source("ndbc:buoy", SourceState.OK),
            ],
            now=_NOW,
        )
        assert result.level == ConfidenceLevel.HIGH
        assert result.reasons == []

    def test_returns_confidence_model(self) -> None:
        result = assess_confidence([], now=_NOW)
        assert isinstance(result, Confidence)


class TestSourceCoverage:
    def test_one_unavailable_source_degrades_to_medium(self) -> None:
        result = assess_confidence(
            [_source("nws:marine_zone", SourceState.UNAVAILABLE)], now=_NOW
        )
        assert result.level == ConfidenceLevel.MEDIUM
        assert result.reasons == ["source_unavailable:nws:marine_zone"]

    def test_two_unavailable_sources_degrades_to_low(self) -> None:
        result = assess_confidence(
            [
                _source("nws:marine_zone", SourceState.UNAVAILABLE),
                _source("ndbc:buoy", SourceState.UNAVAILABLE),
            ],
            now=_NOW,
        )
        assert result.level == ConfidenceLevel.LOW

    def test_degraded_source_penalized_less_than_unavailable(self) -> None:
        degraded = assess_confidence(
            [_source("nws:marine_zone", SourceState.DEGRADED)], now=_NOW
        )
        unavailable = assess_confidence(
            [_source("nws:marine_zone", SourceState.UNAVAILABLE)], now=_NOW
        )
        assert degraded.level == ConfidenceLevel.HIGH
        assert unavailable.level == ConfidenceLevel.MEDIUM
        assert degraded.reasons == ["source_degraded:nws:marine_zone"]

    def test_ok_source_contributes_no_reason(self) -> None:
        result = assess_confidence(
            [_source("nws:marine_zone", SourceState.OK)], now=_NOW
        )
        assert result.reasons == []


class TestObservationAge:
    def test_fresh_observation_no_penalty(self) -> None:
        result = assess_confidence(
            [],
            now=_NOW,
            observations=[AgedObservation("wt", _observation(age_hours=0.5))],
        )
        assert result.level == ConfidenceLevel.HIGH
        assert result.reasons == []

    def test_aging_observation_penalized(self) -> None:
        result = assess_confidence(
            [],
            now=_NOW,
            observations=[AgedObservation("wt", _observation(age_hours=3.0))],
        )
        assert result.reasons == ["aging_observation:wt"]
        assert result.level == ConfidenceLevel.HIGH

    def test_stale_observation_penalized_more_than_aging(self) -> None:
        result = assess_confidence(
            [],
            now=_NOW,
            observations=[AgedObservation("wt", _observation(age_hours=6.0))],
        )
        assert result.reasons == ["stale_observation:wt"]
        assert result.level == ConfidenceLevel.MEDIUM

    def test_stale_and_aging_are_mutually_exclusive(self) -> None:
        result = assess_confidence(
            [],
            now=_NOW,
            observations=[AgedObservation("wt", _observation(age_hours=6.0))],
        )
        assert "aging_observation:wt" not in result.reasons

    @pytest.mark.parametrize("age_hours", [2.0, 5.0])
    def test_boundary_ages_trigger_their_band(self, age_hours: float) -> None:
        result = assess_confidence(
            [],
            now=_NOW,
            observations=[AgedObservation("wt", _observation(age_hours=age_hours))],
        )
        assert result.reasons


class TestFallback:
    def test_fallback_observation_penalized(self) -> None:
        result = assess_confidence(
            [],
            now=_NOW,
            observations=[AgedObservation("wt", _observation(is_fallback=True))],
        )
        assert result.reasons == ["fallback:wt"]
        assert result.level == ConfidenceLevel.HIGH

    def test_fallback_and_age_penalties_both_apply(self) -> None:
        result = assess_confidence(
            [],
            now=_NOW,
            observations=[
                AgedObservation("wt", _observation(age_hours=6.0, is_fallback=True))
            ],
        )
        assert set(result.reasons) == {"stale_observation:wt", "fallback:wt"}

    def test_live_observation_no_fallback_reason(self) -> None:
        result = assess_confidence(
            [],
            now=_NOW,
            observations=[AgedObservation("wt", _observation(is_fallback=False))],
        )
        assert "fallback:wt" not in result.reasons


class TestStationDistance:
    def test_nearby_station_no_penalty(self) -> None:
        result = assess_confidence(
            [], now=_NOW, station_distances=[StationDistance("ndbc:buoy", 5.0)]
        )
        assert result.level == ConfidenceLevel.HIGH
        assert result.reasons == []

    def test_far_station_penalized(self) -> None:
        result = assess_confidence(
            [], now=_NOW, station_distances=[StationDistance("ndbc:buoy", 30.0)]
        )
        assert result.reasons == ["far_station:ndbc:buoy"]

    def test_very_far_station_penalized_more(self) -> None:
        result = assess_confidence(
            [], now=_NOW, station_distances=[StationDistance("ndbc:buoy", 55.0)]
        )
        assert result.reasons == ["distant_station:ndbc:buoy"]
        assert result.level == ConfidenceLevel.MEDIUM

    def test_far_and_very_far_are_mutually_exclusive(self) -> None:
        result = assess_confidence(
            [], now=_NOW, station_distances=[StationDistance("ndbc:buoy", 55.0)]
        )
        assert "far_station:ndbc:buoy" not in result.reasons


class TestCombinedFactorsAndClamping:
    def test_score_clamped_to_zero_not_negative(self) -> None:
        result = assess_confidence(
            [
                _source("nws:marine_zone", SourceState.UNAVAILABLE),
                _source("noaa_coops:water_temperature", SourceState.UNAVAILABLE),
                _source("ndbc:buoy", SourceState.UNAVAILABLE),
            ],
            now=_NOW,
            observations=[
                AgedObservation("wt", _observation(age_hours=10.0, is_fallback=True))
            ],
            station_distances=[StationDistance("ndbc:buoy", 100.0)],
        )
        assert result.level == ConfidenceLevel.LOW

    def test_multiple_reasons_all_reported(self) -> None:
        result = assess_confidence(
            [_source("nws:marine_zone", SourceState.UNAVAILABLE)],
            now=_NOW,
            observations=[
                AgedObservation("wt", _observation(age_hours=6.0, is_fallback=True))
            ],
            station_distances=[StationDistance("ndbc:buoy", 55.0)],
        )
        assert set(result.reasons) == {
            "source_unavailable:nws:marine_zone",
            "stale_observation:wt",
            "fallback:wt",
            "distant_station:ndbc:buoy",
        }

    def test_multiple_observations_each_scored_independently(self) -> None:
        result = assess_confidence(
            [],
            now=_NOW,
            observations=[
                AgedObservation("wt", _observation(age_hours=0.0)),
                AgedObservation("wind", _observation(age_hours=6.0)),
            ],
        )
        assert result.reasons == ["stale_observation:wind"]
