"""Tests for services/arcgis_live_feeds.py — the ArcGIS Living Atlas /
external-API live-feed integration backing the map overlays and combined
weather endpoints. Previously almost entirely untested (16% coverage).

All HTTP calls go through the module's shared `_HTTP` session, so every
test monkeypatches `_HTTP.get` with a canned response (or an exception)
rather than hitting the network. `cache_clear()` is invoked before every
test (autouse fixture) so module-level in-process caches don't leak
results between tests.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

import services.arcgis_live_feeds as feeds


@pytest.fixture(autouse=True)
def _clear_feed_caches():
    feeds.cache_clear()
    yield
    feeds.cache_clear()


def _resp(json_data=None, status_code=200):
    m = Mock()
    m.status_code = status_code
    m.json.return_value = {} if json_data is None else json_data
    if status_code >= 400:
        m.raise_for_status.side_effect = requests.HTTPError(f"status {status_code}")
    else:
        m.raise_for_status.return_value = None
    return m


def _ring_feature(attrs, rings):
    return {"attributes": attrs, "geometry": {"rings": rings}}


def _point_feature(attrs, x, y):
    return {"attributes": attrs, "geometry": {"x": x, "y": y}}


_RING = [[[-77.8, 34.2], [-77.7, 34.2], [-77.7, 34.3], [-77.8, 34.2]]]


class TestPureHelpers:
    def test_is_marine_true_for_keyword(self):
        assert feeds._is_marine("Small Craft Advisory") is True

    def test_is_marine_false_otherwise(self):
        assert feeds._is_marine("Winter Weather Advisory") is False

    def test_warning_color_extreme(self):
        assert feeds._warning_color("Extreme", "Tornado Warning") == "#ef4444"

    def test_warning_color_severe_gale(self):
        assert feeds._warning_color("Severe", "Gale Warning") == "#f97316"

    def test_warning_color_moderate(self):
        assert feeds._warning_color("Moderate", "Small Craft Advisory") == "#eab308"

    def test_warning_color_minor_default(self):
        assert feeds._warning_color("Minor", "Frost Advisory") == "#60a5fa"

    def test_ms_to_iso_valid(self):
        out = feeds._ms_to_iso(1700000000000)
        assert out.startswith("2023-11-")

    def test_ms_to_iso_falsy_returns_empty(self):
        assert feeds._ms_to_iso(None) == ""
        assert feeds._ms_to_iso(0) == ""

    def test_ms_to_iso_invalid_returns_empty(self):
        assert feeds._ms_to_iso("not-a-number") == ""

    def test_ring_to_latlng_swaps_xy(self):
        ring = [[-77.8, 34.2], [-77.7, 34.3]]
        assert feeds._ring_to_latlng(ring) == [[34.2, -77.8], [34.3, -77.7]]

    def test_ring_to_latlng_skips_short_points(self):
        ring = [[-77.8, 34.2], [1.0]]
        assert feeds._ring_to_latlng(ring) == [[34.2, -77.8]]

    def test_evict_oldest_drops_when_at_capacity(self):
        cache = {"a": {"ts": 1.0}, "b": {"ts": 2.0}}
        feeds._evict_oldest(cache, 2)
        assert "a" not in cache
        assert "b" in cache

    def test_evict_oldest_noop_under_capacity(self):
        cache = {"a": {"ts": 1.0}}
        feeds._evict_oldest(cache, 5)
        assert "a" in cache

    def test_category_label_thresholds(self):
        assert feeds._category_label(140) == "Category 5 Hurricane"
        assert feeds._category_label(115) == "Category 4 Hurricane"
        assert feeds._category_label(100) == "Category 3 Hurricane"
        assert feeds._category_label(85) == "Category 2 Hurricane"
        assert feeds._category_label(70) == "Category 1 Hurricane"
        assert feeds._category_label(40) == "Tropical Storm"
        assert feeds._category_label(10) == "Tropical Depression"
        assert feeds._category_label(0) == "Unknown"

    def test_deg_to_compass_none_returns_empty(self):
        assert feeds._deg_to_compass(None) == ""

    def test_deg_to_compass_directions(self):
        assert feeds._deg_to_compass(0) == "N"
        assert feeds._deg_to_compass(90) == "E"
        assert feeds._deg_to_compass(180) == "S"
        assert feeds._deg_to_compass(270) == "W"

    def test_pm25_category_breakpoints(self):
        assert feeds._pm25_category(5.0) == ("Good", "#22c55e")
        assert feeds._pm25_category(300.0) == ("Hazardous", "#7c3aed")

    def test_pm25_category_unknown_when_negative(self):
        assert feeds._pm25_category(-1.0) == ("Unknown", "#94a3b8")

    def test_smoke_style_known_class(self):
        style = feeds._smoke_style("3-25")
        assert style["label"].startswith("Moderate")

    def test_smoke_style_unknown_class_falls_back(self):
        style = feeds._smoke_style("weird-class")
        assert style["label"] == "weird-class"

    def test_temp_color_min_layer(self):
        assert feeds._temp_color(-5, "min") == "#1e3a8a"
        assert feeds._temp_color(80, "min") == "#f97316"

    def test_temp_color_max_layer(self):
        assert feeds._temp_color(20, "max") == "#3b82f6"
        assert feeds._temp_color(100, "max") == "#ef4444"

    def test_current_color_thresholds(self):
        assert feeds._current_color(5) == "#60a5fa"
        assert feeds._current_color(20) == "#22c55e"
        assert feeds._current_color(40) == "#eab308"
        assert feeds._current_color(80) == "#f97316"
        assert feeds._current_color(150) == "#ef4444"

    def test_precip_color_zero_category(self):
        assert feeds._precip_color(0) == feeds._PRECIP_POLY_COLORS[0]

    def test_precip_color_high_category(self):
        assert feeds._precip_color(19) == feeds._PRECIP_POLY_COLORS[7]


class TestFetchMarineWarnings:
    def test_success_returns_parsed_warning(self, monkeypatch):
        feat = _ring_feature(
            {
                "Event": "Small Craft Advisory",
                "Severity": "Moderate",
                "Summary": "Winds 20-25 kt",
                "Description": "full text",
                "Instruction": "be careful",
                "Affected": "Coastal waters",
                "End_": 1700000000000,
            },
            _RING,
        )
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        out = feeds.fetch_marine_warnings(34.0, -78.0, 34.5, -77.5)
        assert len(out) == 1
        assert out[0]["event"] == "Small Craft Advisory"
        assert out[0]["marine"] is True
        assert out[0]["rings"]

    def test_skips_features_without_rings(self, monkeypatch):
        feat = _ring_feature({"Event": "X"}, [])
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        out = feeds.fetch_marine_warnings(34.0, -78.0, 34.5, -77.5)
        assert out == []

    def test_exception_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_marine_warnings(34.0, -78.0, 34.5, -77.5) == []

    def test_cache_hit_skips_second_http_call(self, monkeypatch):
        get_mock = Mock(return_value=_resp({"features": []}))
        monkeypatch.setattr(feeds._HTTP, "get", get_mock)
        feeds.fetch_marine_warnings(34.0, -78.0, 34.5, -77.5)
        feeds.fetch_marine_warnings(34.0, -78.0, 34.5, -77.5)
        assert get_mock.call_count == 1


class TestFetchActiveStorms:
    def test_success_merges_position_track_cone(self, monkeypatch):
        pos_feat = _point_feature(
            {"STORMNAME": "ida", "INTENSITY": 70, "MSLP": 960}, -80.0, 25.0
        )
        track_feat = {
            "attributes": {"STORMNAME": "ida"},
            "geometry": {"paths": [[[-80.0, 25.0], [-79.0, 26.0]]]},
        }
        cone_feat = _ring_feature({"STORMNAME": "ida"}, _RING)
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(
                side_effect=[
                    _resp({"features": [pos_feat]}),
                    _resp({"features": [track_feat]}),
                    _resp({"features": [cone_feat]}),
                ]
            ),
        )
        out = feeds.fetch_active_storms()
        assert len(out) == 1
        assert out[0]["name"] == "Ida"
        assert out[0]["category"] == "Category 1 Hurricane"
        assert out[0]["track"]
        assert out[0]["cone"]

    def test_positions_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_active_storms() == []

    def test_no_storms_returns_empty_without_further_calls(self, monkeypatch):
        get_mock = Mock(return_value=_resp({"features": []}))
        monkeypatch.setattr(feeds._HTTP, "get", get_mock)
        out = feeds.fetch_active_storms()
        assert out == []
        assert get_mock.call_count == 1

    def test_track_and_cone_exceptions_still_return_positions(self, monkeypatch):
        pos_feat = _point_feature(
            {"STORMNAME": "ida", "INTENSITY": 70, "MSLP": 960}, -80.0, 25.0
        )
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(
                side_effect=[
                    _resp({"features": [pos_feat]}),
                    requests.ConnectionError("track down"),
                    requests.ConnectionError("cone down"),
                ]
            ),
        )
        out = feeds.fetch_active_storms()
        assert len(out) == 1
        assert out[0]["track"] == []
        assert out[0]["cone"] == []

    def test_cache_hit_skips_second_call(self, monkeypatch):
        get_mock = Mock(return_value=_resp({"features": []}))
        monkeypatch.setattr(feeds._HTTP, "get", get_mock)
        feeds.fetch_active_storms()
        feeds.fetch_active_storms()
        assert get_mock.call_count == 1


class TestFetchRecentStormTracks:
    def _feat(self, name, basin, ss, start):
        return {
            "attributes": {
                "STORMID": "AL012023",
                "STORMNAME": name,
                "BASIN": basin,
                "SS": ss,
                "STARTDTG": start,
                "ENDDTG": start + 1000,
            },
            "geometry": {"paths": [[[-80.0, 25.0], [-79.0, 26.0]]]},
        }

    def test_success_sorts_by_start_date_desc(self, monkeypatch):
        feats = [
            self._feat("Alpha", "AL", 2, 1000),
            self._feat("Beta", "AL", 3, 2000),
        ]
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": feats}))
        )
        out = feeds.fetch_recent_storm_tracks()
        assert out[0]["name"] == "Beta"
        assert out[1]["name"] == "Alpha"
        assert out[0]["category"] == "Category 2 Hurricane"

    def test_filters_by_basin(self, monkeypatch):
        feats = [
            self._feat("Alpha", "AL", 2, 1000),
            self._feat("Beta", "EP", 2, 2000),
        ]
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": feats}))
        )
        out = feeds.fetch_recent_storm_tracks(basin="ep")
        assert len(out) == 1
        assert out[0]["name"] == "Beta"

    def test_skips_features_without_paths(self, monkeypatch):
        feat = self._feat("Alpha", "AL", 2, 1000)
        feat["geometry"] = {"paths": []}
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        assert feeds.fetch_recent_storm_tracks() == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_recent_storm_tracks() == []


class TestFetchAirQuality:
    def test_success_returns_nearest_station(self, monkeypatch):
        feat = _point_feature(
            {
                "location": "Station A",
                "city": "Wilmington",
                "value": 10.0,
                "unit": "µg/m³",
                "lastUpdated": "2024-01-01",
            },
            -77.8,
            34.2,
        )
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        out = feeds.fetch_air_quality(34.2, -77.8)
        assert out["location"] == "Station A"
        assert out["category"] == "Good"

    def test_no_stations_in_any_pad_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": []}))
        )
        assert feeds.fetch_air_quality(34.2, -77.8) is None

    def test_exception_breaks_loop_and_returns_none(self, monkeypatch):
        get_mock = Mock(side_effect=requests.ConnectionError("down"))
        monkeypatch.setattr(feeds._HTTP, "get", get_mock)
        assert feeds.fetch_air_quality(34.2, -77.8) is None
        assert get_mock.call_count == 1


class TestFetchWindForecast:
    def test_groups_and_averages_by_interval(self, monkeypatch):
        feats = [
            {
                "attributes": {
                    "IntervalStart": 1000,
                    "WindDir": 90,
                    "WindSpeed": 10,
                    "WindGust": 15,
                }
            },
            {
                "attributes": {
                    "IntervalStart": 1000,
                    "WindDir": 100,
                    "WindSpeed": 20,
                    "WindGust": 25,
                }
            },
        ]
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": feats}))
        )
        out = feeds.fetch_wind_forecast(34.2, -77.8)
        assert len(out) == 1
        assert out[0]["wind_speed"] == 15
        assert out[0]["wind_dir"] in ("E", "SE")

    def test_caps_at_eight_intervals(self, monkeypatch):
        feats = [
            {"attributes": {"IntervalStart": i * 1000, "WindDir": 0, "WindSpeed": 5, "WindGust": 0}}
            for i in range(12)
        ]
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": feats}))
        )
        out = feeds.fetch_wind_forecast(34.2, -77.8)
        assert len(out) == 8

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_wind_forecast(34.2, -77.8) == []


class TestFetchSstStations:
    def test_success_maps_alert_level(self, monkeypatch):
        feat = _point_feature(
            {"name": "Reef A", "sst": 28.0, "ssta": 1.0, "dhw": 2.0, "alert": 2, "date": 1700000000000},
            -77.8,
            34.2,
        )
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        out = feeds.fetch_sst_stations(34.0, -78.0, 34.5, -77.5)
        assert out[0]["alert_label"] == "Bleaching Warning"
        assert out[0]["sst_f"] is not None

    def test_skips_missing_coords(self, monkeypatch):
        feat = {"attributes": {"name": "X"}, "geometry": {}}
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        assert feeds.fetch_sst_stations(34.0, -78.0, 34.5, -77.5) == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_sst_stations(34.0, -78.0, 34.5, -77.5) == []


class TestFetchWildfireIncidents:
    def test_success_sorts_by_acres_desc(self, monkeypatch):
        small = _point_feature(
            {"IncidentName": "small fire", "DailyAcres": 10, "PercentContained": 50},
            -78.0,
            34.0,
        )
        big = _point_feature(
            {"IncidentName": "big fire", "DailyAcres": 9000, "PercentContained": 10},
            -78.0,
            34.0,
        )
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [small, big]}))
        )
        out = feeds.fetch_wildfire_incidents(33.0, -79.0, 35.0, -77.0)
        assert out[0]["name"] == "Big Fire"

    def test_skips_missing_coords(self, monkeypatch):
        feat = {"attributes": {"IncidentName": "X"}, "geometry": {}}
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        assert feeds.fetch_wildfire_incidents(33.0, -79.0, 35.0, -77.0) == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_wildfire_incidents(33.0, -79.0, 35.0, -77.0) == []


class TestFetchSmokeForecast:
    def test_keeps_only_latest_reference_date(self, monkeypatch):
        old = _ring_feature({"smoke_classdesc": "0-3", "referencedate": 1000}, _RING)
        new = _ring_feature({"smoke_classdesc": "3-25", "referencedate": 2000}, _RING)
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [old, new]}))
        )
        out = feeds.fetch_smoke_forecast(33.0, -79.0, 35.0, -77.0)
        assert len(out) == 1
        assert out[0]["class_desc"] == "3-25"

    def test_no_features_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": []}))
        )
        assert feeds.fetch_smoke_forecast(33.0, -79.0, 35.0, -77.0) == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_smoke_forecast(33.0, -79.0, 35.0, -77.0) == []


class TestFetchPrecipForecast:
    def test_dedups_by_fromdate_and_caps_at_four(self, monkeypatch):
        feats = [
            {"attributes": {"category": 2, "fromdate": i, "todate": i + 1, "label": ""}}
            for i in range(6)
        ] + [{"attributes": {"category": 2, "fromdate": 0, "todate": 1, "label": ""}}]
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": feats}))
        )
        out = feeds.fetch_precip_forecast(34.2, -77.8)
        assert len(out) == 4
        assert out[0]["rain"] is True

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_precip_forecast(34.2, -77.8) == []


class TestFetchSeaIceExtent:
    def test_success_returns_dict(self, monkeypatch):
        feat = _ring_feature(
            {"Rec_Year": 2024, "Rec_Month": 3, "Rec_Area": 10.0, "Rec_Extent": 12.0},
            _RING,
        )
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        out = feeds.fetch_sea_ice_extent()
        assert out["year"] == 2024
        assert out["extent_mkm2"] == 12.0

    def test_no_features_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": []}))
        )
        assert feeds.fetch_sea_ice_extent() is None

    def test_exception_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_sea_ice_extent() is None


class TestFetchTempForecast:
    def test_merges_min_max_keeping_extremes(self, monkeypatch):
        period_ms = 1700000000000
        min_feats = {
            "features": [
                {"attributes": {"Period": period_ms, "Temp": 40}},
                {"attributes": {"Period": period_ms, "Temp": 35}},
            ]
        }
        max_feats = {
            "features": [
                {"attributes": {"Period": period_ms, "Temp": 70}},
                {"attributes": {"Period": period_ms, "Temp": 80}},
            ]
        }
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(side_effect=[_resp(min_feats), _resp(max_feats)]),
        )
        out = feeds.fetch_temp_forecast(34.2, -77.8)
        assert len(out) == 1
        assert out[0]["min_f"] == 35
        assert out[0]["max_f"] == 80

    def test_one_layer_exception_keeps_other_layer_data(self, monkeypatch):
        period_ms = 1700000000000
        max_feats = {"features": [{"attributes": {"Period": period_ms, "Temp": 80}}]}
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(side_effect=[requests.ConnectionError("min down"), _resp(max_feats)]),
        )
        out = feeds.fetch_temp_forecast(34.2, -77.8)
        assert out[0]["min_f"] is None
        assert out[0]["max_f"] == 80

    def test_both_layers_fail_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_temp_forecast(34.2, -77.8) == []


class TestFetchSeismicEvents:
    def test_success_maps_alert_color(self, monkeypatch):
        feat = {
            "attributes": {
                "latitude": 34.2,
                "longitude": -77.8,
                "mag": 4.5,
                "depth": 10.0,
                "place": "offshore NC",
                "eventTime": 1700000000000,
                "hoursOld": 2,
                "tsunami": 1,
                "alert": "orange",
                "sig": 300,
                "eventType": "earthquake",
            }
        }
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        out = feeds.fetch_seismic_events(33.0, -79.0, 35.0, -77.0)
        assert out[0]["alert_color"] == "#FF9800"
        assert out[0]["tsunami"] is True

    def test_skips_missing_coords(self, monkeypatch):
        feat = {"attributes": {"mag": 3.0}}
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        assert feeds.fetch_seismic_events(33.0, -79.0, 35.0, -77.0) == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_seismic_events(33.0, -79.0, 35.0, -77.0) == []


class TestFetchDrought:
    def test_picks_highest_nonzero_category(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(
                return_value=_resp(
                    [{"D0": 100, "D1": 80, "D2": 40, "D3": 0, "D4": 0, "MapDate": "20240101"}]
                )
            ),
        )
        out = feeds.fetch_drought(34.2, -77.8)
        assert out["dm"] == 2
        assert out["code"] == "D2"
        assert out["date"] == "2024-01-01"

    def test_all_zero_returns_no_drought(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(return_value=_resp([{"D0": 0, "D1": 0, "D2": 0, "D3": 0, "D4": 0}])),
        )
        out = feeds.fetch_drought(34.2, -77.8)
        assert out["dm"] == -1
        assert out["label"] == "No Drought"

    def test_empty_response_returns_none(self, monkeypatch):
        monkeypatch.setattr(feeds._HTTP, "get", Mock(return_value=_resp([])))
        assert feeds.fetch_drought(34.2, -77.8) is None

    def test_exception_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_drought(34.2, -77.8) is None


class TestFetchMetarStations:
    def test_success_parses_station_list(self, monkeypatch):
        raw = [
            {
                "lat": 34.2,
                "lon": -77.8,
                "icaoId": "KILM",
                "name": "Wilmington",
                "temp": 20.0,
                "dewp": 10.0,
                "visib": "10",
                "wspd": 10,
                "wgst": 15,
                "wdir": 90,
                "slp": 1015.0,
                "fltcat": "vfr",
            }
        ]
        monkeypatch.setattr(feeds._HTTP, "get", Mock(return_value=_resp(raw)))
        out = feeds.fetch_metar_stations(33.0, -79.0, 35.0, -77.0)
        assert out[0]["icao"] == "KILM"
        assert out[0]["flight_cat"] == "VFR"
        assert out[0]["cat_color"] == "#22c55e"

    def test_dict_response_uses_data_key(self, monkeypatch):
        raw = {"data": []}
        monkeypatch.setattr(feeds._HTTP, "get", Mock(return_value=_resp(raw)))
        assert feeds.fetch_metar_stations(33.0, -79.0, 35.0, -77.0) == []

    def test_skips_missing_coords(self, monkeypatch):
        raw = [{"icaoId": "KILM"}]
        monkeypatch.setattr(feeds._HTTP, "get", Mock(return_value=_resp(raw)))
        assert feeds.fetch_metar_stations(33.0, -79.0, 35.0, -77.0) == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_metar_stations(33.0, -79.0, 35.0, -77.0) == []


class TestFetchTerminator:
    def test_success_returns_rings_and_timestamp(self, monkeypatch):
        feat = _ring_feature({"timestamp": 1700000000000}, _RING)
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        out = feeds.fetch_terminator()
        assert out["rings"]
        assert out["timestamp"]

    def test_no_features_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": []}))
        )
        assert feeds.fetch_terminator() is None

    def test_exception_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_terminator() is None


class TestFetchStreamGauges:
    def test_success_parses_stage_and_flow(self, monkeypatch):
        body = {
            "value": {
                "timeSeries": [
                    {
                        "sourceInfo": {
                            "siteCode": [{"value": "0205551460"}],
                            "siteName": "Test Creek",
                            "geoLocation": {
                                "geogLocation": {"latitude": 34.2, "longitude": -77.8}
                            },
                        },
                        "variable": {"variableCode": [{"value": "00065"}]},
                        "values": [{"value": [{"value": "5.2", "dateTime": "2024-01-01T00:00:00Z"}]}],
                    },
                    {
                        "sourceInfo": {
                            "siteCode": [{"value": "0205551460"}],
                            "siteName": "Test Creek",
                            "geoLocation": {
                                "geogLocation": {"latitude": 34.2, "longitude": -77.8}
                            },
                        },
                        "variable": {"variableCode": [{"value": "00060"}]},
                        "values": [{"value": [{"value": "120.5", "dateTime": "2024-01-01T00:00:00Z"}]}],
                    },
                ]
            }
        }
        monkeypatch.setattr(feeds._HTTP, "get", Mock(return_value=_resp(body)))
        out = feeds.fetch_stream_gauges(33.0, -79.0, 35.0, -77.0)
        assert len(out) == 1
        assert out[0]["stage_ft"] == 5.2
        assert out[0]["flow_cfs"] == 120.5

    def test_skips_missing_site_code(self, monkeypatch):
        body = {"value": {"timeSeries": [{"sourceInfo": {"siteCode": []}}]}}
        monkeypatch.setattr(feeds._HTTP, "get", Mock(return_value=_resp(body)))
        assert feeds.fetch_stream_gauges(33.0, -79.0, 35.0, -77.0) == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_stream_gauges(33.0, -79.0, 35.0, -77.0) == []


class TestFetchStormReports:
    def test_combines_all_three_layers(self, monkeypatch):
        hail = {
            "features": [
                {
                    "attributes": {
                        "LATITUDE": 34.0,
                        "LONGITUDE": -78.0,
                        "UTC_DATETIME": 1000,
                        "HAIL_SIZE": "1.5",
                    }
                }
            ]
        }
        tornado = {
            "features": [
                {
                    "attributes": {
                        "LATITUDE": 34.1,
                        "LONGITUDE": -78.1,
                        "UTC_DATETIME": 2000,
                        "F_SCALE": "2",
                    }
                }
            ]
        }
        wind = {
            "features": [
                {
                    "attributes": {
                        "LATITUDE": 34.2,
                        "LONGITUDE": -78.2,
                        "UTC_DATETIME": 3000,
                    }
                }
            ]
        }
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(side_effect=[_resp(hail), _resp(tornado), _resp(wind)]),
        )
        out = feeds.fetch_storm_reports(33.0, -79.0, 35.0, -77.0)
        assert len(out) == 3
        types = {r["type"] for r in out}
        assert types == {"hail", "tornado", "wind"}
        torn = next(r for r in out if r["type"] == "tornado")
        assert torn["magnitude"] == "EF2"

    def test_one_layer_failure_does_not_break_others(self, monkeypatch):
        wind = {
            "features": [
                {
                    "attributes": {
                        "LATITUDE": 34.2,
                        "LONGITUDE": -78.2,
                        "UTC_DATETIME": 3000,
                    }
                }
            ]
        }
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(
                side_effect=[
                    requests.ConnectionError("hail down"),
                    requests.ConnectionError("tornado down"),
                    _resp(wind),
                ]
            ),
        )
        out = feeds.fetch_storm_reports(33.0, -79.0, 35.0, -77.0)
        assert len(out) == 1
        assert out[0]["type"] == "wind"


class TestFetchAqiMap:
    def test_success_maps_category(self, monkeypatch):
        feat = _point_feature(
            {"location": "Station A", "value": 10.0, "lastUpdated": "now"}, -77.8, 34.2
        )
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        out = feeds.fetch_aqi_map(33.0, -79.0, 35.0, -77.0)
        assert out[0]["category"] == "Good"

    def test_skips_missing_coords(self, monkeypatch):
        feat = {"attributes": {"value": 10.0}, "geometry": {}}
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        assert feeds.fetch_aqi_map(33.0, -79.0, 35.0, -77.0) == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_aqi_map(33.0, -79.0, 35.0, -77.0) == []


class TestFetchDroughtMap:
    def test_success_skips_negative_dm(self, monkeypatch):
        valid = _ring_feature({"dm": 2}, _RING)
        invalid = _ring_feature({"dm": -1}, _RING)
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(return_value=_resp({"features": [valid, invalid]})),
        )
        out = feeds.fetch_drought_map(33.0, -79.0, 35.0, -77.0)
        assert len(out) == 1
        assert out[0]["code"] == "D2"

    def test_service_error_in_body_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(return_value=_resp({"error": {"message": "bad request"}})),
        )
        assert feeds.fetch_drought_map(33.0, -79.0, 35.0, -77.0) == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_drought_map(33.0, -79.0, 35.0, -77.0) == []


class TestFetchPrecipitationMap:
    def test_skips_zero_category(self, monkeypatch):
        zero = _ring_feature({"category": 0, "fromdate": 1, "todate": 2}, _RING)
        nonzero = _ring_feature({"category": 5, "fromdate": 1, "todate": 2}, _RING)
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(return_value=_resp({"features": [zero, nonzero]})),
        )
        out = feeds.fetch_precipitation_map(33.0, -79.0, 35.0, -77.0)
        assert len(out) == 1
        assert out[0]["category"] == 5

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_precipitation_map(33.0, -79.0, 35.0, -77.0) == []


class TestFetchNdbcBuoys:
    def test_success_converts_units(self, monkeypatch):
        table = {
            "columnNames": [
                "station",
                "latitude",
                "longitude",
                "time",
                "wd",
                "wspd",
                "gst",
                "wvht",
                "dpd",
                "wtmp",
                "atmp",
                "bar",
            ],
            "rows": [
                ["44025", 34.2, -77.8, "2024-01-01T00:00:00Z", 90, 5.0, 7.0, 1.0, 8.0, 15.0, 16.0, 1015.0]
            ],
        }
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"table": table}))
        )
        out = feeds.fetch_ndbc_buoys(33.0, -79.0, 35.0, -77.0)
        assert out[0]["id"] == "44025"
        assert out[0]["wind_kt"] == pytest.approx(9.7, abs=0.1)

    def test_404_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({}, status_code=404))
        )
        assert feeds.fetch_ndbc_buoys(33.0, -79.0, 35.0, -77.0) == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_ndbc_buoys(33.0, -79.0, 35.0, -77.0) == []


class TestFetchNdfdTemperatureMap:
    def test_success_buckets_min_and_max(self, monkeypatch):
        period_ms = 1700000000000
        min_feats = {
            "features": [
                {
                    "attributes": {"Temp": 40, "Period": period_ms},
                    "geometry": {"rings": _RING},
                }
            ]
        }
        max_feats = {
            "features": [
                {
                    "attributes": {"Temp": 75, "Period": period_ms},
                    "geometry": {"rings": _RING},
                }
            ]
        }
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(side_effect=[_resp(min_feats), _resp(max_feats)]),
        )
        out = feeds.fetch_ndfd_temperature_map(33.0, -79.0, 35.0, -77.0)
        assert len(out["min"]) == 1
        assert len(out["max"]) == 1
        assert out["min"][0]["temp_f"] == 40

    def test_one_layer_exception_keeps_other(self, monkeypatch):
        period_ms = 1700000000000
        max_feats = {
            "features": [
                {
                    "attributes": {"Temp": 75, "Period": period_ms},
                    "geometry": {"rings": _RING},
                }
            ]
        }
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(side_effect=[requests.ConnectionError("down"), _resp(max_feats)]),
        )
        out = feeds.fetch_ndfd_temperature_map(33.0, -79.0, 35.0, -77.0)
        assert out["min"] == []
        assert len(out["max"]) == 1


class TestFetchHfradarCurrents:
    def test_merges_three_regions(self, monkeypatch):
        feat = {
            "attributes": {
                "lat": 34.2,
                "lon": -77.8,
                "speed": 30.0,
                "direction": 180,
                "u": 1.0,
                "v": -1.0,
                "datetime": 1700000000000,
            }
        }
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(
                side_effect=[
                    _resp({"features": [feat]}),
                    _resp({"features": []}),
                    _resp({"features": []}),
                ]
            ),
        )
        out = feeds.fetch_hfradar_currents(33.0, -79.0, 35.0, -77.0)
        assert len(out) == 1
        assert out[0]["color"] == "#eab308"

    def test_region_with_error_body_skipped_silently(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(
                side_effect=[
                    _resp({"error": "not applicable"}),
                    _resp({"features": []}),
                    _resp({"features": []}),
                ]
            ),
        )
        assert feeds.fetch_hfradar_currents(33.0, -79.0, 35.0, -77.0) == []

    def test_region_exception_skipped(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_hfradar_currents(33.0, -79.0, 35.0, -77.0) == []


class TestFetchTropicalOutlook:
    def test_success_normalizes_probability(self, monkeypatch):
        feat = _ring_feature(
            {
                "probability": "High",
                "basin": "atl",
                "discussion": "x" * 400,
                "FormationChance7day": "70%",
            },
            _RING,
        )
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        out = feeds.fetch_tropical_outlook()
        assert out[0]["probability"] == "high"
        assert out[0]["basin"] == "ATL"
        assert len(out[0]["discussion"]) == 300

    def test_defaults_to_low_probability(self, monkeypatch):
        feat = _ring_feature({}, _RING)
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"features": [feat]}))
        )
        out = feeds.fetch_tropical_outlook()
        assert out[0]["probability"] == "low"

    def test_service_error_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP,
            "get",
            Mock(return_value=_resp({"error": {"message": "bad"}})),
        )
        assert feeds.fetch_tropical_outlook() == []

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(side_effect=requests.ConnectionError("down"))
        )
        assert feeds.fetch_tropical_outlook() == []

    def test_cache_hit_skips_second_call(self, monkeypatch):
        get_mock = Mock(return_value=_resp({"features": []}))
        monkeypatch.setattr(feeds._HTTP, "get", get_mock)
        feeds.fetch_tropical_outlook()
        feeds.fetch_tropical_outlook()
        assert get_mock.call_count == 1
