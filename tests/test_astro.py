"""Tests for services/astro.py — sun/moon/solunar calculations."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import services.astro as astro_mod
from services.astro import (
    _moon_phase,
    _moon_transit_hours,
    _sun_times,
    _sun_event_time,
    compute_lunar_details,
    compute_solunar_times,
    compute_twilight_times,
)


# ---------------------------------------------------------------------------
# _sun_times
# ---------------------------------------------------------------------------


class TestSunTimes:
    _DT = datetime(2026, 6, 21, tzinfo=ZoneInfo("America/New_York"))  # summer solstice

    def test_returns_sunrise_before_sunset(self):
        sunrise, sunset = _sun_times(self._DT, 34.2, -77.8, "America/New_York")
        assert sunrise < sunset

    def test_default_coords_used_when_zero(self):
        sunrise1, sunset1 = _sun_times(self._DT, 0, 0, "America/New_York")
        sunrise2, sunset2 = _sun_times(self._DT, 34.2104, -77.7964, "America/New_York")
        # Should be equal (defaults match module constants)
        diff_rise = abs((sunrise1 - sunrise2).total_seconds())
        diff_set = abs((sunset1 - sunset2).total_seconds())
        assert diff_rise < 60  # within a minute
        assert diff_set < 60

    def test_default_lat_branch(self):
        # lat=0 triggers the lat = _LAT default branch
        sunrise, sunset = _sun_times(self._DT, 0, -77.8, "America/New_York")
        assert sunrise < sunset

    def test_default_lng_branch(self):
        # lng=0 triggers the lng = _LNG default branch
        sunrise, sunset = _sun_times(self._DT, 34.2, 0, "America/New_York")
        assert sunrise < sunset

    def test_returns_timezone_aware_datetimes(self):
        sunrise, sunset = _sun_times(self._DT, 34.2, -77.8, "America/New_York")
        assert sunrise.tzinfo is not None
        assert sunset.tzinfo is not None


# ---------------------------------------------------------------------------
# _moon_phase
# ---------------------------------------------------------------------------


class TestMoonPhase:
    def test_returns_float_between_0_and_1(self):
        dt = datetime(2026, 6, 21, tzinfo=ZoneInfo("UTC"))
        phase = _moon_phase(dt)
        assert 0.0 <= phase <= 1.0

    def test_naive_datetime_handled(self):
        dt_naive = datetime(2026, 6, 21, 12, 0, 0)  # no tzinfo
        phase = _moon_phase(dt_naive)
        assert 0.0 <= phase <= 1.0

    def test_known_new_moon(self):
        # 2026-02-17 is approximately a new moon
        dt = datetime(2026, 2, 17, tzinfo=ZoneInfo("UTC"))
        phase = _moon_phase(dt)
        assert phase < 0.1 or phase > 0.9  # close to 0 or 1 (both = new moon)


# ---------------------------------------------------------------------------
# _moon_transit_hours
# ---------------------------------------------------------------------------


class TestMoonTransitHours:
    def test_returns_two_floats_in_24h_range(self):
        dt = datetime(2026, 6, 21, tzinfo=ZoneInfo("America/New_York"))
        overhead, underfoot = _moon_transit_hours(dt, -77.8)
        assert 0.0 <= overhead < 24.0
        assert 0.0 <= underfoot < 24.0

    def test_underfoot_is_roughly_12_hours_from_overhead(self):
        dt = datetime(2026, 6, 21, tzinfo=ZoneInfo("America/New_York"))
        overhead, underfoot = _moon_transit_hours(dt, -77.8)
        diff = (underfoot - overhead) % 24.0
        assert abs(diff - 12.0) < 1.0  # within 1 hour

    def test_naive_datetime_uses_default_offset(self):
        # No tzinfo → falls back to utc_offset = -5 (EST default)
        dt_naive = datetime(2026, 6, 21, 12, 0, 0)
        overhead, underfoot = _moon_transit_hours(dt_naive, -77.8)
        assert 0.0 <= overhead < 24.0
        assert 0.0 <= underfoot < 24.0


# ---------------------------------------------------------------------------
# _sun_event_time
# ---------------------------------------------------------------------------


class TestSunEventTime:
    def test_civil_dawn_before_sunrise(self):
        dt = datetime(2026, 6, 21, tzinfo=ZoneInfo("America/New_York"))
        civil_dawn = _sun_event_time(dt, 34.2, -77.8, "America/New_York", 96.0, True)
        sunrise, _ = _sun_times(dt, 34.2, -77.8, "America/New_York")
        assert civil_dawn < sunrise

    def test_naive_datetime_handled(self):
        dt_naive = datetime(2026, 6, 21)
        event = _sun_event_time(dt_naive, 34.2, -77.8, "America/New_York", 90.833, True)
        assert event.tzinfo is not None


# ---------------------------------------------------------------------------
# compute_twilight_times
# ---------------------------------------------------------------------------


class TestComputeTwilightTimes:
    def test_returns_all_expected_keys(self):
        dt = datetime(2026, 6, 21, tzinfo=ZoneInfo("America/New_York"))
        result = compute_twilight_times(dt, 34.2, -77.8, "America/New_York")
        for key in ("civil_dawn", "civil_dusk", "nautical_dawn", "nautical_dusk",
                    "astronomical_dawn", "astronomical_dusk", "golden_am", "golden_pm"):
            assert key in result

    def test_golden_hour_format(self):
        dt = datetime(2026, 6, 21, tzinfo=ZoneInfo("America/New_York"))
        result = compute_twilight_times(dt, 34.2, -77.8, "America/New_York")
        assert " - " in result["golden_am"]


# ---------------------------------------------------------------------------
# compute_lunar_details
# ---------------------------------------------------------------------------


class TestComputeLunarDetails:
    def test_returns_all_keys(self):
        dt = datetime(2026, 6, 21, tzinfo=ZoneInfo("America/New_York"))
        result = compute_lunar_details(dt, -77.8, "America/New_York")
        for key in ("moonrise", "moonset", "age_days", "distance_km"):
            assert key in result

    def test_age_days_in_range(self):
        dt = datetime(2026, 6, 21, tzinfo=ZoneInfo("America/New_York"))
        result = compute_lunar_details(dt, -77.8, "America/New_York")
        assert 0 <= result["age_days"] <= 29.6

    def test_naive_datetime_handled(self):
        dt_naive = datetime(2026, 6, 21)
        result = compute_lunar_details(dt_naive, -77.8, "America/New_York")
        assert "moonrise" in result


# ---------------------------------------------------------------------------
# compute_solunar_times — phase-name / rating branches
# ---------------------------------------------------------------------------


class TestComputeSolunarTimes:
    _DT = datetime(2026, 6, 21, tzinfo=ZoneInfo("America/New_York"))
    _LNG = -77.8

    def _run(self, phase_frac: float) -> dict:
        with patch.object(astro_mod, "_moon_phase", return_value=phase_frac):
            return compute_solunar_times(self._DT, 34.2, self._LNG, "America/New_York")

    def test_new_moon(self):
        result = self._run(0.02)
        assert result["moon_phase"] == "New Moon"
        assert result["rating"] == "Excellent"

    def test_waxing_crescent_poor(self):
        result = self._run(0.10)
        assert result["moon_phase"] == "Waxing Crescent"
        assert result["rating"] == "Poor"

    def test_waxing_crescent_fair(self):
        result = self._run(0.23)
        assert result["moon_phase"] == "Waxing Crescent"
        assert result["rating"] == "Fair"

    def test_first_quarter(self):
        result = self._run(0.27)
        assert result["moon_phase"] == "First Quarter"
        assert result["rating"] == "Good"

    def test_waxing_gibbous(self):
        result = self._run(0.40)
        assert result["moon_phase"] == "Waxing Gibbous"
        assert result["rating"] == "Good"

    def test_full_moon(self):
        result = self._run(0.50)
        assert result["moon_phase"] == "Full Moon"
        assert result["rating"] == "Excellent"

    def test_waning_gibbous(self):
        result = self._run(0.65)
        assert result["moon_phase"] == "Waning Gibbous"
        assert result["rating"] == "Good"

    def test_last_quarter(self):
        result = self._run(0.77)
        assert result["moon_phase"] == "Last Quarter"
        assert result["rating"] == "Good"

    def test_waning_crescent_fair(self):
        result = self._run(0.84)
        assert result["moon_phase"] == "Waning Crescent"
        assert result["rating"] == "Fair"

    def test_waning_crescent_poor_else(self):
        result = self._run(0.91)
        assert result["moon_phase"] == "Waning Crescent"
        assert result["rating"] == "Poor"

    def test_returns_major_and_minor_periods(self):
        result = self._run(0.50)
        assert len(result["major_periods"]) == 2
        assert len(result["minor_periods"]) == 2
        for period in result["major_periods"] + result["minor_periods"]:
            assert "start" in period
            assert "end" in period

    def test_naive_datetime_handled(self):
        dt_naive = datetime(2026, 6, 21)
        with patch.object(astro_mod, "_moon_phase", return_value=0.50):
            result = compute_solunar_times(dt_naive, 34.2, self._LNG, "America/New_York")
        assert "moon_phase" in result

    def test_illumination_new_moon_near_zero(self):
        result = self._run(0.0)
        assert result["illumination_pct"] < 5.0

    def test_illumination_full_moon_near_100(self):
        result = self._run(0.5)
        assert result["illumination_pct"] > 95.0
