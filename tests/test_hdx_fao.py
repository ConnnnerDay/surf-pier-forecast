"""Tests for services/hdx_fao.py — HDX CKAN search and FAO fishing zone lookup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

import services.hdx_fao as hdx_fao


@pytest.fixture(autouse=True)
def _clear_cache():
    hdx_fao._CACHE.clear()
    yield
    hdx_fao._CACHE.clear()


def _mock_resp(json_body, status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = json_body
    m.status_code = status_code
    return m


class TestSearchHdxDatasets:
    def test_parses_package_results(self):
        body = {
            "result": {
                "results": [
                    {
                        "id": "pkg1",
                        "name": "marine-fisheries-data",
                        "title": "Marine Fisheries Data",
                        "notes": "x" * 400,
                        "organization": {"title": "FAO"},
                        "license_title": "CC-BY",
                        "num_resources": 2,
                        "resources": [
                            {"name": "data.csv", "format": "CSV", "url": "http://x/d.csv", "size": 100}
                        ],
                        "tags": [{"name": "fisheries"}, {"name": "marine"}],
                        "last_modified": "2024-01-01",
                    }
                ]
            }
        }
        with patch.object(hdx_fao._HTTP, "get", return_value=_mock_resp(body)):
            results = hdx_fao.search_hdx_datasets("fisheries")
        assert len(results) == 1
        pkg = results[0]
        assert pkg["id"] == "pkg1"
        assert pkg["title"] == "Marine Fisheries Data"
        assert len(pkg["notes"]) == 300
        assert pkg["organization"] == "FAO"
        assert pkg["tags"] == ["fisheries", "marine"]
        assert pkg["hdx_url"] == "https://data.humdata.org/dataset/marine-fisheries-data"

    def test_caps_resources_and_tags(self):
        body = {
            "result": {
                "results": [
                    {
                        "id": "pkg2",
                        "resources": [{"name": f"r{i}"} for i in range(5)],
                        "tags": [{"name": f"t{i}"} for i in range(12)],
                    }
                ]
            }
        }
        with patch.object(hdx_fao._HTTP, "get", return_value=_mock_resp(body)):
            results = hdx_fao.search_hdx_datasets("fisheries")
        assert len(results[0]["resources"]) == 3
        assert len(results[0]["tags"]) == 8

    def test_network_failure_returns_empty_list(self):
        with patch.object(
            hdx_fao._HTTP, "get", side_effect=requests.ConnectionError("down")
        ):
            results = hdx_fao.search_hdx_datasets("fisheries")
        assert results == []

    def test_failed_search_is_cached_with_short_ttl(self):
        with patch.object(
            hdx_fao._HTTP, "get", side_effect=requests.ConnectionError("down")
        ):
            hdx_fao.search_hdx_datasets("fisheries", rows=5)
        cache_key = "hdx_search:fisheries:5"
        assert hdx_fao._CACHE[cache_key]["ttl"] == 300

    def test_successful_search_is_cached_with_long_ttl(self):
        with patch.object(hdx_fao._HTTP, "get", return_value=_mock_resp({"result": {"results": []}})):
            hdx_fao.search_hdx_datasets("fisheries", rows=5)
        cache_key = "hdx_search:fisheries:5"
        assert hdx_fao._CACHE[cache_key]["ttl"] == hdx_fao._CACHE_HDX_TTL

    def test_second_call_uses_cache(self):
        with patch.object(
            hdx_fao._HTTP, "get", return_value=_mock_resp({"result": {"results": []}})
        ) as mock_get:
            hdx_fao.search_hdx_datasets("fisheries")
            hdx_fao.search_hdx_datasets("fisheries")
        assert mock_get.call_count == 1


class TestFetchFaoFisheriesZones:
    def test_uses_live_wfs_result_when_available(self):
        body = {
            "features": [
                {
                    "properties": {
                        "F_AREA": "21",
                        "NAME_EN": "Northwest Atlantic",
                        "F_SUBAREA": "21.1",
                        "OCEAN": "Atlantic",
                    }
                }
            ]
        }
        with patch.object(hdx_fao._HTTP, "get", return_value=_mock_resp(body)):
            result = hdx_fao.fetch_fao_fisheries_zones(40.0, -70.0)
        assert result["area_code"] == "21"
        assert result["area_name"] == "Northwest Atlantic"
        assert result["sub_area"] == "21.1"

    def test_falls_back_to_lookup_table_on_wfs_failure(self):
        with patch.object(
            hdx_fao._HTTP, "get", side_effect=requests.ConnectionError("down")
        ):
            # lat=20, lng=-70 falls only within Western Central Atlantic (31).
            result = hdx_fao.fetch_fao_fisheries_zones(20.0, -70.0)
        assert result["area_code"] == "31"
        assert result["method"] == "lookup_table"

    def test_falls_back_when_wfs_has_no_features(self):
        with patch.object(hdx_fao._HTTP, "get", return_value=_mock_resp({"features": []})):
            result = hdx_fao.fetch_fao_fisheries_zones(34.2, -77.8)
        assert result["method"] == "lookup_table"

    def test_unknown_coordinates_return_unknown_area(self):
        # lat=85 falls outside every defined FAO major area's lat_range.
        result = hdx_fao._lookup_fao_area(85.0, 0.0)
        assert result["area_code"] == ""
        assert result["area_name"] == "Unknown"

    def test_longitude_normalized_past_180(self):
        # 250 degrees normalizes to -110, inside Eastern Central Pacific (-180,-75).
        result = hdx_fao._lookup_fao_area(20.0, 250.0)
        assert result["area_code"] == "77"

    def test_second_call_uses_cache(self):
        with patch.object(hdx_fao._HTTP, "get", return_value=_mock_resp({"features": []})) as mock_get:
            hdx_fao.fetch_fao_fisheries_zones(34.2, -77.8)
            hdx_fao.fetch_fao_fisheries_zones(34.2, -77.8)
        assert mock_get.call_count == 1


class TestFetchFaoSpeciesInfo:
    def test_returns_parsed_species_on_success(self):
        body = [
            {
                "nameScientific": "Pomatomus saltatrix",
                "family": "Pomatomidae",
                "order": "Perciformes",
                "alpha3Code": "BLU",
                "isscaapGroup": "Miscellaneous coastal fishes",
                "asfisCode": "BLU",
            }
        ]
        with patch.object(hdx_fao._HTTP, "get", return_value=_mock_resp(body)):
            result = hdx_fao.fetch_fao_species_info("Bluefish")
        assert result["scientific_name"] == "Pomatomus saltatrix"
        assert result["asfis_code"] == "BLU"

    def test_returns_none_for_empty_results(self):
        with patch.object(hdx_fao._HTTP, "get", return_value=_mock_resp([])):
            result = hdx_fao.fetch_fao_species_info("Nonexistent Fish")
        assert result is None

    def test_returns_none_on_non_200_status(self):
        with patch.object(hdx_fao._HTTP, "get", return_value=_mock_resp([], status_code=500)):
            result = hdx_fao.fetch_fao_species_info("Bluefish")
        assert result is None

    def test_returns_none_on_network_error(self):
        with patch.object(
            hdx_fao._HTTP, "get", side_effect=requests.ConnectionError("down")
        ):
            result = hdx_fao.fetch_fao_species_info("Bluefish")
        assert result is None

    def test_lookup_is_case_insensitive_for_caching(self):
        body = [{"nameScientific": "Pomatomus saltatrix", "asfisCode": "BLU"}]
        with patch.object(hdx_fao._HTTP, "get", return_value=_mock_resp(body)) as mock_get:
            hdx_fao.fetch_fao_species_info("Bluefish")
            hdx_fao.fetch_fao_species_info("BLUEFISH")
        assert mock_get.call_count == 1


class TestGetHdxFaoEnrichment:
    def test_combines_zone_datasets_and_species(self):
        zone = {"area_code": "31", "area_name": "Western Central Atlantic"}
        datasets = [{"id": "pkg1", "title": "Fish data"}]
        species_info = {"scientific_name": "Pomatomus saltatrix", "asfis_code": "BLU"}

        with patch.object(hdx_fao, "fetch_fao_fisheries_zones", return_value=zone), \
                patch.object(hdx_fao, "search_hdx_datasets", return_value=datasets), \
                patch.object(hdx_fao, "fetch_fao_species_info", return_value=species_info):
            result = hdx_fao.get_hdx_fao_enrichment(34.2, -77.8, ["Bluefish"])

        assert result["available"] is True
        assert result["fao_zone"] == zone
        assert result["hdx_datasets"] == datasets
        assert len(result["species_enrichment"]) == 1
        assert result["species_enrichment"][0]["common_name"] == "Bluefish"

    def test_unavailable_when_zone_lookup_fails(self):
        with patch.object(hdx_fao, "fetch_fao_fisheries_zones", side_effect=Exception("boom")), \
                patch.object(hdx_fao, "search_hdx_datasets", return_value=[]), \
                patch.object(hdx_fao, "fetch_fao_species_info", return_value=None):
            result = hdx_fao.get_hdx_fao_enrichment(34.2, -77.8, [])
        assert result["available"] is False
        assert result["fao_zone"] == {}

    def test_species_lookup_failure_is_skipped_not_fatal(self):
        zone = {"area_code": "31", "area_name": "Western Central Atlantic"}
        with patch.object(hdx_fao, "fetch_fao_fisheries_zones", return_value=zone), \
                patch.object(hdx_fao, "search_hdx_datasets", return_value=[]), \
                patch.object(hdx_fao, "fetch_fao_species_info", side_effect=Exception("boom")):
            result = hdx_fao.get_hdx_fao_enrichment(34.2, -77.8, ["Bluefish"])
        assert result["species_enrichment"] == []

    def test_hdx_search_failure_results_in_empty_datasets(self):
        zone = {"area_code": "31", "area_name": "Western Central Atlantic"}
        with patch.object(hdx_fao, "fetch_fao_fisheries_zones", return_value=zone), \
                patch.object(hdx_fao, "search_hdx_datasets", side_effect=Exception("boom")), \
                patch.object(hdx_fao, "fetch_fao_species_info", return_value=None):
            result = hdx_fao.get_hdx_fao_enrichment(34.2, -77.8, [])
        assert result["hdx_datasets"] == []

    def test_limits_species_lookups_to_three(self):
        zone = {"area_code": "31", "area_name": "Western Central Atlantic"}
        with patch.object(hdx_fao, "fetch_fao_fisheries_zones", return_value=zone), \
                patch.object(hdx_fao, "search_hdx_datasets", return_value=[]), \
                patch.object(hdx_fao, "fetch_fao_species_info", return_value=None) as mock_species:
            hdx_fao.get_hdx_fao_enrichment(
                34.2, -77.8, ["A", "B", "C", "D", "E"]
            )
        assert mock_species.call_count == 3

    def test_second_call_uses_cache(self):
        zone = {"area_code": "31", "area_name": "Western Central Atlantic"}
        with patch.object(hdx_fao, "fetch_fao_fisheries_zones", return_value=zone) as mock_zone, \
                patch.object(hdx_fao, "search_hdx_datasets", return_value=[]), \
                patch.object(hdx_fao, "fetch_fao_species_info", return_value=None):
            hdx_fao.get_hdx_fao_enrichment(34.2, -77.8, [])
            hdx_fao.get_hdx_fao_enrichment(34.2, -77.8, [])
        assert mock_zone.call_count == 1


class TestCacheHelpers:
    def test_cache_get_returns_none_when_expired(self):
        hdx_fao._cache_set("k", "v", ttl=10)
        hdx_fao._CACHE["k"]["ts"] -= 20
        assert hdx_fao._cache_get("k") is None

    def test_cache_evicts_oldest_when_full(self):
        hdx_fao._cache_set("k0", "first")
        hdx_fao._CACHE["k0"]["ts"] -= 1000
        for i in range(1, hdx_fao._CACHE_MAX):
            hdx_fao._cache_set(f"k{i}", f"v{i}")
        assert len(hdx_fao._CACHE) == hdx_fao._CACHE_MAX
        hdx_fao._cache_set("overflow", "new")
        assert "k0" not in hdx_fao._CACHE
