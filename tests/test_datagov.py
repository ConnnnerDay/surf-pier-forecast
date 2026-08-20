"""Tests for services/datagov.py — EPA Water Quality Portal integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

import services.datagov as datagov


@pytest.fixture(autouse=True)
def _clear_cache():
    datagov._CACHE.clear()
    yield
    datagov._CACHE.clear()


def _mock_resp(json_body, status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = json_body
    m.status_code = status_code
    return m


class TestFetchWaterQuality:
    def test_aggregates_measurements_across_rows(self):
        rows = [
            {
                "properties": {
                    "MonitoringLocationIdentifier": "ST1",
                    "MonitoringLocationName": "Station One",
                    "CharacteristicName": "Temperature, water",
                    "ResultMeasureValue": "22.5",
                    "ResultMeasure/MeasureUnitCode": "deg C",
                    "ActivityLocation/LatitudeMeasure": "34.2",
                    "ActivityLocation/LongitudeMeasure": "-77.8",
                    "ActivityStartDate": "2024-06-01",
                }
            },
            {
                "properties": {
                    "MonitoringLocationIdentifier": "ST1",
                    "CharacteristicName": "pH",
                    "ResultMeasureValue": "7.8",
                    "ResultMeasure/MeasureUnitCode": "",
                }
            },
        ]
        with patch.object(datagov._HTTP, "get", return_value=_mock_resp(rows)):
            result = datagov.fetch_water_quality(34.2, -77.8)

        assert len(result["stations"]) == 1
        station = result["stations"][0]
        assert station["id"] == "ST1"
        assert station["measurements"]["Temperature, water"]["value"] == 22.5
        assert station["measurements"]["pH"]["value"] == 7.8
        assert result["summary"]["water_temp_c"] == 22.5
        assert result["summary"]["ph"] == 7.8
        assert result["fetched_at"] is not None

    def test_handles_feature_collection_shape(self):
        body = {
            "features": [
                {
                    "properties": {
                        "MonitoringLocationIdentifier": "ST2",
                        "CharacteristicName": "Salinity",
                        "ResultMeasureValue": "30.1",
                    }
                }
            ]
        }
        with patch.object(datagov._HTTP, "get", return_value=_mock_resp(body)):
            result = datagov.fetch_water_quality(34.2, -77.8)
        assert result["summary"]["salinity_ppt"] == 30.1

    def test_caps_stations_at_20(self):
        rows = [
            {
                "properties": {
                    "MonitoringLocationIdentifier": f"ST{i}",
                    "CharacteristicName": "pH",
                    "ResultMeasureValue": "7.0",
                }
            }
            for i in range(30)
        ]
        with patch.object(datagov._HTTP, "get", return_value=_mock_resp(rows)):
            result = datagov.fetch_water_quality(34.2, -77.8)
        assert len(result["stations"]) == 20

    def test_skips_rows_with_no_numeric_value(self):
        rows = [
            {
                "properties": {
                    "MonitoringLocationIdentifier": "ST1",
                    "CharacteristicName": "pH",
                    "ResultMeasureValue": "not-a-number",
                }
            }
        ]
        with patch.object(datagov._HTTP, "get", return_value=_mock_resp(rows)):
            result = datagov.fetch_water_quality(34.2, -77.8)
        # Station is recorded but the measurement is dropped.
        assert result["stations"][0]["measurements"] == {}
        assert result["summary"] == {}

    def test_network_error_returns_empty_result(self):
        with patch.object(
            datagov._HTTP, "get", side_effect=requests.ConnectionError("down")
        ):
            result = datagov.fetch_water_quality(34.2, -77.8)
        assert result["stations"] == []
        assert result["summary"] == {}
        assert result["fetched_at"] is None

    def test_json_parse_error_returns_empty_result(self):
        bad_resp = MagicMock()
        bad_resp.raise_for_status.return_value = None
        bad_resp.json.side_effect = ValueError("bad json")
        with patch.object(datagov._HTTP, "get", return_value=bad_resp):
            result = datagov.fetch_water_quality(34.2, -77.8)
        assert result["stations"] == []

    def test_http_error_status_returns_empty_result(self):
        err_resp = MagicMock()
        err_resp.raise_for_status.side_effect = requests.HTTPError("500")
        with patch.object(datagov._HTTP, "get", return_value=err_resp):
            result = datagov.fetch_water_quality(34.2, -77.8)
        assert result["stations"] == []

    def test_second_call_uses_cache(self):
        rows = []
        with patch.object(
            datagov._HTTP, "get", return_value=_mock_resp(rows)
        ) as mock_get:
            datagov.fetch_water_quality(34.2, -77.8)
            datagov.fetch_water_quality(34.2, -77.8)
        assert mock_get.call_count == 1

    def test_failed_fetch_uses_shorter_retry_ttl(self):
        with patch.object(
            datagov._HTTP, "get", side_effect=requests.ConnectionError("down")
        ):
            datagov.fetch_water_quality(34.2, -77.8)
        cache_key = ("wq", round(34.2, 2), round(-77.8, 2), 50, 7)
        entry = datagov._CACHE[cache_key]
        assert entry["failed"] is True
        # Backdate past the normal TTL but within the failure TTL.
        entry["ts"] -= datagov._CACHE_TTL_FAIL + 1
        with patch.object(
            datagov._HTTP, "get", return_value=_mock_resp([])
        ) as mock_get2:
            datagov.fetch_water_quality(34.2, -77.8)
        assert mock_get2.call_count == 1


class TestFetchBeachClosures:
    def test_returns_closures_for_state(self):
        rows = [
            {
                "properties": {
                    "MonitoringLocationName": "Sunny Beach",
                    "MonitoringLocationIdentifier": "BC1",
                    "CountyCode": "129",
                    "LatitudeMeasure": "34.1",
                    "LongitudeMeasure": "-77.9",
                }
            }
        ]
        with patch.object(datagov._HTTP, "get", return_value=_mock_resp(rows)):
            result = datagov.fetch_beach_closures("nc")
        assert len(result) == 1
        assert result[0]["beach_name"] == "Sunny Beach"
        assert result[0]["lat"] == 34.1

    def test_skips_rows_without_name(self):
        rows = [{"properties": {"MonitoringLocationIdentifier": "BC2"}}]
        with patch.object(datagov._HTTP, "get", return_value=_mock_resp(rows)):
            result = datagov.fetch_beach_closures("NC")
        assert result == []

    def test_non_list_response_returns_empty(self):
        with patch.object(
            datagov._HTTP, "get", return_value=_mock_resp({"not": "a list"})
        ):
            result = datagov.fetch_beach_closures("NC")
        assert result == []

    def test_network_error_returns_empty_list(self):
        with patch.object(
            datagov._HTTP, "get", side_effect=requests.ConnectionError("down")
        ):
            result = datagov.fetch_beach_closures("NC")
        assert result == []

    def test_caps_results_at_50(self):
        rows = [
            {
                "properties": {
                    "MonitoringLocationName": f"Beach {i}",
                    "MonitoringLocationIdentifier": f"BC{i}",
                }
            }
            for i in range(60)
        ]
        with patch.object(datagov._HTTP, "get", return_value=_mock_resp(rows)):
            result = datagov.fetch_beach_closures("NC")
        assert len(result) == 50

    def test_unknown_state_code_uses_fallback_fips(self):
        with patch.object(
            datagov._HTTP, "get", return_value=_mock_resp([])
        ) as mock_get:
            datagov.fetch_beach_closures("ZZ")
        params = mock_get.call_args.kwargs["params"]
        assert params["statecode"] == "US:00"

    def test_second_call_uses_cache(self):
        with patch.object(
            datagov._HTTP, "get", return_value=_mock_resp([])
        ) as mock_get:
            datagov.fetch_beach_closures("NC")
            datagov.fetch_beach_closures("NC")
        assert mock_get.call_count == 1


class TestCacheEviction:
    def test_oldest_entry_evicted_when_cache_full(self):
        datagov._cache_set(("k0",), "first")
        original_ts = datagov._CACHE[("k0",)]["ts"]
        datagov._CACHE[("k0",)]["ts"] = original_ts - 1000
        for i in range(1, datagov._CACHE_MAX):
            datagov._cache_set((f"k{i}",), f"v{i}")
        assert len(datagov._CACHE) == datagov._CACHE_MAX
        datagov._cache_set(("overflow",), "new")
        assert ("k0",) not in datagov._CACHE
        assert len(datagov._CACHE) == datagov._CACHE_MAX


class TestGetWaterQualitySummary:
    def test_builds_display_ready_summary(self):
        raw = {
            "summary": {
                "dissolved_oxygen": 7.234,
                "water_temp_c": 20.0,
                "ph": 7.123,
                "salinity_ppt": 32.456,
                "turbidity_ntu": 3.1,
                "enterococcus_cfu_100ml": 50,
            },
            "stations": [{"id": "1"}],
            "source": "EPA Water Quality Portal",
            "source_url": "https://www.waterqualitydata.us/",
            "fetched_at": "2024-06-01T00:00:00+00:00",
        }
        with patch.object(datagov, "fetch_water_quality", return_value=raw):
            result = datagov.get_water_quality_summary(34.2, -77.8)

        assert result["available"] is True
        assert result["do_mg_l"] == "7.2"
        assert result["temp_c"] == "20.0"
        assert result["temp_f"] == "68.0"
        assert result["ph"] == "7.12"
        assert result["enterococcus_flag"] == "ok"
        assert result["station_count"] == 1

    def test_enterococcus_above_threshold_flags_advisory(self):
        raw = {"summary": {"enterococcus_cfu_100ml": 200}, "stations": []}
        with patch.object(datagov, "fetch_water_quality", return_value=raw):
            result = datagov.get_water_quality_summary(34.2, -77.8)
        assert result["enterococcus_flag"] == "advisory"

    def test_missing_enterococcus_is_unknown(self):
        raw = {"summary": {}, "stations": []}
        with patch.object(datagov, "fetch_water_quality", return_value=raw):
            result = datagov.get_water_quality_summary(34.2, -77.8)
        assert result["enterococcus_flag"] == "unknown"
        assert result["available"] is False
        assert result["temp_f"] is None

    def test_microcystin_above_danger_threshold(self):
        raw = {"summary": {"microcystin_ug_l": 25.0}, "stations": [{"id": "1"}]}
        with patch.object(datagov, "fetch_water_quality", return_value=raw):
            result = datagov.get_water_quality_summary(34.2, -77.8)
        assert result["hab_risk"] == "danger"
        assert "20.0" in result["hab_message"] or "25.0" in result["hab_message"]
        assert result["microcystin_ug_l"] == "25.0"

    def test_microcystin_above_watch_threshold(self):
        raw = {"summary": {"microcystin_ug_l": 10.0}, "stations": []}
        with patch.object(datagov, "fetch_water_quality", return_value=raw):
            result = datagov.get_water_quality_summary(34.2, -77.8)
        assert result["hab_risk"] == "watch"

    def test_microcystin_below_thresholds_is_low_risk(self):
        raw = {"summary": {"microcystin_ug_l": 1.0}, "stations": []}
        with patch.object(datagov, "fetch_water_quality", return_value=raw):
            result = datagov.get_water_quality_summary(34.2, -77.8)
        assert result["hab_risk"] == "low"

    def test_elevated_chlorophyll_without_toxin_reading_is_watch(self):
        raw = {"summary": {"chlorophyll_a": 30.0}, "stations": []}
        with patch.object(datagov, "fetch_water_quality", return_value=raw):
            result = datagov.get_water_quality_summary(34.2, -77.8)
        assert result["hab_risk"] == "watch"

    def test_no_bloom_indicators_is_unknown_risk(self):
        raw = {"summary": {}, "stations": []}
        with patch.object(datagov, "fetch_water_quality", return_value=raw):
            result = datagov.get_water_quality_summary(34.2, -77.8)
        assert result["hab_risk"] == "unknown"
        assert result["hab_message"] == ""


class TestHelpers:
    def test_safe_float_handles_invalid_values(self):
        assert datagov._safe_float("12.5") == 12.5
        assert datagov._safe_float(None) is None
        assert datagov._safe_float("not-a-number") is None

    def test_c_to_f_conversion(self):
        assert datagov._c_to_f(0) == 32
        assert datagov._c_to_f(100) == 212
        assert datagov._c_to_f(None) is None

    def test_fmt_rounds_to_decimals(self):
        assert datagov._fmt(7.23456, 2) == "7.23"
        assert datagov._fmt(None, 2) is None
