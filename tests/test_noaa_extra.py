"""Tests for services/noaa.py functions not covered by test_noaa_ndbc.py:

fetch_water_temperature, get_water_temp (3-tier fallback), _try_coops_wind,
fetch_tide_predictions, fetch_currents_predictions, fetch_currents_observation,
and build_tide_chart_svg.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

import services.noaa as noaa


def _mock_resp(json_body, status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = json_body
    m.status_code = status_code
    return m


class TestFetchWaterTemperature:
    def test_returns_float_on_success(self):
        body = {"data": [{"v": "72.5"}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            result = noaa.fetch_water_temperature("8658163")
        assert result == 72.5

    def test_returns_none_when_no_rows(self):
        with patch.object(noaa, "http_get", return_value=_mock_resp({"data": []})):
            result = noaa.fetch_water_temperature("8658163")
        assert result is None

    def test_returns_none_on_network_error(self):
        with patch.object(
            noaa, "http_get", side_effect=requests.ConnectionError("down")
        ):
            result = noaa.fetch_water_temperature("8658163")
        assert result is None

    def test_uses_default_station_when_empty(self):
        body = {"data": [{"v": "70.0"}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)) as mock_get:
            noaa.fetch_water_temperature()
        url = mock_get.call_args.args[0]
        assert noaa.WATER_TEMP_STATION in url


class TestGetWaterTemp:
    def test_uses_live_reading_when_available(self):
        sources: list[str] = []
        with patch.object(noaa, "fetch_water_temperature", return_value=68.0):
            temp, is_live = noaa.get_water_temp(6, sources_used=sources)
        assert temp == 68.0
        assert is_live is True
        assert "NOAA CO-OPS water temperature" in sources

    def test_falls_back_to_location_monthly_avg(self):
        fallbacks: list[str] = []
        location = {"temp_region": "nc_south", "coops_station": "1234567"}
        with (
            patch.object(noaa, "fetch_water_temperature", return_value=None),
            patch.object(noaa, "get_monthly_water_temps", return_value={6: 75.0}),
        ):
            temp, is_live = noaa.get_water_temp(
                6, location=location, fallbacks_triggered=fallbacks
            )
        assert temp == 75.0
        assert is_live is False
        assert "monthly_location_water_temp" in fallbacks

    def test_falls_back_to_generic_regional_avg_when_no_location(self):
        fallbacks: list[str] = []
        with patch.object(noaa, "fetch_water_temperature", return_value=None):
            temp, is_live = noaa.get_water_temp(7, fallbacks_triggered=fallbacks)
        assert temp == float(noaa.MONTHLY_AVG_WATER_TEMP_F[7])
        assert is_live is False
        assert "monthly_regional_water_temp" in fallbacks

    def test_works_without_optional_tracking_lists(self):
        with patch.object(noaa, "fetch_water_temperature", return_value=None):
            temp, is_live = noaa.get_water_temp(1)
        assert temp == float(noaa.MONTHLY_AVG_WATER_TEMP_F[1])
        assert is_live is False

    def test_uses_station_from_location_dict(self):
        location = {"coops_station": "9999999"}
        with patch.object(
            noaa, "fetch_water_temperature", return_value=65.0
        ) as mock_fetch:
            noaa.get_water_temp(3, location=location)
        mock_fetch.assert_called_once_with("9999999")


class TestTryCoopsWind:
    def test_returns_wind_range_and_direction(self):
        body = {"data": [{"s": "10.0", "g": "15.0", "d": "SW"}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            wind_range, wave, direction = noaa._try_coops_wind("8658163")
        assert wind_range == (10.0, 15.0)
        assert wave is None
        assert direction == "SW"

    def test_uses_speed_when_gust_is_zero(self):
        body = {"data": [{"s": "10.0", "g": "0.00", "d": "NE"}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            wind_range, _, _ = noaa._try_coops_wind("8658163")
        assert wind_range == (10.0, 10.0)

    def test_returns_none_triple_when_no_speed(self):
        body = {"data": [{}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            result = noaa._try_coops_wind("8658163")
        assert result == (None, None, None)

    def test_direction_none_when_missing(self):
        body = {"data": [{"s": "5.0"}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            _, _, direction = noaa._try_coops_wind("8658163")
        assert direction is None

    def test_propagates_exception_on_network_error(self):
        with patch.object(
            noaa, "http_get", side_effect=requests.ConnectionError("down")
        ):
            with pytest.raises(requests.ConnectionError):
                noaa._try_coops_wind("8658163")

    def test_uses_default_station_when_empty(self):
        body = {"data": [{"s": "5.0"}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)) as mock_get:
            noaa._try_coops_wind()
        url = mock_get.call_args.args[0]
        assert noaa.WATER_TEMP_STATION in url


class TestFetchTidePredictions:
    def test_parses_high_low_tides(self):
        body = {
            "predictions": [
                {"t": "2024-06-01 06:30", "v": "5.234", "type": "H"},
                {"t": "2024-06-01 12:45", "v": "1.100", "type": "L"},
            ]
        }
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            tides = noaa.fetch_tide_predictions("8658163")
        assert len(tides) == 2
        assert tides[0]["type"] == "High"
        assert tides[0]["height_ft"] == "5.2"
        assert tides[1]["type"] == "Low"

    def test_handles_unparseable_time(self):
        body = {"predictions": [{"t": "garbage", "v": "2.0", "type": "H"}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            tides = noaa.fetch_tide_predictions("8658163")
        assert tides[0]["time"] == "garbage"
        assert tides[0]["hour"] == 12.0

    def test_network_error_returns_empty_list(self):
        with patch.object(
            noaa, "http_get", side_effect=requests.ConnectionError("down")
        ):
            tides = noaa.fetch_tide_predictions("8658163")
        assert tides == []

    def test_empty_predictions_returns_empty_list(self):
        with patch.object(
            noaa, "http_get", return_value=_mock_resp({"predictions": []})
        ):
            tides = noaa.fetch_tide_predictions("8658163")
        assert tides == []


class TestFetchCurrentsPredictions:
    def test_parses_flood_ebb_events(self):
        body = {
            "cp": [
                {"Time": "2024-06-01 06:30", "Velocity_Major": "1.50", "Type": "flood"},
                {"Time": "2024-06-01 12:45", "Velocity_Major": "0.00", "Type": "slack"},
            ]
        }
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            events = noaa.fetch_currents_predictions("ACT1234")
        assert len(events) == 2
        assert events[0]["event"] == "Flood"
        assert events[0]["speed_kt"] == "1.50"

    def test_skips_rows_without_time(self):
        body = {"cp": [{"Velocity_Major": "1.0", "Type": "flood"}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            events = noaa.fetch_currents_predictions("ACT1234")
        assert events == []

    def test_invalid_velocity_defaults_to_zero(self):
        body = {
            "cp": [
                {"Time": "2024-06-01 06:30", "Velocity_Major": "bad", "Type": "flood"}
            ]
        }
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            events = noaa.fetch_currents_predictions("ACT1234")
        assert events[0]["speed_kt"] == "0.00"

    def test_network_error_returns_empty_list(self):
        with patch.object(
            noaa, "http_get", side_effect=requests.ConnectionError("down")
        ):
            events = noaa.fetch_currents_predictions("ACT1234")
        assert events == []


class TestFetchCurrentsObservation:
    def test_parses_latest_observation(self):
        body = {"data": [{"t": "2024-06-01 06:30", "s": "1.25", "d": "180"}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            result = noaa.fetch_currents_observation("ACT1234")
        assert result["event"] == "Observed"
        assert result["speed_kt"] == "1.25"
        assert result["direction"] == "180"

    def test_returns_none_when_no_rows(self):
        with patch.object(noaa, "http_get", return_value=_mock_resp({"data": []})):
            result = noaa.fetch_currents_observation("ACT1234")
        assert result is None

    def test_missing_time_defaults_to_now(self):
        body = {"data": [{"s": "1.0"}]}
        with patch.object(noaa, "http_get", return_value=_mock_resp(body)):
            result = noaa.fetch_currents_observation("ACT1234")
        assert result["time"] == "Now"

    def test_network_error_returns_none(self):
        with patch.object(
            noaa, "http_get", side_effect=requests.ConnectionError("down")
        ):
            result = noaa.fetch_currents_observation("ACT1234")
        assert result is None


class TestBuildTideChartSvg:
    def test_returns_none_with_fewer_than_two_tides(self):
        assert noaa.build_tide_chart_svg([]) is None
        assert (
            noaa.build_tide_chart_svg(
                [
                    {
                        "hour": 6,
                        "height_num": 1.0,
                        "type": "High",
                        "time": "6 AM",
                        "height_ft": "1.0",
                    }
                ]
            )
            is None
        )

    def test_returns_none_when_fewer_than_two_in_window(self):
        tides = [
            {
                "hour": 6,
                "height_num": 1.0,
                "type": "High",
                "time": "6 AM",
                "height_ft": "1.0",
            },
            {
                "hour": 40,
                "height_num": 2.0,
                "type": "Low",
                "time": "4 PM next",
                "height_ft": "2.0",
            },
        ]
        assert noaa.build_tide_chart_svg(tides) is None

    def test_builds_chart_with_valid_points(self):
        tides = [
            {
                "hour": 2.0,
                "height_num": 1.0,
                "type": "Low",
                "time": "2:00 AM",
                "height_ft": "1.0",
            },
            {
                "hour": 8.0,
                "height_num": 5.0,
                "type": "High",
                "time": "8:00 AM",
                "height_ft": "5.0",
            },
            {
                "hour": 14.0,
                "height_num": 1.5,
                "type": "Low",
                "time": "2:00 PM",
                "height_ft": "1.5",
            },
        ]
        result = noaa.build_tide_chart_svg(tides)
        assert result is not None
        assert result["viewBox"] == "0 0 600 140"
        assert len(result["markers"]) == 3
        assert result["path"].startswith("M")
        assert result["now_marker"] is None

    def test_markers_carry_hour_for_frontend_matching(self):
        tides = [
            {
                "hour": 2.0,
                "height_num": 1.0,
                "type": "Low",
                "time": "2:00 AM",
                "height_ft": "1.0",
            },
            {
                "hour": 8.0,
                "height_num": 5.0,
                "type": "High",
                "time": "8:00 AM",
                "height_ft": "5.0",
            },
        ]
        result = noaa.build_tide_chart_svg(tides)
        hours = [m["hour"] for m in result["markers"]]
        assert hours == [2.0, 8.0]

    def test_curve_is_dense_monotonic_and_spans_the_full_range(self):
        tides = [
            {
                "hour": 2.0,
                "height_num": 1.0,
                "type": "Low",
                "time": "2:00 AM",
                "height_ft": "1.0",
            },
            {
                "hour": 8.0,
                "height_num": 5.0,
                "type": "High",
                "time": "8:00 AM",
                "height_ft": "5.0",
            },
            {
                "hour": 14.0,
                "height_num": 1.5,
                "type": "Low",
                "time": "2:00 PM",
                "height_ft": "1.5",
            },
        ]
        result = noaa.build_tide_chart_svg(tides)
        curve = result["curve"]
        # Roughly 6 samples/hour over a 12h span
        assert len(curve) > 60
        assert curve[0]["hour"] == 2.0
        assert curve[-1]["hour"] == 14.0
        hours = [c["hour"] for c in curve]
        assert hours == sorted(hours)
        xs = [c["x"] for c in curve]
        assert xs == sorted(xs)
        # Cosine interpolation stays within the bracketing extrema
        assert all(1.0 <= c["height"] <= 5.0 for c in curve)

    def test_curve_used_for_now_marker_matches_height_interpolation(self):
        tides = [
            {
                "hour": 2.0,
                "height_num": 1.0,
                "type": "Low",
                "time": "2:00 AM",
                "height_ft": "1.0",
            },
            {
                "hour": 8.0,
                "height_num": 5.0,
                "type": "High",
                "time": "8:00 AM",
                "height_ft": "5.0",
            },
        ]
        result = noaa.build_tide_chart_svg(tides, now_hour=5.0)
        # Midpoint of a half-cosine ramp is exactly the midpoint height.
        mid_sample = min(result["curve"], key=lambda c: abs(c["hour"] - 5.0))
        assert mid_sample["height"] == pytest.approx(3.0, abs=0.1)

    def test_includes_now_marker_when_now_hour_in_range(self):
        tides = [
            {
                "hour": 2.0,
                "height_num": 1.0,
                "type": "Low",
                "time": "2:00 AM",
                "height_ft": "1.0",
            },
            {
                "hour": 8.0,
                "height_num": 5.0,
                "type": "High",
                "time": "8:00 AM",
                "height_ft": "5.0",
            },
        ]
        result = noaa.build_tide_chart_svg(tides, now_hour=5.0)
        assert result["now_marker"] is not None
        assert "cx" in result["now_marker"]
        assert "cy" in result["now_marker"]

    def test_now_marker_omitted_when_outside_range(self):
        tides = [
            {
                "hour": 2.0,
                "height_num": 1.0,
                "type": "Low",
                "time": "2:00 AM",
                "height_ft": "1.0",
            },
            {
                "hour": 8.0,
                "height_num": 5.0,
                "type": "High",
                "time": "8:00 AM",
                "height_ft": "5.0",
            },
        ]
        result = noaa.build_tide_chart_svg(tides, now_hour=20.0)
        assert result["now_marker"] is None

    def test_handles_equal_heights_without_division_error(self):
        tides = [
            {
                "hour": 2.0,
                "height_num": 3.0,
                "type": "Low",
                "time": "2:00 AM",
                "height_ft": "3.0",
            },
            {
                "hour": 8.0,
                "height_num": 3.0,
                "type": "High",
                "time": "8:00 AM",
                "height_ft": "3.0",
            },
        ]
        result = noaa.build_tide_chart_svg(tides)
        assert result is not None

    def test_handles_equal_hours_without_division_error(self):
        tides = [
            {
                "hour": 5.0,
                "height_num": 1.0,
                "type": "Low",
                "time": "5:00 AM",
                "height_ft": "1.0",
            },
            {
                "hour": 5.0,
                "height_num": 4.0,
                "type": "High",
                "time": "5:00 AM",
                "height_ft": "4.0",
            },
        ]
        result = noaa.build_tide_chart_svg(tides)
        assert result is not None
