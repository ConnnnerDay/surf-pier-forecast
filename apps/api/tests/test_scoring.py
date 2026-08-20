"""Tests for app.domain.scoring.score_conditions and its helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.models import Observation
from app.domain.scoring import (
    ForecastScore,
    ScoreVerdict,
    onshore_offshore_dirs,
    score_conditions,
    wind_orientation_for_region,
)
from app.providers.astronomy import (
    MoonPhaseName,
    SolunarRating,
    SolunarTimes,
    SunTimes,
)

_NOW = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)


def _water_temp(value: float, *, is_fallback: bool = False) -> Observation:
    return Observation(
        value=value,
        unit="degF",
        provider="noaa_coops",
        station_id="8658163",
        observed_at=_NOW,
        is_fallback=is_fallback,
        fallback_reason="test fallback" if is_fallback else None,
    )


def _sun_times(sunrise: datetime, sunset: datetime) -> SunTimes:
    return SunTimes(sunrise=sunrise, sunset=sunset)


def _solunar(rating: SolunarRating, illumination_pct: float) -> SolunarTimes:
    return SolunarTimes(
        major_periods=[],
        minor_periods=[],
        rating=rating,
        moon_phase=MoonPhaseName.FULL,
        illumination_pct=illumination_pct,
    )


class TestUnknownWhenDataMissing:
    def test_wind_none_returns_unknown(self) -> None:
        result = score_conditions(None, (1.0, 2.0))
        assert result.score is None
        assert result.verdict == ScoreVerdict.UNKNOWN
        assert result.factors == []
        assert result.summary == ""

    def test_wave_none_returns_unknown(self) -> None:
        result = score_conditions((5.0, 10.0), None)
        assert result.score is None
        assert result.verdict == ScoreVerdict.UNKNOWN

    def test_both_none_returns_unknown(self) -> None:
        result = score_conditions(None, None)
        assert result.verdict == ScoreVerdict.UNKNOWN


class TestWindBands:
    @pytest.mark.parametrize(
        "wind_range,expected_impact",
        [
            ((3.0, 8.0), 14),
            ((8.0, 12.0), 8),
            ((12.0, 16.0), 2),
            ((16.0, 20.0), -8),
            ((20.0, 25.0), -16),
            ((25.0, 30.0), -26),
        ],
    )
    def test_wind_band_impact(
        self, wind_range: tuple[float, float], expected_impact: int
    ) -> None:
        result = score_conditions(wind_range, (0.0, 1.0))
        wind_factors = [f for f in result.factors if "kt)" in f.description]
        assert any(f.impact == expected_impact for f in wind_factors)


class TestWaveBands:
    @pytest.mark.parametrize(
        "wave_range,expected_impact",
        [
            ((0.5, 1.5), 10),
            ((1.5, 3.0), 6),
            ((3.0, 5.0), -4),
            ((5.0, 7.0), -12),
            ((7.0, 10.0), -22),
        ],
    )
    def test_wave_band_impact(
        self, wave_range: tuple[float, float], expected_impact: int
    ) -> None:
        result = score_conditions((0.0, 1.0), wave_range)
        wave_factors = [f for f in result.factors if "surf" in f.description.lower()]
        assert any(f.impact == expected_impact for f in wave_factors)


class TestWindDirection:
    def test_offshore_wind_bonus_east_coast(self) -> None:
        result = score_conditions(
            (5.0, 8.0), (1.0, 2.0), wind_direction="W", coast="east"
        )
        assert any(f.impact == 4 for f in result.factors)

    def test_onshore_wind_penalty_east_coast(self) -> None:
        result = score_conditions(
            (5.0, 8.0), (1.0, 2.0), wind_direction="E", coast="east"
        )
        assert any(f.impact == -4 for f in result.factors)

    def test_no_direction_no_bonus_or_penalty(self) -> None:
        result = score_conditions((5.0, 8.0), (1.0, 2.0))
        assert not any(
            "offshore wind" in f.description or "Onshore wind" in f.description
            for f in result.factors
        )

    def test_hawaii_orientation_never_bonuses_or_penalizes(self) -> None:
        for direction in ("N", "S", "E", "W", "NE", "SW"):
            result = score_conditions(
                (5.0, 8.0), (1.0, 2.0), wind_direction=direction, coast="hawaii"
            )
            assert not any(
                "offshore wind" in f.description or "Onshore wind" in f.description
                for f in result.factors
            )


class TestWindOrientationForRegion:
    @pytest.mark.parametrize(
        "conditions_region,expected",
        [
            ("atlantic_mid", "east"),
            ("atlantic_north", "east"),
            ("atlantic_south", "east"),
            ("pacific", "west"),
            ("pacific_south", "west"),
            ("gulf", "gulf"),
            ("hawaii_conditions", "hawaii"),
            ("", "east"),
        ],
    )
    def test_region_maps_to_orientation(
        self, conditions_region: str, expected: str
    ) -> None:
        assert wind_orientation_for_region(conditions_region) == expected


class TestOnshoreOffshoreDirs:
    @pytest.mark.parametrize("orientation", ["east", "west", "gulf"])
    def test_onshore_offshore_disjoint_and_nonempty(self, orientation: str) -> None:
        onshore, offshore = onshore_offshore_dirs(orientation)
        assert onshore
        assert offshore
        assert onshore.isdisjoint(offshore)

    def test_hawaii_both_empty(self) -> None:
        onshore, offshore = onshore_offshore_dirs("hawaii")
        assert onshore == set()
        assert offshore == set()

    def test_unknown_orientation_falls_back_to_east(self) -> None:
        assert onshore_offshore_dirs("bogus") == onshore_offshore_dirs("east")


class TestWaterTemperature:
    def test_ideal_band_bonus(self) -> None:
        result = score_conditions(
            (5.0, 8.0), (1.0, 2.0), water_temperature=_water_temp(70.0)
        )
        assert any(f.impact == 6 for f in result.factors)
        # Live (non-fallback) reading adds +1, folded into the baseline —
        # not its own factor, so check it moved the score instead.
        baseline_result = score_conditions((5.0, 8.0), (1.0, 2.0))
        assert result.score is not None and baseline_result.score is not None
        assert result.score == baseline_result.score + 6 + 1

    def test_fair_band_bonus(self) -> None:
        result = score_conditions(
            (5.0, 8.0), (1.0, 2.0), water_temperature=_water_temp(52.0)
        )
        assert any(f.impact == 2 for f in result.factors)

    def test_off_peak_penalty(self) -> None:
        result = score_conditions(
            (5.0, 8.0), (1.0, 2.0), water_temperature=_water_temp(40.0)
        )
        assert any(f.impact == -4 for f in result.factors)

    def test_fallback_reading_does_not_get_live_bonus(self) -> None:
        live = score_conditions(
            (5.0, 8.0),
            (1.0, 2.0),
            water_temperature=_water_temp(70.0, is_fallback=False),
        )
        fallback = score_conditions(
            (5.0, 8.0),
            (1.0, 2.0),
            water_temperature=_water_temp(70.0, is_fallback=True),
        )
        assert live.score is not None and fallback.score is not None
        assert live.score == fallback.score + 1

    def test_no_water_temperature_no_effect(self) -> None:
        result = score_conditions((5.0, 8.0), (1.0, 2.0))
        assert not any("water temp" in f.description.lower() for f in result.factors)


class TestDawnDuskWindow:
    def test_at_sunrise_gets_dawn_bonus(self) -> None:
        sunrise = datetime(2024, 7, 15, 6, 0, tzinfo=UTC)
        sunset = datetime(2024, 7, 15, 20, 0, tzinfo=UTC)
        result = score_conditions(
            (5.0, 8.0),
            (1.0, 2.0),
            sun_times=_sun_times(sunrise, sunset),
            now=sunrise,
        )
        assert any(f.description == "Dawn bite window" for f in result.factors)

    def test_at_sunset_gets_dusk_bonus(self) -> None:
        sunrise = datetime(2024, 7, 15, 6, 0, tzinfo=UTC)
        sunset = datetime(2024, 7, 15, 20, 0, tzinfo=UTC)
        result = score_conditions(
            (5.0, 8.0),
            (1.0, 2.0),
            sun_times=_sun_times(sunrise, sunset),
            now=sunset,
        )
        assert any(f.description == "Dusk bite window" for f in result.factors)

    def test_midday_gets_neither(self) -> None:
        sunrise = datetime(2024, 7, 15, 6, 0, tzinfo=UTC)
        sunset = datetime(2024, 7, 15, 20, 0, tzinfo=UTC)
        midday = datetime(2024, 7, 15, 13, 0, tzinfo=UTC)
        result = score_conditions(
            (5.0, 8.0),
            (1.0, 2.0),
            sun_times=_sun_times(sunrise, sunset),
            now=midday,
        )
        assert not any("bite window" in f.description for f in result.factors)

    def test_missing_now_or_sun_times_no_effect(self) -> None:
        sunrise = datetime(2024, 7, 15, 6, 0, tzinfo=UTC)
        sunset = datetime(2024, 7, 15, 20, 0, tzinfo=UTC)
        no_now = score_conditions(
            (5.0, 8.0), (1.0, 2.0), sun_times=_sun_times(sunrise, sunset)
        )
        no_sun_times = score_conditions((5.0, 8.0), (1.0, 2.0), now=sunrise)
        assert not any("bite window" in f.description for f in no_now.factors)
        assert not any("bite window" in f.description for f in no_sun_times.factors)


class TestSolunar:
    def test_excellent_rating_bonus(self) -> None:
        result = score_conditions(
            (5.0, 8.0), (1.0, 2.0), solunar=_solunar(SolunarRating.EXCELLENT, 50.0)
        )
        assert any(
            f.description == "Major feeding window (solunar)" for f in result.factors
        )

    def test_good_rating_bonus(self) -> None:
        result = score_conditions(
            (5.0, 8.0), (1.0, 2.0), solunar=_solunar(SolunarRating.GOOD, 50.0)
        )
        assert any(f.description == "Good solunar period" for f in result.factors)

    def test_poor_rating_penalty(self) -> None:
        result = score_conditions(
            (5.0, 8.0), (1.0, 2.0), solunar=_solunar(SolunarRating.POOR, 50.0)
        )
        assert any(f.description == "Weak solunar period" for f in result.factors)

    def test_fair_rating_no_factor(self) -> None:
        result = score_conditions(
            (5.0, 8.0), (1.0, 2.0), solunar=_solunar(SolunarRating.FAIR, 50.0)
        )
        assert not any(
            f.description
            in {
                "Major feeding window (solunar)",
                "Good solunar period",
                "Weak solunar period",
            }
            for f in result.factors
        )

    def test_illumination_bonus_moves_score(self) -> None:
        mid_illum = score_conditions(
            (5.0, 8.0), (1.0, 2.0), solunar=_solunar(SolunarRating.FAIR, 60.0)
        )
        extreme_illum = score_conditions(
            (5.0, 8.0), (1.0, 2.0), solunar=_solunar(SolunarRating.FAIR, 2.0)
        )
        no_solunar = score_conditions((5.0, 8.0), (1.0, 2.0))
        assert mid_illum.score is not None and no_solunar.score is not None
        assert extreme_illum.score is not None
        assert mid_illum.score == no_solunar.score + 2
        assert extreme_illum.score == no_solunar.score + 1

    def test_no_solunar_no_effect(self) -> None:
        result = score_conditions((5.0, 8.0), (1.0, 2.0))
        baseline = score_conditions((5.0, 8.0), (1.0, 2.0))
        assert result.score == baseline.score


class TestVerdictTiers:
    def test_excellent_tier(self) -> None:
        # Wind+wave alone (50+14+10=74) can't clear the 80 Excellent floor;
        # add the other real-world bonuses that legitimately stack.
        result = score_conditions(
            (0.0, 3.0),
            (0.0, 0.5),
            wind_direction="W",
            coast="east",
            water_temperature=_water_temp(70.0),
        )
        assert result.verdict == ScoreVerdict.EXCELLENT

    @pytest.mark.parametrize(
        "wind_range,wave_range,expected_verdict",
        [
            ((10.0, 12.0), (0.0, 1.0), ScoreVerdict.GOOD),
            ((14.0, 16.0), (2.0, 3.0), ScoreVerdict.FAIR),
            ((18.0, 20.0), (4.0, 5.0), ScoreVerdict.CHALLENGING),
            ((28.0, 30.0), (8.0, 10.0), ScoreVerdict.POOR),
        ],
    )
    def test_verdict_boundaries(
        self,
        wind_range: tuple[float, float],
        wave_range: tuple[float, float],
        expected_verdict: ScoreVerdict,
    ) -> None:
        result = score_conditions(wind_range, wave_range)
        assert result.verdict == expected_verdict

    def test_score_clamped_to_0_100(self) -> None:
        worst = score_conditions(
            (40.0, 50.0),
            (15.0, 20.0),
            wind_direction="E",
            coast="east",
            water_temperature=_water_temp(20.0),
            solunar=_solunar(SolunarRating.POOR, 50.0),
        )
        assert worst.score is not None
        assert 0 <= worst.score <= 100


class TestFactorSortingAndSummary:
    def test_factors_sorted_by_absolute_impact_descending(self) -> None:
        result = score_conditions(
            (25.0, 30.0),
            (0.5, 1.0),
            wind_direction="W",
            coast="east",
        )
        impacts = [abs(f.impact) for f in result.factors]
        assert impacts == sorted(impacts, reverse=True)

    def test_summary_joins_top_four_descriptions(self) -> None:
        result = score_conditions(
            (25.0, 30.0),
            (8.0, 10.0),
            wind_direction="W",
            coast="east",
            water_temperature=_water_temp(70.0),
        )
        top_four = [f.description for f in result.factors[:4]]
        assert result.summary == ", ".join(top_four)


def test_forecast_score_is_pydantic_model() -> None:
    result = score_conditions((5.0, 8.0), (1.0, 2.0))
    assert isinstance(result, ForecastScore)
