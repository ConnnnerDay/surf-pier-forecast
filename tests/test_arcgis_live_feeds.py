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


class TestGetNearestRiverDischarge:
    def _gauge_body(self, site_id, lat, lng, flow=None, stage=None):
        series = []
        if flow is not None:
            series.append(
                {
                    "sourceInfo": {
                        "siteCode": [{"value": site_id}],
                        "siteName": f"Gauge {site_id}",
                        "geoLocation": {"geogLocation": {"latitude": lat, "longitude": lng}},
                    },
                    "variable": {"variableCode": [{"value": "00060"}]},
                    "values": [{"value": [{"value": str(flow), "dateTime": "2024-01-01T00:00:00Z"}]}],
                }
            )
        if stage is not None:
            series.append(
                {
                    "sourceInfo": {
                        "siteCode": [{"value": site_id}],
                        "siteName": f"Gauge {site_id}",
                        "geoLocation": {"geogLocation": {"latitude": lat, "longitude": lng}},
                    },
                    "variable": {"variableCode": [{"value": "00065"}]},
                    "values": [{"value": [{"value": str(stage), "dateTime": "2024-01-01T00:00:00Z"}]}],
                }
            )
        return {"value": {"timeSeries": series}}

    def test_returns_nearest_gauge_sorted_by_distance(self, monkeypatch):
        body = self._gauge_body("A", 34.25, -77.85, flow=50.0)
        monkeypatch.setattr(feeds._HTTP, "get", Mock(return_value=_resp(body)))
        out = feeds.get_nearest_river_discharge(34.2104, -77.7964)
        assert out["available"] is True
        assert out["nearest"]["id"] == "A"
        assert out["nearest"]["flow_cfs"] == 50.0
        assert out["nearest"]["distance_mi"] > 0

    def test_no_gauges_widens_search_then_reports_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            feeds._HTTP, "get", Mock(return_value=_resp({"value": {"timeSeries": []}}))
        )
        out = feeds.get_nearest_river_discharge(34.2104, -77.7964)
        assert out["available"] is False
        assert out["gauges"] == []
        assert out["nearest"] is None

    def test_does_not_mutate_cached_gauge_dicts_across_points(self, monkeypatch):
        """Distance is computed per-call; it must not leak into the shared cache."""
        body = self._gauge_body("A", 34.25, -77.85, flow=50.0)
        monkeypatch.setattr(feeds._HTTP, "get", Mock(return_value=_resp(body)))
        feeds.get_nearest_river_discharge(34.2104, -77.7964)
        cached = feeds.fetch_stream_gauges(
            34.2104 - 0.35, -77.7964 - 0.35, 34.2104 + 0.35, -77.7964 + 0.35
        )
        assert "distance_mi" not in cached[0]


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
