"""Tests for domain.forecast helper functions."""

import pytest

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
    def test_fishable(self):
        result = classify_conditions((5, 10), (1, 2))
        assert result == "Fishable"

    def test_marginal_moderate_wind(self):
        # Wind max 18 (<=20) and wave max 4 (<=5) => Marginal
        result = classify_conditions((10, 18), (2, 4))
        assert result == "Marginal"

    def test_not_fishable_extreme(self):
        result = classify_conditions((25, 40), (6, 10))
        assert result == "Not worth it"

    def test_none_inputs(self):
        """Should handle None gracefully."""
        result = classify_conditions(None, None)
        assert isinstance(result, str)


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
