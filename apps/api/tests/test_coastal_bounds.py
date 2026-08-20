"""Tests for app.providers.coastal_bounds.

Pure functions — no network mocking needed. `gate_coastal_point` tests
build small synthetic station catalogs directly (reusing
app.providers.stations's typed catalog-entry models) rather than fetching
anything.
"""

from __future__ import annotations

from app.providers.coastal_bounds import (
    CoastRegion,
    classify_coast_region,
    gate_coastal_point,
    is_valid_coordinate,
)
from app.providers.stations import CoopsStationCatalogEntry, NdbcStationCatalogEntry


def test_is_valid_coordinate_accepts_boundary_values() -> None:
    assert is_valid_coordinate(90.0, 180.0)
    assert is_valid_coordinate(-90.0, -180.0)
    assert is_valid_coordinate(0.0, 0.0)


def test_is_valid_coordinate_rejects_out_of_range() -> None:
    assert not is_valid_coordinate(90.1, 0.0)
    assert not is_valid_coordinate(-90.1, 0.0)
    assert not is_valid_coordinate(0.0, 180.1)
    assert not is_valid_coordinate(0.0, -180.1)


def test_classify_coast_region_atlantic() -> None:
    # Wrightsville Beach, NC
    assert classify_coast_region(34.2104, -77.7964) == CoastRegion.ATLANTIC
    # New York Harbor
    assert classify_coast_region(40.6, -74.0) == CoastRegion.ATLANTIC


def test_classify_coast_region_gulf() -> None:
    # Galveston, TX
    assert classify_coast_region(29.3, -94.8) == CoastRegion.GULF


def test_classify_coast_region_pacific() -> None:
    # San Diego, CA
    assert classify_coast_region(32.7, -117.2) == CoastRegion.PACIFIC
    # Seattle, WA
    assert classify_coast_region(47.6, -122.3) == CoastRegion.PACIFIC


def test_classify_coast_region_alaska() -> None:
    # Anchorage, AK
    assert classify_coast_region(61.2, -149.9) == CoastRegion.ALASKA


def test_classify_coast_region_hawaii() -> None:
    # Honolulu, HI
    assert classify_coast_region(21.3, -157.9) == CoastRegion.HAWAII


def test_classify_coast_region_none_for_landlocked_state() -> None:
    # Denver, CO
    assert classify_coast_region(39.7, -104.99) is None


def test_classify_coast_region_none_for_far_outside_us() -> None:
    # Central Europe
    assert classify_coast_region(50.1, 14.4) is None


def _coops(id_: str, lat: float, lng: float) -> CoopsStationCatalogEntry:
    return CoopsStationCatalogEntry(id=id_, name="", lat=lat, lng=lng, state="NC")


def _ndbc(id_: str, lat: float, lng: float) -> NdbcStationCatalogEntry:
    return NdbcStationCatalogEntry(id=id_, lat=lat, lng=lng, has_met=True)


def test_gate_coastal_point_within_range_of_coops_station() -> None:
    lat, lng = 34.2104, -77.7964
    coops = [_coops("8658163", 34.2135, -77.7865)]  # ~0.6 mi away

    result = gate_coastal_point(lat, lng, coops, [])

    assert result.is_coastal
    assert result.nearest_station_miles is not None
    assert result.nearest_station_miles < 1.0


def test_gate_coastal_point_within_range_of_ndbc_station() -> None:
    lat, lng = 34.2104, -77.7964
    ndbc = [_ndbc("41110", 34.19, -77.75)]

    result = gate_coastal_point(lat, lng, [], ndbc)

    assert result.is_coastal


def test_gate_coastal_point_uses_the_nearer_of_both_catalogs() -> None:
    lat, lng = 34.2104, -77.7964
    coops = [_coops("far", 40.0, -75.0)]  # far away
    ndbc = [_ndbc("near", 34.19, -77.75)]  # close

    result = gate_coastal_point(lat, lng, coops, ndbc)

    assert result.is_coastal
    assert result.nearest_station_miles is not None
    assert result.nearest_station_miles < 10.0


def test_gate_coastal_point_rejects_far_inland_point() -> None:
    # Denver, CO — nowhere near either catalog's single NC station.
    lat, lng = 39.7, -104.99
    coops = [_coops("8658163", 34.2135, -77.7865)]

    result = gate_coastal_point(lat, lng, coops, [])

    assert not result.is_coastal
    assert result.nearest_station_miles is not None
    assert result.nearest_station_miles > 60.0


def test_gate_coastal_point_respects_custom_max_miles() -> None:
    lat, lng = 34.2104, -77.7964
    coops = [_coops("8658163", 34.30, -77.90)]  # a handful of miles away

    strict = gate_coastal_point(lat, lng, coops, [], max_miles=1.0)
    lenient = gate_coastal_point(lat, lng, coops, [], max_miles=100.0)

    assert not strict.is_coastal
    assert lenient.is_coastal


def test_gate_coastal_point_empty_catalogs_returns_none_distance() -> None:
    result = gate_coastal_point(34.2104, -77.7964, [], [])

    assert not result.is_coastal
    assert result.nearest_station_miles is None
