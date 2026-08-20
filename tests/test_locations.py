"""Tests for locations.py — water temps, geocoding, and location search."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from locations import (
    COASTAL_LOCATIONS,
    find_nearest_locations,
    geocode_zip,
    get_fallback_conditions,
    get_location,
    get_monthly_water_temps,
    get_water_temp,
)


# ---------------------------------------------------------------------------
# get_water_temp
# ---------------------------------------------------------------------------


class TestGetWaterTemp:
    def test_known_region_and_month(self):
        # nc_south is the module default; month 7 (July) should have a warm value
        temp = get_water_temp("nc_south", 7)
        assert temp is not None
        assert temp > 70  # July water is warm

    def test_unknown_region_returns_none(self):
        temp = get_water_temp("does_not_exist", 6)
        assert temp is None

    def test_out_of_range_month_returns_none(self):
        # Month 0 or 13 should not exist in any region dict
        temp = get_water_temp("nc_south", 13)
        assert temp is None

    def test_offset_applied(self):
        base = get_water_temp("nc_south", 6, offset=0.0)
        shifted = get_water_temp("nc_south", 6, offset=5.0)
        assert base is not None and shifted is not None
        assert shifted == pytest.approx(base + 5.0)


# ---------------------------------------------------------------------------
# get_location
# ---------------------------------------------------------------------------


class TestGetLocation:
    def test_known_id(self):
        # wrightsville-beach-nc is the default in CLAUDE.md
        loc = get_location("wrightsville-beach-nc")
        if loc is None:
            # fall back to whatever the first location is
            loc = get_location(COASTAL_LOCATIONS[0]["id"])
        assert loc is not None
        assert "lat" in loc
        assert "lng" in loc

    def test_unknown_id_returns_none(self):
        assert get_location("this-does-not-exist-xyz") is None


# ---------------------------------------------------------------------------
# get_monthly_water_temps
# ---------------------------------------------------------------------------


class TestGetMonthlyWaterTemps:
    def _first_loc(self) -> dict:
        for loc in COASTAL_LOCATIONS:
            if "temp_region" in loc:
                return loc
        return COASTAL_LOCATIONS[0]

    def test_returns_12_months(self):
        loc = self._first_loc()
        temps = get_monthly_water_temps(loc)
        assert len(temps) == 12
        for month in range(1, 13):
            assert month in temps

    def test_offset_applied(self):
        loc = dict(self._first_loc())
        base = get_monthly_water_temps(loc)
        loc["temp_offset"] = 3.0
        shifted = get_monthly_water_temps(loc)
        for m in range(1, 13):
            assert shifted[m] == pytest.approx(base[m] + 3.0)

    def test_zero_offset_returns_base_dict(self):
        loc = dict(self._first_loc())
        loc["temp_offset"] = 0
        temps = get_monthly_water_temps(loc)
        assert len(temps) == 12


# ---------------------------------------------------------------------------
# get_fallback_conditions
# ---------------------------------------------------------------------------


class TestGetFallbackConditions:
    def test_returns_wind_and_wave_for_valid_month(self):
        loc = {"conditions_region": "atlantic_mid"}
        wind, wave, direction = get_fallback_conditions(loc, 6)
        assert isinstance(wind, tuple) and len(wind) == 2
        assert isinstance(wave, tuple) and len(wave) == 2
        assert isinstance(direction, str)

    def test_clamps_month_below_1(self):
        loc = {"conditions_region": "atlantic_mid"}
        wind, wave, direction = get_fallback_conditions(loc, 0)
        assert wind is not None

    def test_clamps_month_above_12(self):
        loc = {"conditions_region": "atlantic_mid"}
        wind, wave, direction = get_fallback_conditions(loc, 99)
        assert wind is not None

    def test_unknown_region_falls_back_to_default(self):
        loc = {"conditions_region": "unknown_region"}
        wind, wave, direction = get_fallback_conditions(loc, 6)
        assert wind is not None


# ---------------------------------------------------------------------------
# geocode_zip
# ---------------------------------------------------------------------------


class TestGeocodeZip:
    def _mock_resp(self, lat: float, lng: float) -> MagicMock:
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {
            "places": [{"latitude": str(lat), "longitude": str(lng)}]
        }
        return m

    def test_valid_zip_returns_coords(self):
        with patch("locations.http_get", return_value=self._mock_resp(34.2, -77.8)):
            result = geocode_zip("28403")
        assert result is not None
        lat, lng = result
        assert lat == pytest.approx(34.2)
        assert lng == pytest.approx(-77.8)

    def test_zip_plus_4_truncated(self):
        with patch("locations.http_get", return_value=self._mock_resp(34.2, -77.8)):
            result = geocode_zip("28403-1234")
        # Should strip the +4 and succeed
        assert result is not None

    def test_non_numeric_returns_none(self):
        result = geocode_zip("ABCDE")
        assert result is None

    def test_wrong_length_returns_none(self):
        assert geocode_zip("1234") is None
        assert geocode_zip("123456") is None

    def test_non_200_status_returns_none(self):
        m = MagicMock()
        m.status_code = 404
        with patch("locations.http_get", return_value=m):
            result = geocode_zip("28403")
        assert result is None

    def test_empty_places_returns_none(self):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"places": []}
        with patch("locations.http_get", return_value=m):
            result = geocode_zip("28403")
        assert result is None

    def test_exception_returns_none(self):
        with patch("locations.http_get", side_effect=Exception("network error")):
            result = geocode_zip("28403")
        assert result is None


# ---------------------------------------------------------------------------
# find_nearest_locations
# ---------------------------------------------------------------------------


class TestFindNearestLocations:
    def test_returns_sorted_by_distance(self):
        # Use Wrightsville Beach NC coords — should find nearby NC locations
        results = find_nearest_locations(34.2104, -77.7964, n=5)
        if len(results) >= 2:
            assert results[0]["distance_miles"] <= results[1]["distance_miles"]

    def test_excludes_locations_beyond_max_miles(self):
        # 0.1 mile radius — only extremely close points (none most likely)
        results = find_nearest_locations(34.2104, -77.7964, n=5, max_miles=0.1)
        for r in results:
            assert r["distance_miles"] <= 0.1

    def test_returns_at_most_n(self):
        results = find_nearest_locations(34.2104, -77.7964, n=3, max_miles=9999)
        assert len(results) <= 3

    def test_adds_distance_miles_field(self):
        results = find_nearest_locations(34.2104, -77.7964, n=1)
        if results:
            assert "distance_miles" in results[0]
