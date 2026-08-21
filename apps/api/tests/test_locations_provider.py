"""Tests for app.providers.locations.

Golden tests pin exact values for a few well-known curated locations —
Montauk NY, Wrightsville Beach NC (the same fixture coordinates used
throughout sprints 13-18's tests), and Poipu HI — verifying the JSON
dataset extraction and the pure resolution logic together. Pure
functions throughout; no network mocking needed.
"""

from __future__ import annotations

from app.providers.locations import (
    CuratedLocation,
    find_nearest_locations,
    format_dynamic_id,
    load_curated_locations,
    load_water_temp_profiles,
    monthly_water_temps_for_region,
    parse_dynamic_id,
    resolve_dynamic_location,
    resolved_from_curated,
    timezone_for_point,
)
from app.providers.stations import CoopsStationCatalogEntry, NdbcStationCatalogEntry


def test_load_curated_locations_count_and_uniqueness() -> None:
    locations = load_curated_locations()
    assert len(locations) == 101
    assert len({loc.id for loc in locations}) == 101


def test_load_curated_locations_golden_montauk() -> None:
    locations = load_curated_locations()
    montauk = next(loc for loc in locations if loc.id == "montauk-ny")
    assert montauk == CuratedLocation(
        id="montauk-ny",
        name="Montauk",
        state="NY",
        lat=41.0713,
        lng=-71.9544,
        timezone="America/New_York",
        coops_station="8510560",
        ndbc_stations=["44025", "44017"],
        nws_zone="ANZ338",
        temp_region="northeast",
        conditions_region="atlantic_north",
        temp_offset=0,
    )


def test_load_curated_locations_golden_wrightsville_beach() -> None:
    locations = load_curated_locations()
    wb = next(loc for loc in locations if loc.id == "wrightsville-beach-nc")
    assert wb == CuratedLocation(
        id="wrightsville-beach-nc",
        name="Wrightsville Beach",
        state="NC",
        lat=34.2104,
        lng=-77.7964,
        timezone="America/New_York",
        coops_station="8658163",
        ndbc_stations=["41110", "41037"],
        nws_zone="AMZ158",
        temp_region="nc_south",
        conditions_region="atlantic_mid",
        temp_offset=0,
    )


def test_load_curated_locations_golden_poipu_hawaii() -> None:
    locations = load_curated_locations()
    poipu = next(loc for loc in locations if loc.id == "poipu-hi")
    assert poipu.name == "Poipu (Kauai)"
    assert poipu.state == "HI"
    assert poipu.timezone == "Pacific/Honolulu"
    assert poipu.temp_region == "hawaii"


def test_load_curated_locations_temp_offset_applied_where_present() -> None:
    locations = load_curated_locations()
    lbi = next(loc for loc in locations if loc.id == "long-beach-island-nj")
    assert lbi.temp_offset == 1


def test_load_water_temp_profiles_has_nc_south_and_twelve_months() -> None:
    profiles = load_water_temp_profiles()
    assert "nc_south" in profiles
    assert set(profiles["nc_south"].keys()) == set(range(1, 13))
    assert profiles["nc_south"][7] == 82  # July


def test_monthly_water_temps_for_region_applies_offset() -> None:
    profiles = load_water_temp_profiles()
    base = monthly_water_temps_for_region("northeast", 0, profiles)
    offset = monthly_water_temps_for_region("northeast", 5, profiles)
    assert offset[1] == base[1] + 5


def test_monthly_water_temps_for_region_unknown_falls_back_to_nc_south() -> None:
    profiles = load_water_temp_profiles()
    result = monthly_water_temps_for_region("nonexistent_region", 0, profiles)
    assert result == profiles["nc_south"]


def test_timezone_for_point_florida_panhandle_is_central() -> None:
    assert timezone_for_point("FL", -86.0) == "America/Chicago"


def test_timezone_for_point_florida_east_coast_is_eastern() -> None:
    assert timezone_for_point("FL", -80.0) == "America/New_York"


def test_timezone_for_point_known_state() -> None:
    assert timezone_for_point("nc", -77.0) == "America/New_York"
    assert timezone_for_point("wa", -122.0) == "America/Los_Angeles"


def test_timezone_for_point_unknown_state_returns_none() -> None:
    assert timezone_for_point("ZZ", 0.0) is None


def test_find_nearest_locations_wrightsville_beach_is_closest_to_itself() -> None:
    locations = load_curated_locations()
    matches = find_nearest_locations(34.2104, -77.7964, locations, n=3)

    assert matches[0].location.id == "wrightsville-beach-nc"
    assert matches[0].distance_miles == 0.0
    assert len(matches) == 3


def test_find_nearest_locations_respects_max_miles() -> None:
    locations = load_curated_locations()
    matches = find_nearest_locations(34.2104, -77.7964, locations, n=100, max_miles=0.1)

    assert len(matches) == 1
    assert matches[0].location.id == "wrightsville-beach-nc"


def test_format_and_parse_dynamic_id_roundtrip() -> None:
    location_id = format_dynamic_id(34.2104, -77.7964)
    assert location_id == "pt_34.210_-77.796"

    parsed = parse_dynamic_id(location_id)
    assert parsed == (34.21, -77.796)


def test_parse_dynamic_id_rejects_non_dynamic_id() -> None:
    assert parse_dynamic_id("wrightsville-beach-nc") is None


def test_parse_dynamic_id_rejects_out_of_range_coordinates() -> None:
    assert parse_dynamic_id("pt_95.000_-77.800") is None


def test_parse_dynamic_id_rejects_malformed_body() -> None:
    assert parse_dynamic_id("pt_not-a-number_-77.800") is None
    assert parse_dynamic_id("pt_onlyonepart") is None


def test_resolved_from_curated_uses_coops_station_for_water_temp() -> None:
    locations = load_curated_locations()
    wb = next(loc for loc in locations if loc.id == "wrightsville-beach-nc")

    resolved = resolved_from_curated(wb)

    assert resolved.is_dynamic is False
    assert resolved.water_temp_station == "8658163" == wb.coops_station
    assert resolved.id == "wrightsville-beach-nc"


def test_resolve_dynamic_location_near_wrightsville_beach_inherits_regional_fields() -> (
    None
):
    locations = load_curated_locations()
    # A point ~1 mile from Wrightsville Beach, with its own nearby stations.
    lat, lng = 34.22, -77.80
    coops_tide = [
        CoopsStationCatalogEntry(
            id="8658163",
            name="Wrightsville Beach",
            lat=34.2135,
            lng=-77.7865,
            state="NC",
        )
    ]
    coops_watertemp = list(coops_tide)
    ndbc = [
        NdbcStationCatalogEntry(id="41110", lat=34.194, lng=-77.75, has_met=True),
        NdbcStationCatalogEntry(id="41037", lat=34.14, lng=-77.72, has_met=True),
    ]

    resolved, anchor_miles = resolve_dynamic_location(
        lat, lng, locations, coops_tide, coops_watertemp, ndbc
    )

    assert resolved.is_dynamic is True
    assert resolved.coops_station == "8658163"
    assert resolved.water_temp_station == "8658163"
    assert resolved.ndbc_stations == ["41110", "41037"]
    assert resolved.temp_region == "nc_south"  # inherited from Wrightsville Beach
    assert resolved.conditions_region == "atlantic_mid"
    assert resolved.timezone == "America/New_York"
    assert (
        resolved.nws_zone == "AMZ158"
    )  # inherited, within the 75mi zone-inherit radius
    assert resolved.name == "Near Wrightsville Beach"
    assert anchor_miles < 5.0
    # anchor_miles is embedded on the model itself too, matching the
    # tuple's second element — app.domain.confidence's station-distance
    # factor reads it from here, not from the tuple.
    assert resolved.anchor_miles == anchor_miles


def test_resolve_dynamic_location_far_from_everything_still_resolves_with_defaults() -> (
    None
):
    # Mid-Pacific, no curated neighbor and no stations at all.
    lat, lng = 10.0, -140.0

    resolved, anchor_miles = resolve_dynamic_location(lat, lng, (), [], [], [])

    assert resolved.is_dynamic is True
    assert resolved.coops_station == ""
    assert resolved.water_temp_station == ""
    assert resolved.ndbc_stations == []
    assert resolved.nws_zone == ""
    assert resolved.temp_region == "nc_south"
    assert resolved.conditions_region == "atlantic_mid"
    assert resolved.timezone == "America/New_York"
    assert anchor_miles == float("inf")
    # The raw tuple can be infinite (no anchor found at all); the model
    # field is None instead — infinity isn't a meaningful JSON value.
    assert resolved.anchor_miles is None


def test_resolve_dynamic_location_nws_zone_not_inherited_when_too_far() -> None:
    locations = load_curated_locations()
    # Kodiak, AK — no curated Alaska location exists, so the nearest curated
    # neighbor (a Pacific NW spot) is over 1,000 miles away, well past the
    # 75-mile NWS-zone-inherit radius.
    lat, lng = 57.79, -152.4

    resolved, anchor_miles = resolve_dynamic_location(lat, lng, locations, [], [], [])

    assert resolved.nws_zone == ""
    assert anchor_miles > 1000.0
