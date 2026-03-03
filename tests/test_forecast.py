"""Tests for domain.forecast helper functions."""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from services.astro import compute_solunar_times

from domain.forecast import (
    _seasonal_averages,
    classify_conditions,
    MONTHLY_AVG_WIND,
    MONTHLY_AVG_WAVES,
    MONTHLY_AVG_WIND_DIR,
)


class TestSeasonalAverages:
    def test_returns_tuple_of_three(self):
        wind, waves, direction = _seasonal_averages(6)
        assert isinstance(wind, tuple)
        assert isinstance(waves, tuple)
        assert isinstance(direction, str)

    def test_all_months(self):
        """Every month should return valid averages."""
        for month in range(1, 13):
            wind, waves, direction = _seasonal_averages(month)
            assert len(wind) == 2
            assert len(waves) == 2
            assert wind[0] <= wind[1]
            assert waves[0] <= waves[1]
            assert direction in ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

    def test_summer_calmer_than_winter(self):
        """Summer months should generally have calmer winds/waves."""
        summer_wind, summer_waves, _ = _seasonal_averages(7)
        winter_wind, winter_waves, _ = _seasonal_averages(1)
        assert summer_wind[1] <= winter_wind[1]
        assert summer_waves[1] <= winter_waves[1]


class TestClassifyConditions:
    def test_excellent_conditions(self):
        result = classify_conditions((4, 8), (1, 1.5), wind_dir="NW", water_temp_f=68)
        assert result in {"Excellent", "Good"}

    def test_fair_to_challenging_moderate_wind(self):
        result = classify_conditions((10, 18), (2, 4), wind_dir="E", water_temp_f=52)
        assert result in {"Fair", "Challenging", "Good"}

    def test_poor_extreme(self):
        result = classify_conditions((25, 40), (6, 10), wind_dir="NE", water_temp_f=44)
        assert result == "Poor"

    def test_none_inputs(self):
        """Should handle None gracefully."""
        result = classify_conditions(None, None)
        assert isinstance(result, str)

    def test_west_coast_offshore_east_wind_bonus(self):
        good = classify_conditions((6, 10), (1, 2), wind_dir="E", coast="west", water_temp_f=65)
        bad = classify_conditions((6, 10), (1, 2), wind_dir="W", coast="west", water_temp_f=65)
        order = {"Poor": 1, "Challenging": 2, "Fair": 3, "Good": 4, "Excellent": 5}
        assert order[good] >= order[bad]


class TestMonthlyData:
    def test_wind_data_complete(self):
        assert len(MONTHLY_AVG_WIND) == 12
        for month in range(1, 13):
            assert month in MONTHLY_AVG_WIND

    def test_wave_data_complete(self):
        assert len(MONTHLY_AVG_WAVES) == 12
        for month in range(1, 13):
            assert month in MONTHLY_AVG_WAVES

    def test_wind_dir_data_complete(self):
        assert len(MONTHLY_AVG_WIND_DIR) == 12
        for month in range(1, 13):
            assert month in MONTHLY_AVG_WIND_DIR


class TestSolunar:
    def test_solunar_has_illumination_and_four_tier_rating(self):
        dt = datetime(2026, 2, 14, 6, 0, tzinfo=ZoneInfo("America/New_York"))
        sol = compute_solunar_times(dt, 34.2, -77.8, "America/New_York")
        assert "illumination_pct" in sol
        assert 0 <= sol["illumination_pct"] <= 100
        assert sol["rating"] in {"Excellent", "Good", "Fair", "Poor"}

    def test_periods_are_dicts_with_start_end(self):
        """Periods must be dicts so build_best_times / build_activity_timeline can subscript them."""
        dt = datetime(2026, 2, 14, 6, 0, tzinfo=ZoneInfo("America/New_York"))
        sol = compute_solunar_times(dt, 34.2, -77.8, "America/New_York")
        for period_list in (sol["major_periods"], sol["minor_periods"]):
            assert len(period_list) > 0
            for p in period_list:
                assert isinstance(p, dict), "periods must be dicts, not tuples"
                assert "start" in p and "end" in p
