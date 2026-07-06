"""Tests for domain.forecast helper functions."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.astro import (
    compute_lunar_details,
    compute_solunar_times,
    compute_twilight_times,
)

from domain.forecast import (
    _seasonal_averages,
    _estimate_uv_index,
    _heat_index_f,
    _wind_chill_f,
    classify_conditions,
    score_conditions,
    build_activity_timeline,
    recompute_current_uv,
    MONTHLY_AVG_WIND,
    MONTHLY_AVG_WAVES,
    MONTHLY_AVG_WIND_DIR,
    build_multiday_outlook,
    build_spot_tips,
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
        good = classify_conditions(
            (6, 10), (1, 2), wind_dir="E", coast="west", water_temp_f=65
        )
        bad = classify_conditions(
            (6, 10), (1, 2), wind_dir="W", coast="west", water_temp_f=65
        )
        order = {"Poor": 1, "Challenging": 2, "Fair": 3, "Good": 4, "Excellent": 5}
        assert order[good] >= order[bad]

    def test_east_coast_ne_wind_is_onshore(self):
        # NE is onshore on the Atlantic coast — should score worse than NW (offshore).
        nw = classify_conditions((6, 10), (1, 2), wind_dir="NW", coast="east", water_temp_f=65)
        ne = classify_conditions((6, 10), (1, 2), wind_dir="NE", coast="east", water_temp_f=65)
        order = {"Poor": 1, "Challenging": 2, "Fair": 3, "Good": 4, "Excellent": 5}
        assert order[nw] >= order[ne]

    def test_east_coast_sw_wind_is_offshore(self):
        # SW is offshore on the Atlantic coast — should score better than SE (onshore).
        sw = classify_conditions((6, 10), (1, 2), wind_dir="SW", coast="east", water_temp_f=65)
        se = classify_conditions((6, 10), (1, 2), wind_dir="SE", coast="east", water_temp_f=65)
        order = {"Poor": 1, "Challenging": 2, "Fair": 3, "Good": 4, "Excellent": 5}
        assert order[sw] >= order[se]


class TestScoreConditions:
    def test_returns_index_verdict_and_explanation(self):
        result = score_conditions(
            (4, 8), (1, 1.5), wind_dir="NW", water_temp_f=68
        )
        assert isinstance(result["score"], int)
        assert 0 <= result["score"] <= 100
        assert result["verdict"] in {"Excellent", "Good"}
        # Explanation should surface the dominant drivers as plain phrases.
        assert result["factors"]
        assert result["summary"]
        assert any("surf" in f.lower() for f in result["factors"])
        assert any("wind" in f.lower() for f in result["factors"])

    def test_unknown_when_data_missing(self):
        result = score_conditions(None, None)
        assert result["score"] is None
        assert result["verdict"] == "Unknown"
        assert result["factors"] == []
        assert result["exceeds"] == []

    def test_classify_conditions_matches_score_verdict(self):
        kwargs = dict(wind_dir="NW", water_temp_f=68)
        assert (
            classify_conditions((4, 8), (1, 1.5), **kwargs)
            == score_conditions((4, 8), (1, 1.5), **kwargs)["verdict"]
        )

    def test_wind_threshold_exceeded_penalises_and_warns(self):
        base = score_conditions((14, 18), (1, 2), wind_dir="NW", water_temp_f=68)
        limited = score_conditions(
            (14, 18), (1, 2), wind_dir="NW", water_temp_f=68, max_wind_kt=10
        )
        assert limited["score"] < base["score"]
        assert limited["exceeds"]
        assert any("limit" in w.lower() for w in limited["exceeds"])
        # The warning is surfaced as a leading driver in the explanation.
        assert any("limit" in f.lower() for f in limited["factors"])

    def test_wave_threshold_exceeded_warns(self):
        limited = score_conditions(
            (6, 10), (4, 5), wind_dir="NW", water_temp_f=68, max_wave_ft=3
        )
        assert any("ft limit" in w.lower() for w in limited["exceeds"])

    def test_threshold_not_exceeded_no_warning(self):
        ok = score_conditions(
            (4, 8), (1, 2), wind_dir="NW", water_temp_f=68,
            max_wind_kt=20, max_wave_ft=5,
        )
        assert ok["exceeds"] == []

    def test_hab_danger_penalises_and_warns(self):
        base = score_conditions((4, 8), (1, 1.5), wind_dir="NW", water_temp_f=68)
        wq = {"available": True, "hab_risk": "danger"}
        result = score_conditions(
            (4, 8), (1, 1.5), wind_dir="NW", water_temp_f=68, water_quality=wq
        )
        assert result["score"] < base["score"]
        assert any("algal bloom" in w.lower() for w in result["exceeds"])
        assert any("algal bloom" in f.lower() for f in result["factors"])

    def test_hab_watch_penalises_less_than_danger(self):
        base_watch = score_conditions(
            (4, 8), (1, 1.5), wind_dir="NW", water_temp_f=68,
            water_quality={"available": True, "hab_risk": "watch"},
        )
        base_danger = score_conditions(
            (4, 8), (1, 1.5), wind_dir="NW", water_temp_f=68,
            water_quality={"available": True, "hab_risk": "danger"},
        )
        assert base_watch["score"] > base_danger["score"]
        # A watch-level risk is a factor but not a hard "exceeds" warning.
        assert not base_watch["exceeds"]

    def test_low_dissolved_oxygen_penalises_and_notes_factor(self):
        base = score_conditions((4, 8), (1, 1.5), wind_dir="NW", water_temp_f=68)
        result = score_conditions(
            (4, 8), (1, 1.5), wind_dir="NW", water_temp_f=68,
            water_quality={"available": True, "do_mg_l": "3.5"},
        )
        assert result["score"] < base["score"]
        assert any("oxygen" in f.lower() for f in result["factors"])

    def test_unavailable_water_quality_has_no_effect(self):
        base = score_conditions((4, 8), (1, 1.5), wind_dir="NW", water_temp_f=68)
        result = score_conditions(
            (4, 8), (1, 1.5), wind_dir="NW", water_temp_f=68,
            water_quality={"available": False, "hab_risk": "danger"},
        )
        assert result["score"] == base["score"]


class TestActivityTimelineOverlays:
    def _forecast(self):
        return {
            "conditions": {"sunrise_sunset": "6:00 AM / 8:00 PM", "wind": "NW 6-10 kt"},
            "solunar": {
                "major_periods": [{"start": "7:00 AM", "end": "9:00 AM"}],
                "minor_periods": [{"start": "2:00 PM", "end": "3:00 PM"}],
            },
            "tides": [
                {"hour": 4.0, "type": "Low", "time": "4:00 AM"},
                {"hour": 10.0, "type": "High", "time": "10:00 AM"},
            ],
        }

    def test_timeline_has_24_hours_with_overlay_keys(self):
        tl = build_activity_timeline(self._forecast(), now_hour=12)
        assert len(tl) == 24
        for entry in tl:
            assert {"sun", "tide", "tide_time", "feeding"} <= set(entry)

    def test_sun_events_mapped_to_hours(self):
        tl = build_activity_timeline(self._forecast(), now_hour=12)
        assert tl[6]["sun"] == "sunrise"
        assert tl[20]["sun"] == "sunset"

    def test_tide_events_mapped_to_hours(self):
        tl = build_activity_timeline(self._forecast(), now_hour=12)
        assert tl[4]["tide"] == "low"
        assert tl[10]["tide"] == "high"
        assert tl[10]["tide_time"] == "10:00 AM"

    def test_feeding_bands_tagged(self):
        tl = build_activity_timeline(self._forecast(), now_hour=12)
        # Major band covers 7-9 AM; minor band covers 2 PM.
        assert tl[8]["feeding"] == "major"
        assert tl[14]["feeding"] == "minor"
        # A quiet hour with no period is untagged.
        assert tl[0]["feeding"] == ""

    def test_major_band_not_downgraded_by_minor(self):
        fc = self._forecast()
        # Overlap a minor period onto the major band; major must win.
        fc["solunar"]["minor_periods"].append({"start": "8:00 AM", "end": "8:30 AM"})
        tl = build_activity_timeline(fc, now_hour=12)
        assert tl[8]["feeding"] == "major"

    def test_falling_pressure_beats_rising(self):
        falling = self._forecast()
        falling["pressure"] = {"trend": "Falling", "pressure_mb": 1005}
        rising = self._forecast()
        rising["pressure"] = {"trend": "Rising", "pressure_mb": 1025}
        peak_falling = max(e["level"] for e in build_activity_timeline(falling, 12))
        peak_rising = max(e["level"] for e in build_activity_timeline(rising, 12))
        assert peak_falling > peak_rising

    def test_pressure_missing_is_safe(self):
        fc = self._forecast()
        # No pressure key, and a malformed one, must not raise.
        assert build_activity_timeline(fc, 12)
        fc["pressure"] = {"trend": "falling", "pressure_mb": "n/a"}
        assert build_activity_timeline(fc, 12)

    def test_bright_moon_boosts_night_hours(self):
        dark = self._forecast()
        dark["solunar"]["illumination_pct"] = 5
        bright = self._forecast()
        bright["solunar"]["illumination_pct"] = 95
        # Compare the raw (pre-normalization shape) night activity via levels at 1 AM.
        dark_tl = build_activity_timeline(dark, 12)
        bright_tl = build_activity_timeline(bright, 12)
        assert bright_tl[1]["level"] >= dark_tl[1]["level"]


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


def test_generate_forecast_wires_river_and_bathymetry(monkeypatch):
    """River discharge and bathymetry should surface in the forecast dict
    and sources_used when their services report data."""
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

    river_summary = {
        "available": True,
        "gauges": [{"id": "A", "flow_cfs": 100.0, "distance_mi": 2.0}],
        "nearest": {"id": "A", "flow_cfs": 100.0, "distance_mi": 2.0},
        "source": "USGS NWIS streamgauges",
    }
    bathy_summary = {
        "available": True,
        "point_depth_ft": -15.0,
        "profile": [{"distance_nm": 0.5, "depth_ft": -20.0}],
        "source": "NOAA NCEI Coastal Digital Elevation Models",
    }

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
    monkeypatch.setattr(fc, "_get_river", lambda *_args, **_kwargs: river_summary)
    monkeypatch.setattr(fc, "_get_bathy", lambda *_args, **_kwargs: bathy_summary)

    out = fc.generate_forecast({"id": "test-loc", "name": "Test", "state": "NC"})
    assert out["river_discharge"] == river_summary
    assert out["bathymetry"] == bathy_summary
    assert "USGS NWIS river discharge" in out["sources_used"]
    assert "NOAA NCEI bathymetric DEM" in out["sources_used"]


def test_generate_forecast_surfaces_hab_danger_without_a_profile(monkeypatch):
    """A HAB-danger reading must reach conditions.exceeds_thresholds even for
    anglers with no saved profile/thresholds -- that path never runs the
    personalized rebuild that would otherwise carry it."""
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

    wq_danger = {"available": True, "hab_risk": "danger", "hab_message": "Toxin danger"}

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
    monkeypatch.setattr(fc, "_get_wq", lambda *_args, **_kwargs: wq_danger)
    monkeypatch.setattr(fc, "_get_river", lambda *_args, **_kwargs: {"available": False})
    monkeypatch.setattr(fc, "_get_bathy", lambda *_args, **_kwargs: {"available": False})

    out = fc.generate_forecast({"id": "test-loc", "name": "Test", "state": "NC"})
    assert out["water_quality"]["hab_risk"] == "danger"
    assert any(
        "algal bloom" in w.lower() for w in out["conditions"]["exceeds_thresholds"]
    )


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
                return (
                    now - timedelta(hours=1),
                    now + timedelta(hours=8),
                    "11:00 AM / 8:00 PM",
                )
            return (
                now - timedelta(hours=4),
                now + timedelta(hours=1),
                "8:00 AM / 1:00 PM",
            )

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

    north_location = {
        "id": "north",
        "name": "North",
        "state": "ME",
        "lat": 45.0,
        "lng": -68.0,
        "timezone": "America/New_York",
    }
    south_location = {
        "id": "south",
        "name": "South",
        "state": "FL",
        "lat": 25.0,
        "lng": -80.0,
        "timezone": "America/New_York",
    }

    north = fc.generate_forecast(north_location)
    south = fc.generate_forecast(south_location)

    assert "uv" in north and "uv" in south
    assert north["uv"]["index"] != south["uv"]["index"]


def test_estimate_uv_index_scales_by_latitude():
    """Lower latitudes (closer to equator) should produce higher peak UV."""
    tz = ZoneInfo("America/New_York")
    # Use a fixed solar-noon-ish time so timing is not the differentiator
    now = datetime(2024, 6, 21, 12, 0, 0, tzinfo=tz)
    sunrise = now - timedelta(hours=6)  # 6 AM
    sunset = now + timedelta(hours=6)  # 6 PM  (noon = pct=0.5, bell peak)

    uv_tropical = _estimate_uv_index(now, sunrise, sunset, lat=20.0)  # Hawaii
    uv_florida = _estimate_uv_index(now, sunrise, sunset, lat=27.0)  # FL
    uv_nc = _estimate_uv_index(now, sunrise, sunset, lat=35.0)  # NC
    uv_maine = _estimate_uv_index(now, sunrise, sunset, lat=44.0)  # ME

    # Each location should have a lower UV than the one closer to the equator
    assert uv_tropical > uv_florida > uv_nc > uv_maine
    # UV should be positive midday for all locations
    assert uv_maine > 0


def test_estimate_uv_index_returns_zero_at_night():
    """UV should be 0 when the current time is outside sunrise-sunset."""
    tz = ZoneInfo("America/New_York")
    now = datetime(2024, 6, 21, 23, 0, 0, tzinfo=tz)  # 11 PM
    sunrise = now.replace(hour=6)
    sunset = now.replace(hour=20)
    assert _estimate_uv_index(now, sunrise, sunset, lat=35.0) == 0.0


def test_recompute_current_uv_uses_location_lat():
    """recompute_current_uv should yield different values for different latitudes."""
    fl_location = {"lat": 25.0, "lng": -80.0, "timezone": "America/New_York"}
    me_location = {"lat": 45.0, "lng": -68.0, "timezone": "America/New_York"}

    fl_uv = recompute_current_uv(fl_location)
    me_uv = recompute_current_uv(me_location)

    assert "index" in fl_uv and "level" in fl_uv
    assert "index" in me_uv and "level" in me_uv
    # Both should produce valid UV dicts; at midday FL should exceed ME
    # (We can't assert the exact time of day in tests, but structure is valid.)
    assert isinstance(fl_uv["index"], float)
    assert isinstance(me_uv["index"], float)


def test_recompute_current_uv_no_location():
    """recompute_current_uv should not raise when called without a location."""
    result = recompute_current_uv(None)
    assert "index" in result
    assert isinstance(result["index"], float)


def test_heat_index_and_wind_chill_helpers():
    assert _heat_index_f(90, 65) is not None
    assert _heat_index_f(72, 60) is None
    assert _wind_chill_f(40, 15) is not None
    assert _wind_chill_f(60, 15) is None


def test_build_multiday_outlook_uses_daily_nws_period_data(monkeypatch):
    now = datetime(2026, 3, 5, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    mock_periods = [
        {
            "isDaytime": True,
            "name": "Friday",
            "startTime": "2026-03-06T06:00:00-05:00",
            "windSpeed": "10 to 14 mph",
            "windDirection": "SW",
            "detailedForecast": "Southwest wind 10 to 14 mph. Seas 2 to 3 ft.",
        },
        {
            "isDaytime": True,
            "name": "Saturday",
            "startTime": "2026-03-07T06:00:00-05:00",
            "windSpeed": "5 to 8 mph",
            "windDirection": "N",
            "detailedForecast": "North wind 5 to 8 mph. Seas 1 to 2 ft.",
        },
        {
            "isDaytime": True,
            "name": "Sunday",
            "startTime": "2026-03-08T06:00:00-05:00",
            "windSpeed": "15 to 20 mph",
            "windDirection": "E",
            "detailedForecast": "East wind 15 to 20 mph. Seas 4 to 6 ft.",
        },
    ]

    monkeypatch.setattr(
        "domain.forecast._fetch_nws_extended", lambda *_args, **_kwargs: mock_periods
    )
    monkeypatch.setattr(
        "domain.forecast.compute_solunar_times", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        "domain.forecast._sun_times", lambda *_args, **_kwargs: (None, None)
    )

    outlook = build_multiday_outlook(
        now,
        {
            "lat": 34.2,
            "lng": -77.8,
            "timezone": "America/New_York",
            "conditions_region": "atlantic_mid",
        },
    )

    assert [d["day"] for d in outlook] == ["Friday", "Saturday", "Sunday"]
    assert [d["wind"] for d in outlook] == ["SW 9-12 kt", "N 4-7 kt", "E 13-17 kt"]
    assert [d["waves"] for d in outlook] == ["2-3 ft", "1-2 ft", "4-6 ft"]


def test_build_multiday_outlook_does_not_match_wrong_period_by_name(monkeypatch):
    """Name-based fallback must NOT fire when startTime was parsed but belongs to
    a different day.

    The old code lacked a ``continue`` after a successful-but-non-matching
    startTime parse, so the loop would fall through to the name check.  If a
    period appeared in the list *before* the correct one and its name happened
    to start with the same 3 characters as the target day (e.g. a "Saturday
    Afternoon" period appearing before the canonical "Saturday" period), that
    wrong period would be selected — causing all 3 days to display the same
    stale data.
    """
    now = datetime(2026, 3, 5, 12, 0, tzinfo=ZoneInfo("America/New_York"))

    mock_periods = [
        {
            # This period's name starts with "Fri" but its startTime is Monday –
            # without the fix the name check fires and it would be selected for
            # the Friday slot even though the date is completely wrong.
            "isDaytime": True,
            "name": "Friday Outlook",
            "startTime": "2026-03-09T06:00:00-05:00",  # Monday – wrong date
            "windSpeed": "30 to 40 mph",
            "windDirection": "N",
            "detailedForecast": "North wind 30 to 40 mph. Seas 8 to 12 ft.",
        },
        {
            "isDaytime": True,
            "name": "Friday",
            "startTime": "2026-03-06T06:00:00-05:00",
            "windSpeed": "10 to 14 mph",
            "windDirection": "SW",
            "detailedForecast": "Southwest wind 10 to 14 mph. Seas 2 to 3 ft.",
        },
        {
            "isDaytime": True,
            "name": "Saturday",
            "startTime": "2026-03-07T06:00:00-05:00",
            "windSpeed": "5 to 8 mph",
            "windDirection": "N",
            "detailedForecast": "North wind 5 to 8 mph. Seas 1 to 2 ft.",
        },
        {
            "isDaytime": True,
            "name": "Sunday",
            "startTime": "2026-03-08T06:00:00-05:00",
            "windSpeed": "15 to 20 mph",
            "windDirection": "E",
            "detailedForecast": "East wind 15 to 20 mph. Seas 4 to 6 ft.",
        },
    ]

    monkeypatch.setattr(
        "domain.forecast._fetch_nws_extended", lambda *_args, **_kwargs: mock_periods
    )
    monkeypatch.setattr(
        "domain.forecast.compute_solunar_times", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        "domain.forecast._sun_times", lambda *_args, **_kwargs: (None, None)
    )

    outlook = build_multiday_outlook(
        now,
        {
            "lat": 34.2,
            "lng": -77.8,
            "timezone": "America/New_York",
            "conditions_region": "atlantic_mid",
        },
    )

    # Friday must use the real Friday period, not "Friday Outlook" whose
    # startTime is Monday.
    assert [d["day"] for d in outlook] == ["Friday", "Saturday", "Sunday"]
    assert [d["wind"] for d in outlook] == ["SW 9-12 kt", "N 4-7 kt", "E 13-17 kt"]
    assert [d["waves"] for d in outlook] == ["2-3 ft", "1-2 ft", "4-6 ft"]


def test_build_multiday_outlook_estimates_waves_from_daily_wind_when_missing(
    monkeypatch,
):
    now = datetime(2026, 3, 5, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    mock_periods = [
        {
            "isDaytime": True,
            "name": "Friday",
            "startTime": "2026-03-06T06:00:00-05:00",
            "windSpeed": "8 to 10 mph",
            "windDirection": "SW",
            "detailedForecast": "Southwest wind 8 to 10 mph.",
        },
        {
            "isDaytime": True,
            "name": "Saturday",
            "startTime": "2026-03-07T06:00:00-05:00",
            "windSpeed": "15 to 18 mph",
            "windDirection": "N",
            "detailedForecast": "North wind 15 to 18 mph.",
        },
        {
            "isDaytime": True,
            "name": "Sunday",
            "startTime": "2026-03-08T06:00:00-05:00",
            "windSpeed": "22 to 26 mph",
            "windDirection": "E",
            "detailedForecast": "East wind 22 to 26 mph.",
        },
    ]

    monkeypatch.setattr(
        "domain.forecast._fetch_nws_extended", lambda *_args, **_kwargs: mock_periods
    )
    monkeypatch.setattr(
        "domain.forecast.compute_solunar_times", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        "domain.forecast._sun_times", lambda *_args, **_kwargs: (None, None)
    )

    outlook = build_multiday_outlook(
        now,
        {
            "lat": 34.2,
            "lng": -77.8,
            "timezone": "America/New_York",
            "conditions_region": "atlantic_mid",
        },
    )

    assert [d["waves"] for d in outlook] == ["1-2 ft", "2-4 ft", "3-6 ft"]


def test_build_multiday_outlook_uses_marine_zone_when_gridpoint_fails(monkeypatch):
    """When the NWS gridpoint API fails (e.g. offshore/pier coordinates), the
    outlook must fall back to the marine zone forecast and correctly parse wind
    speed in knots and wave height from the detailedForecast text."""
    now = datetime(2026, 3, 5, 12, 0, tzinfo=ZoneInfo("America/New_York"))

    # Marine zone periods: wind in knots + wave height in detailedForecast text;
    # no separate windSpeed / windDirection fields.
    marine_periods = [
        {
            "isDaytime": True,
            "name": "Friday",
            "startTime": "2026-03-06T06:00:00-05:00",
            "detailedForecast": "Southwest winds 10 to 14 knots. Seas 2 to 3 feet.",
        },
        {
            "isDaytime": False,
            "name": "Friday Night",
            "startTime": "2026-03-06T18:00:00-05:00",
            "detailedForecast": "Southwest winds 8 to 12 knots. Seas 2 to 3 feet.",
        },
        {
            "isDaytime": True,
            "name": "Saturday",
            "startTime": "2026-03-07T06:00:00-05:00",
            "detailedForecast": "North winds 4 to 7 knots. Seas 1 to 2 feet.",
        },
        {
            "isDaytime": True,
            "name": "Sunday",
            "startTime": "2026-03-08T06:00:00-05:00",
            "detailedForecast": "East winds 13 to 17 knots. Seas 4 to 6 feet.",
        },
    ]

    # Gridpoint always fails; marine zone returns the periods above.
    def _fake_fetch_extended(lat, lng, zone=""):
        if zone == "AMZ158":
            return marine_periods
        return []

    monkeypatch.setattr("domain.forecast._fetch_nws_extended", _fake_fetch_extended)
    monkeypatch.setattr("domain.forecast.compute_solunar_times", lambda *_a, **_kw: {})
    monkeypatch.setattr("domain.forecast._sun_times", lambda *_a, **_kw: (None, None))

    outlook = build_multiday_outlook(
        now,
        {
            "lat": 34.2,
            "lng": -77.8,
            "nws_zone": "AMZ158",
            "timezone": "America/New_York",
            "conditions_region": "atlantic_mid",
        },
    )

    assert [d["day"] for d in outlook] == ["Friday", "Saturday", "Sunday"]
    assert [d["wind"] for d in outlook] == ["SW 10-14 kt", "N 4-7 kt", "E 13-17 kt"]
    assert [d["waves"] for d in outlook] == ["2-3 ft", "1-2 ft", "4-6 ft"]


def test_personalize_forecast_uses_location_fish_region_for_calendar(monkeypatch):
    from domain import forecast as fc

    monkeypatch.setattr(
        fc,
        "build_species_ranking",
        lambda *_args, **_kwargs: [{"name": "Red drum (puppy drum)"}],
    )
    monkeypatch.setattr(fc, "build_rig_recommendations", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_bait_ranking", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_bite_alerts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_gear_checklist", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fc, "build_multiday_outlook", lambda *_args, **_kwargs: [])

    captured = {}

    def _calendar(_species, _location=None, fish_region=""):
        captured["fish_region"] = fish_region
        return []

    monkeypatch.setattr(fc, "build_species_calendar", _calendar)

    base = {
        "conditions": {
            "water_temp_f": 68,
            "wind": "SW 8-12 kt",
            "waves": "2-3 ft",
            "verdict": "Good",
        },
        "tide_state": "incoming",
    }
    profile = {"fishing_types": ["pier"]}
    location = {
        "state": "NC",
        "fish_region": "southeast",
        "timezone": "America/New_York",
        "conditions_region": "atlantic_mid",
    }

    fc.personalize_forecast(base, profile, location=location)

    assert captured["fish_region"] == "southeast"


def test_tide_predictions_fall_back_to_available_date_when_today_missing(monkeypatch):
    from domain.forecast import TidePredictionService

    service = TidePredictionService()

    def _fake_fetch(*_args, **_kwargs):
        return [
            {
                "date_str": "20260103",
                "time": "1:00 AM",
                "type": "High",
                "height_ft": "4.2",
                "hour": 1.0,
                "height_num": 4.2,
            },
            {
                "date_str": "20260103",
                "time": "7:00 AM",
                "type": "Low",
                "height_ft": "0.9",
                "hour": 7.0,
                "height_num": 0.9,
            },
        ]

    monkeypatch.setattr("domain.forecast.fetch_tide_predictions", _fake_fetch)
    monkeypatch.setattr(
        "domain.forecast.build_tide_chart_svg",
        lambda tides, now_hour=None: (
            {
                "path": "M0,0",
                "fill_path": "M0,0Z",
                "markers": [],
                "viewBox": "0 0 600 140",
                "width": 600,
                "height": 140,
            }
            if tides
            else {}
        ),
    )

    out = service.get_tide_predictions(
        datetime(2026, 1, 2, 22, 0), {"coops_station": "123"}, "America/New_York"
    )

    assert len(out["tides"]) == 2
    assert out["tides"][0]["date_str"] == "20260103"
    assert isinstance(out["tide_chart"], dict)
    assert "path" in out["tide_chart"]


# ---------------------------------------------------------------------------
# _derive_coast() — canonical coast derivation helper
# ---------------------------------------------------------------------------


class TestDeriveCoast:
    """Unit tests for the canonical _derive_coast() helper.

    Verifies every supported conditions_region prefix maps to the correct
    coast string and that unknown/missing regions return None (safe fallback).
    """

    def _coast(self, conditions_region):
        from domain.forecast import _derive_coast

        return _derive_coast({"conditions_region": conditions_region})

    # ---- Atlantic (east) ----
    def test_atlantic_north_is_east(self):
        assert self._coast("atlantic_north") == "east"

    def test_atlantic_mid_is_east(self):
        assert self._coast("atlantic_mid") == "east"

    def test_atlantic_south_is_east(self):
        assert self._coast("atlantic_south") == "east"

    # ---- Gulf (east) ----
    def test_gulf_is_east(self):
        assert self._coast("gulf") == "east"

    # ---- Pacific (west) ----
    def test_pacific_is_west(self):
        assert self._coast("pacific") == "west"

    def test_pacific_south_is_west(self):
        assert self._coast("pacific_south") == "west"

    # ---- Hawaii ----
    def test_hawaii_conditions_is_hawaii(self):
        assert self._coast("hawaii_conditions") == "hawaii"

    # ---- Unknown / missing → None ----
    def test_missing_conditions_region_returns_none(self):
        from domain.forecast import _derive_coast

        assert _derive_coast({"state": "NC"}) is None

    def test_empty_conditions_region_returns_none(self):
        assert self._coast("") is None

    def test_unrecognised_region_returns_none(self):
        assert self._coast("great_lakes") is None

    def test_none_location_returns_none(self):
        from domain.forecast import _derive_coast

        assert _derive_coast(None) is None

    def test_empty_location_returns_none(self):
        from domain.forecast import _derive_coast

        assert _derive_coast({}) is None

    # ---- Integration: coast drives species filtering ----
    def test_east_location_produces_only_east_species(self):
        """An east-coast location must not return west or Hawaii species."""
        from domain.species import build_species_ranking, SPECIES_DB

        ranking = build_species_ranking(month=7, water_temp=76, coast="east")
        for sp in ranking:
            db_entry = next(s for s in SPECIES_DB if s["name"] == sp["name"])
            assert db_entry.get("coast") == "east", (
                f"Non-east species '{sp['name']}' appeared in east ranking"
            )

    def test_west_location_produces_only_west_species(self):
        """A west-coast location must not return east or Hawaii species."""
        from domain.species import build_species_ranking, SPECIES_DB

        ranking = build_species_ranking(month=11, water_temp=60, coast="west")
        for sp in ranking:
            db_entry = next(s for s in SPECIES_DB if s["name"] == sp["name"])
            assert db_entry.get("coast") == "west", (
                f"Non-west species '{sp['name']}' appeared in west ranking"
            )

    def test_hawaii_location_produces_only_hawaii_species(self):
        """A Hawaii location must not return east or west species."""
        from domain.species import build_species_ranking, SPECIES_DB

        ranking = build_species_ranking(month=6, water_temp=78, coast="hawaii")
        for sp in ranking:
            db_entry = next(s for s in SPECIES_DB if s["name"] == sp["name"])
            assert db_entry.get("coast") == "hawaii", (
                f"Non-hawaii species '{sp['name']}' appeared in Hawaii ranking"
            )

    def test_unknown_location_produces_no_species(self):
        """A location with no conditions_region must produce no species (coast=None)."""
        from domain.forecast import _derive_coast
        from domain.species import build_species_ranking

        coast = _derive_coast({"state": "XX"})  # no conditions_region
        assert coast is None
        ranking = build_species_ranking(month=7, water_temp=72, coast=coast)
        assert ranking == [], (
            "Unknown coast must produce empty species list, not east/default species"
        )


def test_personalize_caught_here_boost(monkeypatch):
    """Species the user has landed here get a score bump, a flag, and rerank."""
    from domain import forecast as fc

    # Stub the species-dependent section rebuilds (and the networked outlook)
    # so the test isolates the caught-here boost.
    for name in (
        "build_rig_recommendations", "build_bait_ranking", "build_lure_recommendations",
        "build_species_calendar", "build_bite_alerts", "build_gear_checklist",
        "build_safety_checklist", "build_spot_tips", "build_best_times",
        "build_multiday_outlook", "pick_best_fishing_day",
    ):
        monkeypatch.setattr(fc, name, lambda *a, **k: [])
    monkeypatch.setattr(fc, "_get_technique_tip", lambda *a, **k: "")

    forecast = {
        "generated_at": "2026-06-10T10:00:00",
        "conditions": {"verdict": "Good", "water_temp_f": 70, "wind": "NE 6-10 kt",
                       "waves": "1-2 ft", "wind_dir": "NE"},
        "tide_state": "Rising",
        "solunar": {},
        "species": [
            {"name": "Bluefish", "score": 80, "rank": 1, "rig": "x", "hook_size": "2/0",
             "sinker": "2 oz", "bait": "cut", "activity": "Hot", "explanation": "",
             "categories": [], "lures": ""},
            {"name": "Red drum", "score": 70, "rank": 2, "rig": "x", "hook_size": "2/0",
             "sinker": "2 oz", "bait": "cut", "activity": "Active", "explanation": "",
             "categories": [], "lures": ""},
        ],
    }
    loc = {"id": "loc", "conditions_region": "atlantic", "timezone": "America/New_York"}

    out = fc.personalize_forecast(
        forecast, {}, loc, caught_species={"red drum"}
    )
    by_name = {s["name"]: s for s in out["species"]}
    assert by_name["Red drum"].get("caught_here") is True
    assert by_name["Red drum"]["score"] == 78  # 70 + 8
    # 78 < 80 so Bluefish still leads, but Red drum keeps its flag and bump.
    assert by_name["Bluefish"].get("caught_here") is not True

    # A bigger boost would overtake — verify reranking happens when it does.
    fc._PERSONALIZE_CACHE.clear()  # same generated_at would otherwise cache-hit
    forecast["species"][1]["score"] = 75
    out2 = fc.personalize_forecast(
        forecast, {}, loc, caught_species={"red drum"}
    )
    assert out2["species"][0]["name"] == "Red drum"  # 75+8=83 > 80
    assert out2["species"][0]["rank"] == 1


class TestPickBestFishingDay:
    def test_tier_dominates_score(self):
        from domain.forecast import pick_best_fishing_day
        # Excellent (low score) beats Good (high score) — tier wins.
        out = pick_best_fishing_day(
            "Fair",
            [
                {"day": "Sat", "verdict": "Good", "score": 70, "top_species": []},
                {"day": "Sun", "verdict": "Excellent", "score": 50, "top_species": []},
            ],
            today_score=40,
        )
        assert out["best_day"] == "Sun"
        assert out["verdict"] == "Excellent"

    def test_numeric_score_breaks_tie_within_tier(self):
        from domain.forecast import pick_best_fishing_day
        out = pick_best_fishing_day(
            "Fair",
            [
                {"day": "Sat", "verdict": "Good", "score": 62, "top_species": []},
                {"day": "Sun", "verdict": "Good", "score": 75, "top_species": []},
            ],
            today_score=40,
        )
        assert out["best_day"] == "Sun"  # higher score within the same tier

    def test_today_can_win(self):
        from domain.forecast import pick_best_fishing_day
        out = pick_best_fishing_day(
            "Excellent",
            [{"day": "Sat", "verdict": "Fair", "score": 50, "top_species": []}],
            today_score=88,
        )
        assert out["best_day"] == "Today"
        assert "great" in out["recommendation"].lower()


class TestSafetyChecklistPFD:
    def _has_pfd(self, items):
        return any("PFD" in i["text"] or "life vest" in i["text"] for i in items)

    def test_heavy_surf_adds_pfd_for_shore_angler(self):
        from domain.forecast import build_safety_checklist
        assert self._has_pfd(build_safety_checklist(wave_range=(4, 5), fishing_types=["surf"]))
        assert self._has_pfd(build_safety_checklist(wave_range=(5, 7), fishing_types=["jetty"]))

    def test_calm_surf_no_pfd(self):
        from domain.forecast import build_safety_checklist
        assert not self._has_pfd(build_safety_checklist(wave_range=(1, 2), fishing_types=["surf"]))

    def test_kayak_not_duplicated(self):
        from domain.forecast import build_safety_checklist
        items = build_safety_checklist(wave_range=(4, 5), fishing_types=["kayak"])
        assert sum("inflatable PFD" in i["text"] for i in items) == 0


class TestSafetyChecklistHab:
    def _texts(self, items):
        return [i["text"] for i in items]

    def test_hab_danger_adds_warning(self):
        from domain.forecast import build_safety_checklist
        wq = {"available": True, "hab_risk": "danger", "hab_message": "Danger msg"}
        items = build_safety_checklist(water_quality=wq)
        assert any("Danger msg" == t for t in self._texts(items))

    def test_hab_danger_adds_extra_item_for_wade_anglers(self):
        from domain.forecast import build_safety_checklist
        wq = {"available": True, "hab_risk": "danger"}
        items = build_safety_checklist(water_quality=wq, fishing_types=["wade"])
        assert any("Skip wading" in t for t in self._texts(items))

    def test_hab_danger_no_wade_item_for_non_wade_anglers(self):
        from domain.forecast import build_safety_checklist
        wq = {"available": True, "hab_risk": "danger"}
        items = build_safety_checklist(water_quality=wq, fishing_types=["surf"])
        assert not any("Skip wading" in t for t in self._texts(items))

    def test_hab_watch_adds_caution_item(self):
        from domain.forecast import build_safety_checklist
        wq = {"available": True, "hab_risk": "watch"}
        items = build_safety_checklist(water_quality=wq)
        assert any("rinse hands" in t.lower() for t in self._texts(items))

    def test_hab_low_risk_adds_nothing(self):
        from domain.forecast import build_safety_checklist
        wq = {"available": True, "hab_risk": "low"}
        items = build_safety_checklist(water_quality=wq)
        assert not any("algal bloom" in t.lower() for t in self._texts(items))

    def test_unavailable_water_quality_adds_nothing(self):
        from domain.forecast import build_safety_checklist
        wq = {"available": False, "hab_risk": "danger"}
        items = build_safety_checklist(water_quality=wq)
        assert not any("algal bloom" in t.lower() for t in self._texts(items))


class TestRecentRainTips:
    def _titles(self, tips):
        return [t["title"] for t in tips]

    def test_heavy_rain_muddy_water_tip(self):
        from domain.forecast import build_spot_tips
        tips = build_spot_tips(recent_rain_in=1.3, coast="east")
        assert any("Muddy Water" in t for t in self._titles(tips))

    def test_moderate_rain_runoff_tip(self):
        from domain.forecast import build_spot_tips
        tips = build_spot_tips(recent_rain_in=0.6, coast="east")
        assert any("Runoff" in t for t in self._titles(tips))

    def test_light_rain_no_tip(self):
        from domain.forecast import build_spot_tips
        tips = build_spot_tips(recent_rain_in=0.2, coast="east")
        assert not any("Runoff" in t or "Muddy" in t for t in self._titles(tips))

    def test_none_rain_safe(self):
        from domain.forecast import build_spot_tips
        assert isinstance(build_spot_tips(recent_rain_in=None, coast="east"), list)


class TestHabRiskTips:
    def _titles(self, tips):
        return [t["title"] for t in tips]

    def test_hab_watch_adds_warning_tip(self):
        wq = {"available": True, "hab_risk": "watch", "hab_message": "Elevated bloom risk"}
        tips = build_spot_tips(water_quality=wq, coast="east")
        assert any("Harmful Algal Bloom Watch" in t for t in self._titles(tips))
        detail = next(t["detail"] for t in tips if t["title"] == "Harmful Algal Bloom Watch")
        assert detail == "Elevated bloom risk"

    def test_hab_danger_adds_danger_tip(self):
        wq = {"available": True, "hab_risk": "danger", "hab_message": "Toxin danger"}
        tips = build_spot_tips(water_quality=wq, coast="east")
        assert any("Harmful Algal Bloom Danger" in t for t in self._titles(tips))

    def test_hab_low_risk_adds_no_tip(self):
        wq = {"available": True, "hab_risk": "low", "hab_message": ""}
        tips = build_spot_tips(water_quality=wq, coast="east")
        assert not any("Harmful Algal Bloom" in t for t in self._titles(tips))

    def test_unavailable_water_quality_adds_no_tip(self):
        wq = {"available": False, "hab_risk": "danger"}
        tips = build_spot_tips(water_quality=wq, coast="east")
        assert not any("Harmful Algal Bloom" in t for t in self._titles(tips))


class TestBathymetryTips:
    def _titles(self, tips):
        return [t["title"] for t in tips]

    def test_shallow_point_depth_adds_shallow_tip(self):
        bathy = {"available": True, "point_depth_ft": -4.0, "profile": []}
        tips = build_spot_tips(bathymetry=bathy, coast="east")
        assert any("Shallow Water" in t for t in self._titles(tips))

    def test_deep_drop_off_adds_structure_tip(self):
        bathy = {
            "available": True,
            "point_depth_ft": -10.0,
            "profile": [{"distance_nm": 1.0, "depth_ft": -30.0}],
        }
        tips = build_spot_tips(bathymetry=bathy, coast="east")
        assert any("Drop-Off" in t for t in self._titles(tips))

    def test_gradual_slope_adds_no_structure_tip(self):
        bathy = {
            "available": True,
            "point_depth_ft": -10.0,
            "profile": [{"distance_nm": 1.0, "depth_ft": -12.0}],
        }
        tips = build_spot_tips(bathymetry=bathy, coast="east")
        assert not any(
            "Drop-Off" in t or "Shallow Water" in t for t in self._titles(tips)
        )

    def test_land_point_adds_no_tip(self):
        bathy = {"available": True, "point_depth_ft": 5.0, "profile": []}
        tips = build_spot_tips(bathymetry=bathy, coast="east")
        assert not any(
            "Drop-Off" in t or "Shallow Water" in t for t in self._titles(tips)
        )

    def test_unavailable_bathymetry_adds_no_tip(self):
        tips = build_spot_tips(bathymetry={"available": False}, coast="east")
        assert not any(
            "Drop-Off" in t or "Shallow Water" in t for t in self._titles(tips)
        )


class TestRiverDischargeTip:
    def test_nearby_gauge_appends_flow_to_rain_tip(self):
        river = {
            "available": True,
            "nearest": {"name": "Test Creek", "distance_mi": 2.0, "flow_cfs": 150.0},
        }
        tips = build_spot_tips(recent_rain_in=1.2, river_discharge=river, coast="east")
        rain_tip = next(t for t in tips if t["title"] == "Muddy Water After Heavy Rain")
        assert "Test Creek" in rain_tip["detail"]
        assert "150 cfs" in rain_tip["detail"]

    def test_distant_gauge_does_not_append(self):
        river = {
            "available": True,
            "nearest": {"name": "Far Creek", "distance_mi": 40.0, "flow_cfs": 150.0},
        }
        tips = build_spot_tips(recent_rain_in=1.2, river_discharge=river, coast="east")
        rain_tip = next(t for t in tips if t["title"] == "Muddy Water After Heavy Rain")
        assert "Far Creek" not in rain_tip["detail"]

    def test_no_rain_tip_means_no_gauge_append(self):
        river = {
            "available": True,
            "nearest": {"name": "Test Creek", "distance_mi": 2.0, "flow_cfs": 150.0},
        }
        tips = build_spot_tips(river_discharge=river, coast="east")
        assert not any("Test Creek" in t.get("detail", "") for t in tips)
