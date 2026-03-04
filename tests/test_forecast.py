"""Tests for domain.forecast helper functions."""

import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.astro import compute_lunar_details, compute_solunar_times, compute_twilight_times

from domain.forecast import (
    _seasonal_averages,
    _heat_index_f,
    _wind_chill_f,
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


class TestAstronomyExtras:
    def test_twilight_contains_golden_windows(self):
        dt = datetime(2026, 6, 14, 6, 0, tzinfo=ZoneInfo("America/New_York"))
        out = compute_twilight_times(dt, 34.2, -77.8, "America/New_York")
        assert "golden_am" in out and "golden_pm" in out

    def test_lunar_details_has_rise_set_age_distance(self):
        dt = datetime(2026, 6, 14, 6, 0, tzinfo=ZoneInfo("America/New_York"))
        out = compute_lunar_details(dt, -77.8, "America/New_York")
        assert {"moonrise", "moonset", "age_days", "distance_km"}.issubset(out.keys())


def test_generate_forecast_includes_metadata(monkeypatch):
    """Generated forecast should include version/source metadata for auditability."""
    from domain import forecast as fc

    class _Marine:
        def get_marine_forecast(self, *_args, **_kwargs):
            _kwargs["sources_used"].append("test_marine")
            return (5.0, 8.0), (1.0, 2.0), "NW"

    class _Tides:
        def get_tide_predictions(self, *_args, **_kwargs):
            return {}

    class _Buoy:
        def get_barometric_pressure(self, *_args, **_kwargs):
            return None

    class _Weather:
        def get_weather_alerts(self, *_args, **_kwargs):
            return []

        def get_state_alerts(self, *_args, **_kwargs):
            return []

        def get_current_weather(self, *_args, **_kwargs):
            return None

    class _Env:
        def get_coops_environmental(self, *_args, **_kwargs):
            return {}

        def get_currents(self, *_args, **_kwargs):
            return []

        def get_current_observation(self, *_args, **_kwargs):
            return None

    class _Astro:
        def get_sun_times(self, now, *_args, **_kwargs):
            return now, now, "6:00 AM / 6:00 PM"

        def get_solunar_times(self, *_args, **_kwargs):
            return {}

        def get_twilight_times(self, *_args, **_kwargs):
            return {}

        def get_lunar_details(self, *_args, **_kwargs):
            return {}

    class _Builder:
        def __init__(self):
            self.marine_service = _Marine()
            self.tide_service = _Tides()
            self.buoy_service = _Buoy()
            self.weather_service = _Weather()
            self.environment_service = _Env()
            self.astro_service = _Astro()

    monkeypatch.setattr(fc, "ForecastBuilder", _Builder)
    monkeypatch.setattr(fc, "get_water_temp", lambda *_args, **_kwargs: (70.0, True))
    monkeypatch.setattr(fc, "build_species_ranking", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_rig_recommendations", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_bait_ranking", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_species_calendar", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_natural_bait_chart", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_spot_tips", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_conditions_explainer", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_bite_alerts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_gear_checklist", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_safety_checklist", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_best_times", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_activity_timeline", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_multiday_outlook", lambda *_args, **_kwargs: [])

    out = fc.generate_forecast({"id": "test-loc", "name": "Test", "state": "NC"})
    assert out["forecast_version"] == fc.FORECAST_VERSION
    assert isinstance(out["sources_used"], list)
    assert isinstance(out["fallbacks_triggered"], list)


def test_generate_forecast_uv_reflects_selected_location(monkeypatch):
    """UV index should be computed from sun times for the requested location."""
    from domain import forecast as fc

    class _Marine:
        def get_marine_forecast(self, *_args, **_kwargs):
            return (5.0, 8.0), (1.0, 2.0), "NW"

    class _Tides:
        def get_tide_predictions(self, *_args, **_kwargs):
            return {}

    class _Buoy:
        def get_barometric_pressure(self, *_args, **_kwargs):
            return None

    class _Weather:
        def get_weather_alerts(self, *_args, **_kwargs):
            return []

        def get_state_alerts(self, *_args, **_kwargs):
            return []

        def get_current_weather(self, *_args, **_kwargs):
            return None

    class _Env:
        def get_coops_environmental(self, *_args, **_kwargs):
            return {}

        def get_currents(self, *_args, **_kwargs):
            return []

        def get_current_observation(self, *_args, **_kwargs):
            return None

    class _Astro:
        def get_sun_times(self, now, lat, *_args, **_kwargs):
            # Simulate different daylight windows by location latitude.
            if lat > 40:
                return now - timedelta(hours=1), now + timedelta(hours=8), "11:00 AM / 8:00 PM"
            return now - timedelta(hours=4), now + timedelta(hours=1), "8:00 AM / 1:00 PM"

        def get_solunar_times(self, *_args, **_kwargs):
            return {}

        def get_twilight_times(self, *_args, **_kwargs):
            return {}

        def get_lunar_details(self, *_args, **_kwargs):
            return {}

    class _Builder:
        def __init__(self):
            self.marine_service = _Marine()
            self.tide_service = _Tides()
            self.buoy_service = _Buoy()
            self.weather_service = _Weather()
            self.environment_service = _Env()
            self.astro_service = _Astro()

    monkeypatch.setattr(fc, "ForecastBuilder", _Builder)
    monkeypatch.setattr(fc, "get_water_temp", lambda *_args, **_kwargs: (70.0, True))
    monkeypatch.setattr(fc, "build_species_ranking", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_rig_recommendations", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_bait_ranking", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_species_calendar", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_natural_bait_chart", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_spot_tips", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_conditions_explainer", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_bite_alerts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_gear_checklist", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_safety_checklist", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_best_times", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_activity_timeline", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_multiday_outlook", lambda *_args, **_kwargs: [])

    north_location = {"id": "north", "name": "North", "state": "ME", "lat": 45.0, "lng": -68.0, "timezone": "America/New_York"}
    south_location = {"id": "south", "name": "South", "state": "FL", "lat": 25.0, "lng": -80.0, "timezone": "America/New_York"}

    north = fc.generate_forecast(north_location)
    south = fc.generate_forecast(south_location)

    assert "uv" in north and "uv" in south
    assert north["uv"]["index"] != south["uv"]["index"]


def test_heat_index_and_wind_chill_helpers():
    assert _heat_index_f(90, 65) is not None
    assert _heat_index_f(72, 60) is None
    assert _wind_chill_f(40, 15) is not None
    assert _wind_chill_f(60, 15) is None
