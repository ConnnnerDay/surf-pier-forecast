"""Tests for the new geospatial service modules.

Covers:
- services/osm_tiles.py
- services/natural_earth.py
- services/datagov.py
- services/esri_open_data.py
- services/nasa_worldview.py
- services/aerial_imagery.py
- services/hdx_fao.py
- web/geo_api.py (Flask blueprint endpoints)

All network calls are mocked so tests run offline without API keys.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# services/osm_tiles.py
# ─────────────────────────────────────────────────────────────────────────────

class TestOsmTiles:
    def test_get_tile_config_returns_layers(self):
        from services.osm_tiles import get_tile_config
        cfg = get_tile_config()
        assert "layers" in cfg
        assert len(cfg["layers"]) >= 2
        ids = {l["id"] for l in cfg["layers"]}
        assert "osm_standard" in ids
        assert "osm_humanitarian" in ids

    def test_tile_url_has_placeholders(self):
        from services.osm_tiles import get_tile_config
        cfg = get_tile_config()
        osm = next(l for l in cfg["layers"] if l["id"] == "osm_standard")
        assert "{z}" in osm["url"]
        assert "{x}" in osm["url"]
        assert "{y}" in osm["url"]

    def test_attribution_present(self):
        from services.osm_tiles import get_tile_config
        cfg = get_tile_config()
        for layer in cfg["layers"]:
            assert "attribution" in layer["options"]
            assert "openstreetmap" in layer["options"]["attribution"].lower()

    def test_fetch_osm_amenities_returns_list_on_success(self):
        from services.osm_tiles import fetch_osm_amenities
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "elements": [
                {"type": "node", "lat": 36.1, "lon": -75.5,
                 "tags": {"name": "Jennette's Pier", "man_made": "pier"}},
                {"type": "node", "lat": 36.2, "lon": -75.6,
                 "tags": {"name": "Nags Head Marina", "amenity": "marina"}},
            ]
        }
        with patch("services.osm_tiles._HTTP") as mock_http:
            mock_http.post.return_value = mock_resp
            results = fetch_osm_amenities(36.1, -75.5, radius_m=1000)
        assert isinstance(results, list)
        assert len(results) == 2
        assert results[0]["lat"] == 36.1
        assert results[0]["type"] == "pier"

    def test_fetch_osm_amenities_returns_empty_on_error(self):
        from services.osm_tiles import fetch_osm_amenities
        import requests as req
        with patch("services.osm_tiles._HTTP") as mock_http:
            mock_http.post.side_effect = req.RequestException("timeout")
            results = fetch_osm_amenities(36.1, -75.5)
        assert results == []

    def test_fetch_osm_amenities_caches_results(self):
        from services.osm_tiles import fetch_osm_amenities, _CACHE
        _CACHE.clear()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"elements": []}
        with patch("services.osm_tiles._HTTP") as mock_http:
            mock_http.post.return_value = mock_resp
            fetch_osm_amenities(10.0, 20.0)
            fetch_osm_amenities(10.0, 20.0)
            assert mock_http.post.call_count == 1  # second call hits cache


# ─────────────────────────────────────────────────────────────────────────────
# services/natural_earth.py
# ─────────────────────────────────────────────────────────────────────────────

class TestNaturalEarth:
    def test_get_coastlines_returns_feature_collection_on_download_failure(self, tmp_path, monkeypatch):
        """Returns empty FeatureCollection if download fails — never raises."""
        monkeypatch.setattr(
            "services.natural_earth._BASE_DIR", str(tmp_path)
        )
        import requests as req
        with patch("services.natural_earth._HTTP") as mock_http:
            mock_http.get.side_effect = req.RequestException("offline")
            result = __import__(
                "services.natural_earth", fromlist=["get_coastlines_geojson"]
            ).get_coastlines_geojson()
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []

    def test_get_coastlines_returns_cached_geojson(self, tmp_path, monkeypatch):
        """Returns cached data from disk without making a network request."""
        import os
        monkeypatch.setattr("services.natural_earth._BASE_DIR", str(tmp_path))

        fake_geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "LineString",
                 "coordinates": [[-75.0, 35.0], [-74.5, 35.5]]}, "properties": {}}
            ]
        }
        cache_path = tmp_path / "coastline_110m.geojson"
        cache_path.write_text(json.dumps(fake_geojson))

        from services.natural_earth import get_coastlines_geojson
        result = get_coastlines_geojson()
        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 1

    def test_clip_geojson_filters_features(self):
        from services.natural_earth import _clip_geojson
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "geometry": {"type": "LineString",
                              "coordinates": [[-75.0, 35.0], [-74.5, 35.5]]},
                 "properties": {}},
                {"type": "Feature",
                 "geometry": {"type": "LineString",
                              "coordinates": [[10.0, 55.0], [11.0, 55.5]]},
                 "properties": {}},
            ]
        }
        # bbox centred on US East Coast
        clipped = _clip_geojson(geojson, (30.0, -80.0, 40.0, -70.0))
        assert len(clipped["features"]) == 1
        assert clipped["features"][0]["geometry"]["coordinates"][0][0] == -75.0

    def test_flatten_coords_handles_nested(self):
        from services.natural_earth import _flatten_coords
        # LineString coordinates
        result = _flatten_coords([[-75.0, 35.0], [-74.5, 35.5]])
        assert (-75.0, 35.0) in result
        # Polygon coordinates (nested one level deeper)
        result2 = _flatten_coords([[[-75.0, 35.0], [-74.5, 35.5], [-75.0, 35.0]]])
        assert (-75.0, 35.0) in result2


# ─────────────────────────────────────────────────────────────────────────────
# services/datagov.py
# ─────────────────────────────────────────────────────────────────────────────

class TestDataGov:
    def test_get_water_quality_summary_returns_structure(self):
        from services.datagov import get_water_quality_summary
        mock_wq = {
            "stations": [],
            "summary": {},
            "source": "EPA Water Quality Portal",
            "source_url": "https://www.waterqualitydata.us/",
            "fetched_at": "2024-01-15T00:00:00+00:00",
        }
        with patch("services.datagov.fetch_water_quality", return_value=mock_wq):
            summary = get_water_quality_summary(36.1, -75.5)
        assert "available" in summary
        assert "source" in summary
        assert summary["available"] is False

    def test_get_water_quality_summary_parses_values(self):
        from services.datagov import get_water_quality_summary
        mock_wq = {
            "stations": [],
            "summary": {
                "water_temp_c": 20.5,
                "dissolved_oxygen": 7.2,
                "ph": 8.1,
                "salinity_ppt": 32.0,
                "enterococcus_cfu_100ml": 200.0,
            },
            "source": "EPA Water Quality Portal",
            "source_url": "https://www.waterqualitydata.us/",
            "fetched_at": "2024-01-15T00:00:00+00:00",
        }
        with patch("services.datagov.fetch_water_quality", return_value=mock_wq):
            summary = get_water_quality_summary(36.1, -75.5)
        assert summary["available"] is True
        assert summary["temp_c"] == "20.5"
        assert summary["ph"] == "8.10"
        assert summary["enterococcus_flag"] == "advisory"  # > 104

    def test_enterococcus_ok_flag_below_threshold(self):
        from services.datagov import get_water_quality_summary
        mock_wq = {
            "stations": [],
            "summary": {"enterococcus_cfu_100ml": 50.0},
            "source": "EPA WQP", "source_url": "", "fetched_at": None,
        }
        with patch("services.datagov.fetch_water_quality", return_value=mock_wq):
            summary = get_water_quality_summary(1.0, 1.0)
        assert summary["enterococcus_flag"] == "ok"

    def test_fetch_water_quality_returns_empty_on_error(self):
        from services.datagov import fetch_water_quality, _CACHE
        _CACHE.clear()
        import requests as req
        with patch("services.datagov._HTTP") as mock_http:
            mock_http.get.side_effect = req.RequestException("offline")
            result = fetch_water_quality(36.1, -75.5)
        assert result["stations"] == []
        assert result["summary"] == {}


# ─────────────────────────────────────────────────────────────────────────────
# services/esri_open_data.py
# ─────────────────────────────────────────────────────────────────────────────

class TestEsriOpenData:
    def test_fetch_esri_layers_config_structure(self):
        from services.esri_open_data import fetch_esri_layers_config
        cfg = fetch_esri_layers_config()
        assert "layers" in cfg
        ids = {l["id"] for l in cfg["layers"]}
        assert "esri_piers" in ids
        assert "esri_beaches" in ids

    def test_fetch_pier_locations_returns_list_on_success(self):
        from services.esri_open_data import fetch_pier_locations
        mock_geojson = {
            "features": [
                {
                    "geometry": {"type": "Point", "coordinates": [-75.5, 36.0]},
                    "properties": {"NAME": "Nags Head Pier", "TYPE": "Fishing Pier"},
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_geojson
        with patch("services.esri_open_data._HTTP") as mock_http:
            mock_http.get.return_value = mock_resp
            results = fetch_pier_locations(35.0, -76.0, 37.0, -74.0)
        assert isinstance(results, list)

    def test_fetch_pier_locations_returns_empty_on_error(self):
        from services.esri_open_data import fetch_pier_locations, _CACHE
        _CACHE.clear()
        import requests as req
        with patch("services.esri_open_data._HTTP") as mock_http:
            mock_http.get.side_effect = req.RequestException("offline")
            results = fetch_pier_locations(35.0, -76.0, 37.0, -74.0)
        assert results == []

    def test_normalise_features_extracts_lat_lng(self):
        from services.esri_open_data import _normalise_features
        features = [
            {
                "geometry": {"type": "Point", "coordinates": [-75.0, 36.0]},
                "properties": {"NAME": "Test Pier"},
            }
        ]
        result = _normalise_features(features, default_type="pier")
        assert len(result) == 1
        assert result[0]["lat"] == 36.0
        assert result[0]["lng"] == -75.0
        assert result[0]["name"] == "Test Pier"

    def test_normalise_features_handles_polygon_centroid(self):
        from services.esri_open_data import _normalise_features
        features = [
            {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-75.0, 36.0], [-74.5, 36.0], [-74.5, 36.5], [-75.0, 36.0]]]
                },
                "properties": {"UNIT_NAME": "Cape Hatteras National Seashore"},
            }
        ]
        result = _normalise_features(features, default_type="park")
        assert len(result) == 1
        assert result[0]["geometry_type"] == "Polygon"
        assert result[0]["lat"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# services/nasa_worldview.py
# ─────────────────────────────────────────────────────────────────────────────

class TestNasaWorldview:
    def test_get_gibs_layers_returns_list(self):
        from services.nasa_worldview import get_gibs_layers
        result = get_gibs_layers()
        assert "layers" in result
        assert len(result["layers"]) >= 5
        assert "attribution" in result

    def test_get_sst_tile_config_structure(self):
        from services.nasa_worldview import get_sst_tile_config
        cfg = get_sst_tile_config()
        assert "url" in cfg
        assert "options" in cfg
        assert "metadata" in cfg
        assert "{z}" in cfg["url"]
        assert "{y}" in cfg["url"]
        assert "{x}" in cfg["url"]

    def test_get_imagery_tile_config_for_valid_layer(self):
        from services.nasa_worldview import get_imagery_tile_config
        cfg = get_imagery_tile_config("MODIS_Terra_Chlorophyll_A")
        assert cfg
        assert "url" in cfg
        assert "Chlorophyll" in cfg["metadata"]["label"]

    def test_get_imagery_tile_config_returns_empty_for_unknown(self):
        from services.nasa_worldview import get_imagery_tile_config
        result = get_imagery_tile_config("NONEXISTENT_LAYER_ID")
        assert result == {}

    def test_tile_url_contains_date(self):
        from services.nasa_worldview import get_sst_tile_config
        cfg = get_sst_tile_config(date="2024-01-15")
        assert "2024-01-15" in cfg["url"]

    def test_get_gibs_layers_with_date(self):
        from services.nasa_worldview import get_gibs_layers
        result = get_gibs_layers(date="2024-06-01")
        assert result["date"] == "2024-06-01"
        # All time-varying layer URLs should contain the date
        for layer in result["layers"]:
            if layer["has_time"]:
                assert "2024-06-01" in layer["url"], (
                    f"Layer {layer['id']} URL missing date: {layer['url']}"
                )

    def test_nasa_attribution_present(self):
        from services.nasa_worldview import get_gibs_layers
        result = get_gibs_layers()
        assert "NASA" in result["attribution"]
        assert "GIBS" in result["attribution"]


# ─────────────────────────────────────────────────────────────────────────────
# services/aerial_imagery.py
# ─────────────────────────────────────────────────────────────────────────────

class TestAerialImagery:
    def test_get_aerial_tile_config_structure(self):
        from services.aerial_imagery import get_aerial_tile_config
        cfg = get_aerial_tile_config()
        assert "layers" in cfg
        assert "default" in cfg
        esri_ids = {l["id"] for l in cfg["layers"]}
        assert "esri_world_imagery" in esri_ids

    def test_esri_imagery_url_valid(self):
        from services.aerial_imagery import get_aerial_tile_config
        cfg = get_aerial_tile_config()
        esri = next(l for l in cfg["layers"] if l["id"] == "esri_world_imagery")
        assert "arcgisonline.com" in esri["url"]
        assert "{z}" in esri["url"]

    def test_search_oam_imagery_returns_list_on_success(self):
        from services.aerial_imagery import search_oam_imagery, _CACHE
        _CACHE.clear()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "uuid": "abc123",
                    "title": "Coastal survey 2023",
                    "provider": "NOAA",
                    "acquisition_end": "2023-06-15T12:00:00Z",
                    "gsd": 0.1,
                    "thumbnail": "https://example.com/thumb.jpg",
                    "properties": {"tms": "", "sensor": "DJI"},
                    "bbox": [-75.5, 36.0, -75.0, 36.5],
                    "license": "CC0",
                }
            ]
        }
        with patch("services.aerial_imagery._HTTP") as mock_http:
            mock_http.get.return_value = mock_resp
            results = search_oam_imagery(36.0, -75.5, 36.5, -75.0)
        assert len(results) == 1
        assert results[0]["id"] == "abc123"
        assert results[0]["resolution_m"] == 0.1
        # lat/lng must be computed from bbox so the JS marker filter passes
        assert results[0]["lat"] == pytest.approx(36.25)
        assert results[0]["lng"] == pytest.approx(-75.25)

    def test_search_oam_imagery_returns_empty_on_error(self):
        from services.aerial_imagery import search_oam_imagery, _CACHE
        _CACHE.clear()
        import requests as req
        with patch("services.aerial_imagery._HTTP") as mock_http:
            mock_http.get.side_effect = req.RequestException("offline")
            results = search_oam_imagery(36.0, -75.5, 36.5, -75.0)
        assert results == []

    def test_get_imagery_sources_metadata(self):
        from services.aerial_imagery import get_imagery_sources
        sources = get_imagery_sources()
        assert "sources" in sources
        ids = {s["id"] for s in sources["sources"]}
        assert "esri_imagery" in ids
        assert "openaerial_map" in ids
        # All sources must declare key_required
        for s in sources["sources"]:
            assert s["key_required"] is False


# ─────────────────────────────────────────────────────────────────────────────
# services/hdx_fao.py
# ─────────────────────────────────────────────────────────────────────────────

class TestHdxFao:
    def test_lookup_fao_area_east_coast(self):
        from services.hdx_fao import _lookup_fao_area
        result = _lookup_fao_area(36.0, -75.0)  # NC coast
        assert result["area_code"] in ("21", "31")  # NW or W Central Atlantic

    def test_lookup_fao_area_pacific(self):
        from services.hdx_fao import _lookup_fao_area
        result = _lookup_fao_area(37.8, -122.5)  # San Francisco Bay area
        assert result["area_code"] in ("67", "77")  # NE or E Central Pacific

    def test_lookup_fao_area_returns_dict_structure(self):
        from services.hdx_fao import _lookup_fao_area
        result = _lookup_fao_area(25.0, -80.0)  # Florida
        assert "area_code" in result
        assert "area_name" in result
        assert "fao_url" in result

    def test_fetch_fao_fisheries_zones_falls_back_to_lookup(self):
        """WFS failure should silently fall back to coordinate lookup table."""
        from services.hdx_fao import fetch_fao_fisheries_zones, _CACHE
        _CACHE.clear()
        import requests as req
        with patch("services.hdx_fao._HTTP") as mock_http:
            mock_http.get.side_effect = req.RequestException("offline")
            result = fetch_fao_fisheries_zones(36.0, -75.0)
        assert result["area_code"] != ""
        assert result["area_name"] != ""

    def test_search_hdx_datasets_returns_list_on_success(self):
        from services.hdx_fao import search_hdx_datasets, _CACHE
        _CACHE.clear()
        mock_resp = MagicMock()
        mock_resp.status_code = 200  # raise_for_status is a no-op on 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "result": {
                "results": [
                    {
                        "id": "abc",
                        "title": "Global Fisheries Data",
                        "name": "global-fisheries-data",
                        "notes": "A test dataset about fisheries.",
                        "organization": {"title": "FAO"},
                        "license_title": "CC-BY",
                        "num_resources": 2,
                        "resources": [],
                        "tags": [{"name": "fisheries"}],
                        "last_modified": "2024-01-01",
                    }
                ]
            }
        }
        with patch("services.hdx_fao._HTTP") as mock_http:
            mock_http.get.return_value = mock_resp
            results = search_hdx_datasets("fisheries")
        assert len(results) == 1
        assert results[0]["title"] == "Global Fisheries Data"

    def test_search_hdx_datasets_returns_empty_on_error(self):
        from services.hdx_fao import search_hdx_datasets, _CACHE
        _CACHE.clear()
        import requests as req
        with patch("services.hdx_fao._HTTP") as mock_http:
            mock_http.get.side_effect = req.RequestException("offline")
            results = search_hdx_datasets("fisheries")
        assert results == []

    def test_get_hdx_fao_enrichment_structure(self):
        from services.hdx_fao import get_hdx_fao_enrichment
        with (
            patch("services.hdx_fao.fetch_fao_fisheries_zones",
                  return_value={"area_code": "21", "area_name": "Northwest Atlantic",
                                "fao_url": "https://fao.org"}),
            patch("services.hdx_fao.search_hdx_datasets", return_value=[]),
            patch("services.hdx_fao.fetch_fao_species_info", return_value=None),
        ):
            result = get_hdx_fao_enrichment(36.0, -75.0, ["Striped bass"])
        assert "fao_zone" in result
        assert "hdx_datasets" in result
        assert "species_enrichment" in result


# ─────────────────────────────────────────────────────────────────────────────
# web/geo_api.py — Flask blueprint endpoints
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    from app import create_app
    _app = create_app()
    _app.config["TESTING"] = True
    return _app


@pytest.fixture
def client(app):
    return app.test_client()


class TestGeoApiBlueprint:
    def test_geo_layers_endpoint_exists(self, client):
        resp = client.get("/api/v1/geo/layers")
        assert resp.status_code == 200

    def test_geo_layers_response_structure(self, client):
        resp = client.get("/api/v1/geo/layers")
        data = resp.get_json()
        assert data["ok"] is True
        assert "base_layers" in data["data"]
        assert "overlay_layers" in data["data"]

    def test_geo_environmental_requires_lat_lng(self, client):
        resp = client.get("/api/v1/geo/environmental")
        assert resp.status_code == 400

    def test_geo_environmental_invalid_lat(self, client):
        resp = client.get("/api/v1/geo/environmental?lat=999&lng=-75")
        assert resp.status_code == 400

    def test_geo_environmental_valid_params(self, client):
        with (
            patch("web.geo_api.get_water_quality_summary",
                  return_value={"available": False, "source": "EPA"}),
            patch("web.geo_api.get_sst_tile_config",
                  return_value={"url": "https://gibs.example.com/{z}/{y}/{x}.png",
                                "options": {}}),
            patch("web.geo_api.fetch_beach_closures", return_value=[]),
        ):
            resp = client.get("/api/v1/geo/environmental?lat=36.0&lng=-75.0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "water_quality" in data["data"]

    def test_geo_coastlines_endpoint(self, client):
        with patch("web.geo_api.get_coastlines_geojson",
                   return_value={"type": "FeatureCollection", "features": []}):
            resp = client.get("/api/v1/geo/coastlines")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["type"] == "FeatureCollection"

    def test_geo_coastlines_invalid_res(self, client):
        resp = client.get("/api/v1/geo/coastlines?res=999m")
        assert resp.status_code == 400

    def test_geo_osm_amenities_requires_lat_lng(self, client):
        resp = client.get("/api/v1/geo/osm/amenities")
        assert resp.status_code == 400

    def test_geo_osm_amenities_valid_params(self, client):
        with patch("web.geo_api.fetch_osm_amenities", return_value=[]):
            resp = client.get("/api/v1/geo/osm/amenities?lat=36.0&lng=-75.0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "amenities" in data["data"]

    def test_geo_esri_piers_requires_bbox(self, client):
        resp = client.get("/api/v1/geo/esri/piers")
        assert resp.status_code == 400

    def test_geo_esri_piers_valid_bbox(self, client):
        with patch("web.geo_api.fetch_pier_locations", return_value=[]):
            resp = client.get("/api/v1/geo/esri/piers?south=35&west=-76&north=37&east=-74")
        assert resp.status_code == 200

    def test_geo_esri_piers_rejects_large_bbox(self, client):
        resp = client.get("/api/v1/geo/esri/piers?south=0&west=-180&north=90&east=180")
        assert resp.status_code == 400

    def test_geo_hdx_fao_requires_lat_lng(self, client):
        resp = client.get("/api/v1/geo/hdx-fao")
        assert resp.status_code == 400

    def test_geo_hdx_fao_valid_params(self, client):
        with patch("web.geo_api.get_hdx_fao_enrichment",
                   return_value={"available": True, "fao_zone": {},
                                 "hdx_datasets": [], "species_enrichment": []}):
            resp = client.get("/api/v1/geo/hdx-fao?lat=36.0&lng=-75.0")
        assert resp.status_code == 200

    def test_geo_api_blueprint_registered(self, app):
        assert "geo_api" in app.blueprints

    def test_geo_oam_imagery_requires_bbox(self, client):
        resp = client.get("/api/v1/geo/aerial/oam")
        assert resp.status_code == 400

    def test_geo_oam_imagery_valid_bbox(self, client):
        with patch("web.geo_api.search_oam_imagery", return_value=[]):
            resp = client.get(
                "/api/v1/geo/aerial/oam?south=35&west=-76&north=37&east=-74"
            )
        assert resp.status_code == 200
