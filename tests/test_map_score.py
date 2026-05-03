"""Tests for /api/v1/map/score and /api/v1/map/habitats endpoints.

These endpoints were added as part of the fishing-map refactor:
- /api/v1/map/score  : hourly bite-likelihood scores (1–10) for a lat/lng
- /api/v1/map/habitats : consolidated all-types habitat fetch for a bbox
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import web.api as api_module
from app import create_app
from storage.sqlite import init_db


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_api_caches():
    """Clear module-level LRU caches in web.api between tests."""
    api_module._SCORE_CACHE.clear()
    api_module._HABITATS_CACHE.clear()
    yield
    api_module._SCORE_CACHE.clear()
    api_module._HABITATS_CACHE.clear()


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_map_score.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    init_db()
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ── Shared mock return value for compute_hourly_strike_score ─────────────────


def _mock_score_result(lat: float = 34.21, lng: float = -77.80):
    """Minimal valid result matching compute_hourly_strike_score() shape."""
    return {
        "hours": [
            {
                "hour": h,
                "score": 5,
                "label": "Fair",
                "factors": {
                    "tide": "falling",
                    "solunar": "none",
                    "wind_mph": 8,
                    "wave_ft": 2.0,
                    "water_temp_f": 72,
                },
            }
            for h in range(24)
        ],
        "date": "2026-05-03",
        "location": {"lat": lat, "lng": lng},
        "moon_phase": "Waxing Gibbous",
        "solunar_rating": "Good",
        "water_temp_f": 72.0,
    }


# ── /api/v1/map/score ─────────────────────────────────────────────────────────


class TestMapScore:
    def test_missing_lat_returns_400(self, client):
        rv = client.get("/api/v1/map/score?lng=-77.80")
        assert rv.status_code == 400
        data = json.loads(rv.data)
        assert data.get("ok") is False or "error" in data

    def test_missing_lng_returns_400(self, client):
        rv = client.get("/api/v1/map/score?lat=34.21")
        assert rv.status_code == 400

    def test_invalid_lat_string_returns_400(self, client):
        rv = client.get("/api/v1/map/score?lat=abc&lng=-77.80")
        assert rv.status_code == 400

    def test_lat_out_of_range_returns_400(self, client):
        rv = client.get("/api/v1/map/score?lat=999&lng=-77.80")
        assert rv.status_code == 400

    def test_lng_out_of_range_returns_400(self, client):
        rv = client.get("/api/v1/map/score?lat=34.21&lng=999")
        assert rv.status_code == 400

    def test_valid_request_returns_200(self, client):
        with patch(
            "web.api.compute_hourly_strike_score",
            return_value=_mock_score_result(),
        ):
            rv = client.get("/api/v1/map/score?lat=34.21&lng=-77.80")
        assert rv.status_code == 200

    def test_response_envelope_ok_true(self, client):
        with patch(
            "web.api.compute_hourly_strike_score",
            return_value=_mock_score_result(),
        ):
            rv = client.get("/api/v1/map/score?lat=34.21&lng=-77.80")
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert "data" in data

    def test_response_has_24_hours(self, client):
        with patch(
            "web.api.compute_hourly_strike_score",
            return_value=_mock_score_result(),
        ):
            rv = client.get("/api/v1/map/score?lat=34.21&lng=-77.80")
        data = json.loads(rv.data)
        assert len(data["data"]["hours"]) == 24

    def test_each_hour_has_required_fields(self, client):
        with patch(
            "web.api.compute_hourly_strike_score",
            return_value=_mock_score_result(),
        ):
            rv = client.get("/api/v1/map/score?lat=34.21&lng=-77.80")
        hours = json.loads(rv.data)["data"]["hours"]
        for h in hours:
            assert "hour" in h
            assert "score" in h
            assert "label" in h
            assert "factors" in h

    def test_response_has_metadata_fields(self, client):
        with patch(
            "web.api.compute_hourly_strike_score",
            return_value=_mock_score_result(),
        ):
            rv = client.get("/api/v1/map/score?lat=34.21&lng=-77.80")
        data = json.loads(rv.data)["data"]
        assert "date" in data
        assert "location" in data
        assert "moon_phase" in data
        assert "solunar_rating" in data
        assert "water_temp_f" in data

    def test_cache_control_header_present(self, client):
        with patch(
            "web.api.compute_hourly_strike_score",
            return_value=_mock_score_result(),
        ):
            rv = client.get("/api/v1/map/score?lat=34.21&lng=-77.80")
        assert "Cache-Control" in rv.headers

    def test_date_param_passed_through(self, client):
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return _mock_score_result()

        with patch("web.api.compute_hourly_strike_score", side_effect=_capture):
            client.get("/api/v1/map/score?lat=34.21&lng=-77.80&date=2026-06-15")
        assert captured.get("date_str") == "2026-06-15"

    def test_tz_param_passed_through(self, client):
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return _mock_score_result()

        with patch("web.api.compute_hourly_strike_score", side_effect=_capture):
            client.get(
                "/api/v1/map/score?lat=34.21&lng=-77.80&tz=America%2FChicago"
            )
        assert captured.get("tz_name") == "America/Chicago"

    def test_service_error_returns_503(self, client):
        with patch(
            "web.api.compute_hourly_strike_score",
            side_effect=RuntimeError("network error"),
        ):
            rv = client.get("/api/v1/map/score?lat=34.21&lng=-77.80")
        assert rv.status_code == 503

    def test_second_call_returns_cached_result(self, client):
        call_count = {"n": 0}

        def _counting(**kwargs):
            call_count["n"] += 1
            return _mock_score_result()

        with patch("web.api.compute_hourly_strike_score", side_effect=_counting):
            client.get("/api/v1/map/score?lat=34.21&lng=-77.80")
            client.get("/api/v1/map/score?lat=34.21&lng=-77.80")

        # The underlying function should only be called once (second hit is cached)
        assert call_count["n"] == 1


# ── /api/v1/map/habitats ──────────────────────────────────────────────────────


class TestMapHabitats:
    def test_missing_bbox_returns_400(self, client):
        rv = client.get("/api/v1/map/habitats")
        assert rv.status_code == 400
        data = json.loads(rv.data)
        assert data.get("ok") is False or "error" in data

    def test_partial_bbox_returns_400(self, client):
        rv = client.get("/api/v1/map/habitats?south=34&west=-78&north=35")
        assert rv.status_code == 400

    def test_invalid_bbox_values_returns_400(self, client):
        rv = client.get("/api/v1/map/habitats?south=abc&west=-78&north=35&east=-77")
        assert rv.status_code == 400

    def test_inverted_lat_bbox_returns_400(self, client):
        # south > north is invalid
        rv = client.get("/api/v1/map/habitats?south=35&west=-78&north=34&east=-77")
        assert rv.status_code == 400

    def test_oversized_bbox_returns_400(self, client):
        # Span > 10° is rejected
        rv = client.get("/api/v1/map/habitats?south=20&west=-100&north=35&east=-77")
        assert rv.status_code == 400

    def test_valid_bbox_returns_200(self, client):
        with patch("web.api.fetch_ai_habitats", return_value=[]):
            rv = client.get(
                "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
            )
        assert rv.status_code == 200

    def test_response_envelope_ok_true(self, client):
        with patch("web.api.fetch_ai_habitats", return_value=[]):
            rv = client.get(
                "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
            )
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert "data" in data

    def test_response_has_features_list(self, client):
        with patch("web.api.fetch_ai_habitats", return_value=[]):
            rv = client.get(
                "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
            )
        data = json.loads(rv.data)["data"]
        assert "features" in data
        assert isinstance(data["features"], list)

    def test_response_has_count(self, client):
        with patch("web.api.fetch_ai_habitats", return_value=[]):
            rv = client.get(
                "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
            )
        data = json.loads(rv.data)["data"]
        assert "count" in data

    def test_features_returned_from_fetch(self, client):
        sample_feature = {
            "id": "way/123",
            "lat": 34.5,
            "lng": -77.5,
            "osmType": "saltmarsh",
            "name": "Test Marsh",
        }
        with patch("web.api.fetch_ai_habitats", return_value=[sample_feature]):
            rv = client.get(
                "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
            )
        data = json.loads(rv.data)["data"]
        assert data["count"] == 1
        assert data["features"][0]["id"] == "way/123"

    def test_deduplication_by_id(self, client):
        dup_feature = {"id": "way/123", "lat": 34.5, "lng": -77.5, "osmType": "reef"}

        call_count = {"n": 0}

        def _returning_dup(south, west, north, east, habitat_type):
            call_count["n"] += 1
            return [dup_feature]  # every type returns the same feature

        with patch("web.api.fetch_ai_habitats", side_effect=_returning_dup):
            rv = client.get(
                "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
            )
        data = json.loads(rv.data)["data"]
        # Should be deduped to a single feature regardless of how many types returned it
        assert data["count"] == 1

    def test_types_filter_param(self, client):
        called_types: list[str] = []

        def _track(south, west, north, east, habitat_type):
            called_types.append(habitat_type)
            return []

        with patch("web.api.fetch_ai_habitats", side_effect=_track):
            client.get(
                "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
                "&types=surf,kelp"
            )
        assert set(called_types) == {"surf", "kelp"}

    def test_invalid_types_param_returns_400(self, client):
        rv = client.get(
            "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
            "&types=notatype,alsowrong"
        )
        assert rv.status_code == 400

    def test_cache_control_header_present(self, client):
        with patch("web.api.fetch_ai_habitats", return_value=[]):
            rv = client.get(
                "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
            )
        assert "Cache-Control" in rv.headers

    def test_fetch_errors_tolerated_returns_empty(self, client):
        """A fetch error for one type should not crash the whole request."""
        with patch(
            "web.api.fetch_ai_habitats", side_effect=RuntimeError("timeout")
        ):
            rv = client.get(
                "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
            )
        assert rv.status_code == 200
        data = json.loads(rv.data)["data"]
        assert data["features"] == []

    def test_count_matches_features_length(self, client):
        features = [
            {"id": f"node/{i}", "lat": 34.0 + i * 0.01, "lng": -77.5, "osmType": "reef"}
            for i in range(5)
        ]
        with patch("web.api.fetch_ai_habitats", return_value=features[:3]):
            rv = client.get(
                "/api/v1/map/habitats?south=34&west=-78&north=35&east=-77"
                "&types=surf"
            )
        data = json.loads(rv.data)["data"]
        assert data["count"] == len(data["features"])
