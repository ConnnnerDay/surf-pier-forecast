"""Tests for dynamic (any-coordinate) coastal location resolution.

Network access to the NOAA/NDBC station catalogs is mocked so these tests run
offline and deterministically; they exercise the pure assembly/gating logic
plus the graceful fallback to curated stations when a catalog is unavailable.
"""

from __future__ import annotations

import pytest

import locations as L
import services.stations as stations


# Wrightsville Beach, NC — the project's default reference point.
_WB_LAT, _WB_LNG = 34.2104, -77.7964


@pytest.fixture
def fake_catalogs(monkeypatch):
    """Inject small in-memory CO-OPS and NDBC catalogs near Wrightsville Beach."""
    coops = [
        {"id": "8658163", "name": "Wrightsville Beach", "lat": 34.21, "lng": -77.79, "state": "NC"},
        {"id": "8656483", "name": "Beaufort", "lat": 34.72, "lng": -76.67, "state": "NC"},
        {"id": "9410230", "name": "La Jolla", "lat": 32.87, "lng": -117.26, "state": "CA"},
    ]
    ndbc = [
        {"id": "41110", "lat": 34.14, "lng": -77.71, "has_met": True},
        {"id": "41037", "lat": 33.99, "lng": -77.36, "has_met": True},
        {"id": "DRYA1", "lat": 30.0, "lng": -88.0, "has_met": False},
    ]
    monkeypatch.setattr(stations, "_load_coops", lambda: coops)
    monkeypatch.setattr(stations, "_load_ndbc", lambda: ndbc)
    return coops, ndbc


class TestIdEncoding:
    def test_round_trips(self):
        loc_id = L.format_dynamic_id(_WB_LAT, _WB_LNG)
        assert loc_id.startswith("pt_")
        lat, lng = L.parse_dynamic_id(loc_id)
        assert lat == pytest.approx(_WB_LAT, abs=1e-4)
        assert lng == pytest.approx(_WB_LNG, abs=1e-4)

    def test_negative_longitude_preserved(self):
        # Longitude minus sign must survive the underscore split.
        lat, lng = L.parse_dynamic_id("pt_47.6062_-122.3321")
        assert lat == pytest.approx(47.6062)
        assert lng == pytest.approx(-122.3321)

    def test_rejects_curated_and_garbage_ids(self):
        assert L.parse_dynamic_id("wrightsville_beach") is None
        assert L.parse_dynamic_id("pt_garbage") is None
        assert L.parse_dynamic_id("pt_999_999") is None  # out of range


class TestNearestStations:
    def test_nearest_coops_picks_closest(self, fake_catalogs):
        result = stations.nearest_coops_station(_WB_LAT, _WB_LNG)
        assert result is not None
        assert result["id"] == "8658163"
        assert result["state"] == "NC"

    def test_nearest_ndbc_excludes_non_met(self, fake_catalogs):
        result = stations.nearest_ndbc_stations(_WB_LAT, _WB_LNG, n=2)
        ids = [s["id"] for s in result]
        assert ids == ["41110", "41037"]
        assert "DRYA1" not in ids

    def test_empty_catalog_returns_none(self, monkeypatch):
        monkeypatch.setattr(stations, "_load_coops", lambda: [])
        monkeypatch.setattr(stations, "_load_ndbc", lambda: [])
        assert stations.nearest_coops_station(_WB_LAT, _WB_LNG) is None
        assert stations.nearest_ndbc_stations(_WB_LAT, _WB_LNG) == []


class TestBuildDynamicLocation:
    def test_has_all_forecast_fields(self, fake_catalogs):
        loc = L.build_dynamic_location(_WB_LAT, _WB_LNG)
        for key in (
            "id", "name", "state", "lat", "lng", "timezone",
            "coops_station", "ndbc_stations", "nws_zone",
            "conditions_region", "temp_region", "fish_region",
        ):
            assert key in loc, f"missing {key}"
        assert loc["coops_station"] == "8658163"
        assert loc["ndbc_stations"] == ["41110", "41037"]
        assert loc["dynamic"] is True

    def test_resolves_through_get_location(self, fake_catalogs):
        loc_id = L.format_dynamic_id(_WB_LAT, _WB_LNG)
        loc = L.get_location(loc_id)
        assert loc is not None
        assert loc["id"] == loc_id
        assert loc["coops_station"] == "8658163"

    def test_region_inherited_from_nearest_curated(self, fake_catalogs):
        # Near Wrightsville Beach the inherited coast must be the east coast.
        loc = L.build_dynamic_location(_WB_LAT, _WB_LNG)
        assert loc["conditions_region"].startswith(("atlantic", "gulf"))

    def test_falls_back_to_curated_stations_without_catalog(self, monkeypatch):
        # No catalog available → dynamic location still works, inheriting the
        # nearest curated location's stations (today's behaviour, never worse).
        monkeypatch.setattr(stations, "_load_coops", lambda: [])
        monkeypatch.setattr(stations, "_load_ndbc", lambda: [])
        loc = L.build_dynamic_location(_WB_LAT, _WB_LNG)
        assert loc["coops_station"]  # non-empty, from a curated neighbour
        assert loc["ndbc_stations"]


class TestCoastalGate:
    def test_coastal_point_allowed(self, fake_catalogs):
        assert L.dynamic_location_for_point(_WB_LAT, _WB_LNG) is not None

    def test_inland_point_rejected(self, fake_catalogs):
        # Wichita, KS — hundreds of miles from any station or curated spot.
        assert L.dynamic_location_for_point(37.6872, -97.3301) is None
