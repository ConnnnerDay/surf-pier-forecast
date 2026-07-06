"""Tests for services/bathymetry.py — NOAA NCEI coastal DEM depth lookups."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import services.bathymetry as bathymetry


@pytest.fixture(autouse=True)
def _clear_cache():
    bathymetry.cache_clear()
    yield
    bathymetry.cache_clear()


def _mock_resp(json_body, status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = json_body
    m.status_code = status_code
    return m


class TestFetchDepthAtPoint:
    def test_returns_feet_for_underwater_point(self):
        with patch("services.bathymetry.http_get", return_value=_mock_resp({"value": "-12.5"})):
            depth = bathymetry.fetch_depth_at_point(34.2, -77.8)
        assert depth == round(-12.5 * 3.28084, 1)

    def test_returns_none_for_nodata(self):
        with patch("services.bathymetry.http_get", return_value=_mock_resp({"value": "NoData"})):
            assert bathymetry.fetch_depth_at_point(34.2, -77.8) is None

    def test_returns_none_on_missing_value_key(self):
        with patch("services.bathymetry.http_get", return_value=_mock_resp({})):
            assert bathymetry.fetch_depth_at_point(34.2, -77.8) is None

    def test_returns_none_on_exception(self):
        with patch("services.bathymetry.http_get", side_effect=Exception("timeout")):
            assert bathymetry.fetch_depth_at_point(34.2, -77.8) is None

    def test_caches_result(self):
        with patch(
            "services.bathymetry.http_get", return_value=_mock_resp({"value": "-5.0"})
        ) as mock_get:
            bathymetry.fetch_depth_at_point(34.2, -77.8)
            bathymetry.fetch_depth_at_point(34.2, -77.8)
        assert mock_get.call_count == 1


class TestOffsetPoint:
    def test_east_bearing_moves_longitude_positive(self):
        lat, lng = bathymetry._offset_point(34.0, -77.0, 90.0, 1.0)
        assert lng > -77.0
        assert lat == pytest.approx(34.0, abs=0.01)

    def test_south_bearing_moves_latitude_negative(self):
        lat, lng = bathymetry._offset_point(34.0, -77.0, 180.0, 1.0)
        assert lat < 34.0


class TestGetDepthProfile:
    def test_available_with_full_profile(self):
        with patch(
            "services.bathymetry.http_get", return_value=_mock_resp({"value": "-10.0"})
        ):
            out = bathymetry.get_depth_profile(34.2, -77.8, orientation="east")
        assert out["available"] is True
        assert out["point_depth_ft"] == pytest.approx(-32.8, abs=0.1)
        assert len(out["profile"]) == 3
        assert {p["distance_nm"] for p in out["profile"]} == {0.5, 1.0, 2.0}

    def test_hawaii_orientation_skips_directional_profile(self):
        with patch(
            "services.bathymetry.http_get", return_value=_mock_resp({"value": "-10.0"})
        ):
            out = bathymetry.get_depth_profile(21.3, -157.8, orientation="hawaii")
        assert out["profile"] == []
        assert out["point_depth_ft"] is not None

    def test_unavailable_when_all_lookups_fail(self):
        with patch("services.bathymetry.http_get", side_effect=Exception("down")):
            out = bathymetry.get_depth_profile(34.2, -77.8, orientation="east")
        assert out["available"] is False
        assert out["point_depth_ft"] is None
        assert out["profile"] == []
