"""Tests for services/nws.py — NWS marine forecast parsing and alert fetching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.nws import (
    parse_conditions,
    fetch_weather_alerts,
    fetch_state_alerts,
    fetch_current_weather,
    _try_nws_forecast,
    _try_nws_gridpoint,
    _fetch_nws_extended,
)


# ---------------------------------------------------------------------------
# parse_conditions — pure function, no HTTP
# ---------------------------------------------------------------------------


class TestParseConditions:
    def _period(self, text: str) -> dict:
        return {"detailedForecast": text}

    def test_empty_periods_returns_nones(self):
        wind, wave, direction = parse_conditions([])
        assert wind is None
        assert wave is None
        assert direction is None

    def test_parses_wind_range_knots(self):
        periods = [self._period("Southwest wind 10 to 15 kt.")]
        wind, wave, direction = parse_conditions(periods)
        assert wind == (10.0, 15.0)
        assert direction == "SW"

    def test_parses_wind_single_speed(self):
        periods = [self._period("North wind around 8 kt.")]
        wind, _wave, direction = parse_conditions(periods)
        assert wind == (8.0, 8.0)
        assert direction == "N"

    def test_parses_wave_range_feet(self):
        periods = [self._period("Seas 3 to 5 ft.")]
        _wind, wave, _dir = parse_conditions(periods)
        assert wave == (3.0, 5.0)

    def test_parses_seas_single_value(self):
        periods = [self._period("Seas around 2 feet.")]
        _wind, wave, _dir = parse_conditions(periods)
        assert wave == (2.0, 2.0)

    def test_parses_waves_keyword(self):
        periods = [self._period("Waves 1 to 2 ft.")]
        _wind, wave, _dir = parse_conditions(periods)
        assert wave == (1.0, 2.0)

    def test_direction_spelled_out(self):
        periods = [self._period("Northeast wind 12 to 18 kt.")]
        _wind, _wave, direction = parse_conditions(periods)
        assert direction == "NE"

    def test_direction_southwest_spelled_out(self):
        periods = [self._period("Southwest wind 5 kt.")]
        _wind, _wave, direction = parse_conditions(periods)
        assert direction == "SW"

    def test_knots_abbreviation_kt(self):
        periods = [self._period("East wind 20 kt.")]
        wind, _wave, direction = parse_conditions(periods)
        assert wind == (20.0, 20.0)
        assert direction == "E"

    def test_knots_spelled_out(self):
        periods = [self._period("West wind 10 to 15 knots.")]
        wind, _wave, direction = parse_conditions(periods)
        assert wind == (10.0, 15.0)
        assert direction == "W"

    def test_multiple_periods_takes_min_low_max_high_wind(self):
        periods = [
            self._period("South wind 5 to 10 kt."),
            self._period("South wind 15 to 20 kt."),
        ]
        wind, _wave, _dir = parse_conditions(periods)
        # min of lows = 5, max of highs = 20
        assert wind == (5.0, 20.0)

    def test_multiple_periods_takes_min_low_max_high_wave(self):
        periods = [
            self._period("Seas 2 to 3 ft."),
            self._period("Seas 4 to 6 ft."),
        ]
        _wind, wave, _dir = parse_conditions(periods)
        assert wave == (2.0, 6.0)

    def test_first_period_direction_wins(self):
        periods = [
            self._period("North wind 10 kt."),
            self._period("South wind 15 kt."),
        ]
        _wind, _wave, direction = parse_conditions(periods)
        assert direction == "N"

    def test_only_considers_first_three_periods(self):
        periods = [
            self._period(""),
            self._period(""),
            self._period(""),
            self._period("North wind 99 kt. Seas 99 ft."),
        ]
        wind, wave, _dir = parse_conditions(periods)
        # 4th period should be ignored
        assert wind is None
        assert wave is None

    def test_no_wind_match_returns_none_wind(self):
        periods = [self._period("Seas 3 to 5 ft. No wind info.")]
        wind, wave, _dir = parse_conditions(periods)
        assert wind is None
        assert wave == (3.0, 5.0)

    def test_no_wave_match_returns_none_wave(self):
        periods = [self._period("Southwest wind 10 to 15 kt. Calm seas.")]
        _wind, wave, _dir = parse_conditions(periods)
        assert wave is None

    def test_variable_wind_direction(self):
        periods = [self._period("Variable wind 5 kt.")]
        _wind, _wave, direction = parse_conditions(periods)
        assert direction == "VARIABLE"

    def test_period_without_detailed_forecast_key(self):
        # Should not raise; missing key should be treated as empty string
        periods = [{}]
        wind, wave, direction = parse_conditions(periods)
        assert wind is None
        assert wave is None
        assert direction is None


# ---------------------------------------------------------------------------
# fetch_weather_alerts — mocked HTTP
# ---------------------------------------------------------------------------


class TestFetchWeatherAlerts:
    def _mock_resp(self, body: dict) -> MagicMock:
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = body
        return m

    def test_returns_alerts_from_geojson_features(self):
        body = {
            "features": [
                {
                    "properties": {
                        "event": "Small Craft Advisory",
                        "severity": "Moderate",
                        "headline": "Watch out",
                        "description": "High winds expected.",
                    }
                }
            ]
        }
        with patch("services.nws.http_get", return_value=self._mock_resp(body)):
            alerts = fetch_weather_alerts(34.0, -77.0)
        assert len(alerts) == 1
        assert alerts[0]["event"] == "Small Craft Advisory"
        assert alerts[0]["severity"] == "Moderate"

    def test_returns_alerts_from_json_ld_graph(self):
        body = {
            "@graph": [
                {
                    "event": "Gale Warning",
                    "severity": "Severe",
                    "headline": "Gale",
                    "description": "Gale-force winds.",
                }
            ]
        }
        with patch("services.nws.http_get", return_value=self._mock_resp(body)):
            alerts = fetch_weather_alerts(34.0, -77.0)
        assert len(alerts) == 1
        assert alerts[0]["event"] == "Gale Warning"

    def test_skips_items_without_event(self):
        body = {
            "features": [
                {
                    "properties": {
                        "event": "",
                        "severity": "Minor",
                        "headline": "",
                        "description": "",
                    }
                },
                {
                    "properties": {
                        "event": "Coastal Flood Watch",
                        "severity": "Minor",
                        "headline": "Flooding",
                        "description": "",
                    }
                },
            ]
        }
        with patch("services.nws.http_get", return_value=self._mock_resp(body)):
            alerts = fetch_weather_alerts(34.0, -77.0)
        assert len(alerts) == 1
        assert alerts[0]["event"] == "Coastal Flood Watch"

    def test_returns_empty_list_on_http_error(self):
        with patch("services.nws.http_get", side_effect=Exception("timeout")):
            alerts = fetch_weather_alerts(34.0, -77.0)
        assert alerts == []

    def test_limits_to_five_alerts(self):
        body = {
            "features": [
                {
                    "properties": {
                        "event": f"Alert {i}",
                        "severity": "Minor",
                        "headline": "",
                        "description": "",
                    }
                }
                for i in range(10)
            ]
        }
        with patch("services.nws.http_get", return_value=self._mock_resp(body)):
            alerts = fetch_weather_alerts(34.0, -77.0)
        assert len(alerts) == 5

    def test_description_truncated_to_300_chars(self):
        long_desc = "X" * 500
        body = {
            "features": [
                {
                    "properties": {
                        "event": "Test",
                        "severity": "Minor",
                        "headline": "",
                        "description": long_desc,
                    }
                }
            ]
        }
        with patch("services.nws.http_get", return_value=self._mock_resp(body)):
            alerts = fetch_weather_alerts(34.0, -77.0)
        assert len(alerts[0]["description"]) == 300


# ---------------------------------------------------------------------------
# fetch_state_alerts — mocked HTTP
# ---------------------------------------------------------------------------


class TestFetchStateAlerts:
    def test_empty_state_returns_empty(self):

        result = fetch_state_alerts("")
        assert result == []

    def test_returns_state_alerts(self):

        body = {
            "features": [
                {
                    "properties": {
                        "event": "Dense Fog Advisory",
                        "severity": "Minor",
                        "headline": "Fog",
                        "description": "Visibility reduced.",
                    }
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = body

        with patch("services.nws.http_get", return_value=mock_resp):
            alerts = fetch_state_alerts("NC")
        assert len(alerts) == 1
        assert alerts[0]["event"] == "Dense Fog Advisory"

    def test_state_code_uppercased(self):

        body = {"features": []}
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = body

        with patch("services.nws.http_get", return_value=mock_resp) as mock_get:
            fetch_state_alerts("nc")
        call_url = mock_get.call_args[0][0]
        assert "NC" in call_url

    def test_returns_empty_on_exception(self):

        with patch("services.nws.http_get", side_effect=Exception("network error")):
            alerts = fetch_state_alerts("FL")
        assert alerts == []


# ---------------------------------------------------------------------------
# fetch_current_weather — mocked HTTP
# ---------------------------------------------------------------------------


class TestFetchCurrentWeather:
    def _make_pts_resp(
        self, obs_url: str = "https://api.weather.gov/gridpoints/LWX/96,70/stations"
    ) -> MagicMock:
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"properties": {"observationStations": obs_url}}
        return m

    def _make_stations_resp(self, station_ids: list) -> MagicMock:
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"observationStations": station_ids}
        return m

    def _make_obs_resp(self, props: dict) -> MagicMock:
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"properties": props}
        return m

    def test_returns_temp_and_humidity(self):
        pts = self._make_pts_resp()
        stations = self._make_stations_resp(["https://api.weather.gov/stations/KORF"])
        obs = self._make_obs_resp(
            {
                "temperature": {"value": 20.0},  # 68°F
                "relativeHumidity": {"value": 60.0},
                "textDescription": "Mostly Cloudy",
                "windChill": {"value": None},
            }
        )
        with patch("services.nws.http_get", side_effect=[pts, stations, obs]):
            result = fetch_current_weather(34.0, -77.0)
        assert result is not None
        assert result["air_temp_f"] == pytest.approx(68.0, abs=0.1)
        assert result["humidity"] == 60.0
        assert result["description"] == "Mostly Cloudy"

    def test_computes_heat_index_when_hot_and_humid(self):
        pts = self._make_pts_resp()
        stations = self._make_stations_resp(["https://api.weather.gov/stations/KMHV"])
        obs = self._make_obs_resp(
            {
                "temperature": {"value": 30.0},  # 86°F
                "relativeHumidity": {"value": 80.0},
                "textDescription": "Sunny",
                "windChill": {"value": None},
            }
        )
        with patch("services.nws.http_get", side_effect=[pts, stations, obs]):
            result = fetch_current_weather(34.0, -77.0)
        assert result is not None
        assert "feels_like_f" in result
        assert result["feels_like_f"] > result["air_temp_f"]  # heat index > air temp

    def test_no_heat_index_when_cool(self):
        pts = self._make_pts_resp()
        stations = self._make_stations_resp(["https://api.weather.gov/stations/KORF"])
        obs = self._make_obs_resp(
            {
                "temperature": {"value": 15.0},  # 59°F
                "relativeHumidity": {"value": 70.0},
                "textDescription": "Cloudy",
                "windChill": {"value": None},
            }
        )
        with patch("services.nws.http_get", side_effect=[pts, stations, obs]):
            result = fetch_current_weather(34.0, -77.0)
        assert result is not None
        assert "feels_like_f" not in result

    def test_includes_wind_chill(self):
        pts = self._make_pts_resp()
        stations = self._make_stations_resp(["https://api.weather.gov/stations/KORF"])
        obs = self._make_obs_resp(
            {
                "temperature": {"value": -5.0},  # 23°F
                "relativeHumidity": {"value": 50.0},
                "textDescription": "Windy",
                "windChill": {"value": -10.0},
            }
        )
        with patch("services.nws.http_get", side_effect=[pts, stations, obs]):
            result = fetch_current_weather(34.0, -77.0)
        assert result is not None
        assert "wind_chill_f" in result
        assert result["wind_chill_f"] == pytest.approx(14.0, abs=0.1)

    def test_returns_none_when_no_station_list(self):
        pts = self._make_pts_resp()
        stations = self._make_stations_resp([])
        with patch("services.nws.http_get", side_effect=[pts, stations]):
            result = fetch_current_weather(34.0, -77.0)
        assert result is None

    def test_returns_none_when_no_obs_url(self):
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"properties": {"observationStations": ""}}
        with patch("services.nws.http_get", return_value=m):
            result = fetch_current_weather(34.0, -77.0)
        assert result is None

    def test_returns_none_on_exception(self):
        with patch("services.nws.http_get", side_effect=Exception("timeout")):
            result = fetch_current_weather(34.0, -77.0)
        assert result is None

    def test_returns_none_when_temp_missing(self):
        pts = self._make_pts_resp()
        stations = self._make_stations_resp(["https://api.weather.gov/stations/KORF"])
        obs = self._make_obs_resp(
            {
                "temperature": {"value": None},
                "relativeHumidity": {"value": 50.0},
                "textDescription": "Foggy",
                "windChill": {"value": None},
            }
        )
        with patch("services.nws.http_get", side_effect=[pts, stations, obs]):
            result = fetch_current_weather(34.0, -77.0)
        # No air_temp_f → returns None
        assert result is None

    def test_includes_precipitation_data(self):
        pts = self._make_pts_resp()
        stations = self._make_stations_resp(["https://api.weather.gov/stations/KORF"])
        obs = self._make_obs_resp(
            {
                "temperature": {"value": 20.0},
                "relativeHumidity": {"value": 55.0},
                "textDescription": "Rain",
                "windChill": {"value": None},
                "precipitationLast6Hours": {"value": 12.5},
            }
        )
        with patch("services.nws.http_get", side_effect=[pts, stations, obs]):
            result = fetch_current_weather(34.0, -77.0)
        assert result is not None
        assert result["precip_recent_mm"] == 12.5
        assert result["precip_recent_hours"] == 6

    def test_precip_falls_back_to_3h_then_1h(self):
        pts = self._make_pts_resp()
        stations = self._make_stations_resp(["https://api.weather.gov/stations/KORF"])
        obs = self._make_obs_resp(
            {
                "temperature": {"value": 18.0},
                "relativeHumidity": {"value": 60.0},
                "textDescription": "Drizzle",
                "windChill": {"value": None},
                "precipitationLast3Hours": {"value": 4.0},
            }
        )
        with patch("services.nws.http_get", side_effect=[pts, stations, obs]):
            result = fetch_current_weather(34.0, -77.0)
        assert result is not None
        assert result["precip_recent_hours"] == 3


# ---------------------------------------------------------------------------
# fetch_state_alerts — additional branch coverage
# ---------------------------------------------------------------------------


class TestFetchStateAlertsAdditional:
    def test_skips_feature_without_event(self):
        body = {
            "features": [
                {"properties": {"event": "", "severity": "", "headline": "", "description": ""}},
                {"properties": {"event": "Rip Current Statement", "severity": "Minor",
                                "headline": "Rip currents", "description": ""}},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = body
        with patch("services.nws.http_get", return_value=mock_resp):
            alerts = fetch_state_alerts("NC")
        assert len(alerts) == 1
        assert alerts[0]["event"] == "Rip Current Statement"


# ---------------------------------------------------------------------------
# _try_nws_forecast — private, called via domain.forecast pipeline
# ---------------------------------------------------------------------------


class TestTryNwsForecast:
    def _mock(self, body: dict) -> MagicMock:
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = body
        return m

    def test_returns_parsed_conditions(self):
        body = {
            "properties": {
                "periods": [{"detailedForecast": "SW wind 10 to 15 kt. Seas 3 to 5 ft."}]
            }
        }
        with patch("services.nws.http_get", return_value=self._mock(body)):
            wind, wave, direction = _try_nws_forecast("AMZ158")
        assert wind == (10.0, 15.0)
        assert wave == (3.0, 5.0)
        assert direction == "SW"

    def test_uses_default_zone_when_none_given(self):
        body = {"properties": {"periods": []}}
        with patch("services.nws.http_get", return_value=self._mock(body)) as mock_get:
            _try_nws_forecast()
        url = mock_get.call_args[0][0]
        assert "AMZ158" in url

    def test_propagates_http_error(self):
        import requests
        m = MagicMock()
        m.raise_for_status.side_effect = requests.HTTPError("403")
        with patch("services.nws.http_get", return_value=m):
            with pytest.raises(requests.HTTPError):
                _try_nws_forecast("AMZ158")


# ---------------------------------------------------------------------------
# _try_nws_gridpoint — private, wind-from-land-gridpoint fallback
# ---------------------------------------------------------------------------


class TestTryNwsGridpoint:
    def _pts_resp(self, forecast_url: str) -> MagicMock:
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"properties": {"forecast": forecast_url}}
        return m

    def _fc_resp(self, periods: list) -> MagicMock:
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"properties": {"periods": periods}}
        return m

    def test_returns_wind_from_gridpoint(self):
        pts = self._pts_resp("https://api.weather.gov/gridpoints/MHX/59,36/forecast")
        periods = [
            {"windSpeed": "10 to 15 mph", "windDirection": "SW"},
            {"windSpeed": "15 to 20 mph", "windDirection": "W"},
        ]
        fc = self._fc_resp(periods)
        with patch("services.nws.http_get", side_effect=[pts, fc]):
            wind, wave, direction = _try_nws_gridpoint(34.0, -77.0)
        assert wind is not None
        assert wave is None
        assert direction == "SW"

    def test_uses_defaults_when_lat_lng_zero(self):
        pts = self._pts_resp("https://api.weather.gov/gridpoints/MHX/59,36/forecast")
        fc = self._fc_resp([])
        with patch("services.nws.http_get", side_effect=[pts, fc]) as mock_get:
            _try_nws_gridpoint(0, 0)
        pts_url = mock_get.call_args_list[0][0][0]
        # Default coords should be embedded in the URL
        assert "34.2104" in pts_url or "34" in pts_url

    def test_single_speed_wind(self):
        pts = self._pts_resp("https://api.weather.gov/gridpoints/MHX/59,36/forecast")
        periods = [{"windSpeed": "10 mph", "windDirection": "N"}]
        fc = self._fc_resp(periods)
        with patch("services.nws.http_get", side_effect=[pts, fc]):
            wind, _wave, direction = _try_nws_gridpoint(34.0, -77.0)
        import pytest as _pytest
        assert wind is not None
        # 10 mph × 0.868976 ≈ 8.7 kt
        assert wind[0] == _pytest.approx(8.7, abs=0.1)
        assert direction == "N"

    def test_no_wind_data_returns_none(self):
        pts = self._pts_resp("https://api.weather.gov/gridpoints/MHX/59,36/forecast")
        fc = self._fc_resp([{"windSpeed": "", "windDirection": ""}])
        with patch("services.nws.http_get", side_effect=[pts, fc]):
            wind, _wave, direction = _try_nws_gridpoint(34.0, -77.0)
        assert wind is None
        assert direction is None


# ---------------------------------------------------------------------------
# _fetch_nws_extended — 7-day forecast with gridpoint → marine zone fallback
# ---------------------------------------------------------------------------


class TestFetchNwsExtended:
    def _pts_resp(self, forecast_url: str) -> MagicMock:
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"properties": {"forecast": forecast_url}}
        return m

    def _fc_resp(self, periods: list) -> MagicMock:
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"properties": {"periods": periods}}
        return m

    def test_success_returns_periods(self):
        periods = [{"name": "Today", "detailedForecast": "Sunny."}]
        pts = self._pts_resp("https://api.weather.gov/gridpoints/MHX/59,36/forecast")
        fc = self._fc_resp(periods)
        with patch("services.nws.http_get", side_effect=[pts, fc]):
            result = _fetch_nws_extended(34.0, -77.0)
        assert result == periods

    def test_gridpoint_failure_falls_back_to_zone(self):
        zone_periods = [{"name": "Tonight", "detailedForecast": "SE wind 15 kt."}]
        marine_fc = self._fc_resp(zone_periods)
        with patch("services.nws.http_get", side_effect=[Exception("network"), marine_fc]):
            result = _fetch_nws_extended(34.0, -77.0, zone="AMZ158")
        assert result == zone_periods

    def test_gridpoint_failure_no_zone_returns_empty(self):
        with patch("services.nws.http_get", side_effect=Exception("offline")):
            result = _fetch_nws_extended(34.0, -77.0, zone="")
        assert result == []

    def test_both_failures_returns_empty(self):
        with patch("services.nws.http_get", side_effect=[Exception("pts"), Exception("zone")]):
            result = _fetch_nws_extended(34.0, -77.0, zone="AMZ158")
        assert result == []
