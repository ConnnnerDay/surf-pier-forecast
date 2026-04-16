"""Tests for services/fish_structures.py and GET /api/map/structures.

Coverage
--------
- _classify_osm_tags: every tag family mapped in the function
- _build_overpass_query: per-type tag inclusion and empty-types guard
- _deduplicate: name-based and proximity-based dedup
- fetch_osm_structures: Overpass response parsing, bbox filtering, type filter
- fetch_noaa_structures: NOAA ENC layer routing, polygon centroid, soft failure
- find_fish_structures: merge + dedup + tip attachment, type subset filtering
- find_fish_structures cache: hit/miss/ttl/key-isolation/eviction
- GET /api/map/structures: happy path, missing params, bad bbox, bad types,
                           oversized viewport, zoom_required flag
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

import services.fish_structures as _fs_mod
from services.fish_structures import (
    STRUCTURE_TIPS,
    VALID_TYPES,
    _build_overpass_query,
    _classify_osm_tags,
    _deduplicate,
    cache_clear,
    fetch_noaa_structures,
    fetch_osm_structures,
    find_fish_structures,
)


# Clear the module-level cache before every test so cached results from one
# test don't bleed into another when the same bbox is reused.
@pytest.fixture(autouse=True)
def _clear_structure_cache():
    cache_clear()
    yield
    cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# _classify_osm_tags
# ─────────────────────────────────────────────────────────────────────────────


class TestClassifyOsmTags:
    # Habitats — wetland subtypes
    def test_seagrass(self):
        assert (
            _classify_osm_tags({"natural": "wetland", "wetland": "seagrass"})
            == "grass_flat"
        )

    def test_saltmarsh(self):
        assert (
            _classify_osm_tags({"natural": "wetland", "wetland": "saltmarsh"})
            == "saltmarsh"
        )

    def test_mangrove(self):
        assert (
            _classify_osm_tags({"natural": "wetland", "wetland": "mangrove"})
            == "mangrove"
        )

    def test_tidalflat(self):
        assert (
            _classify_osm_tags({"natural": "wetland", "wetland": "tidalflat"})
            == "tidal_flat"
        )

    def test_unknown_wetland_returns_none(self):
        assert _classify_osm_tags({"natural": "wetland", "wetland": "bog"}) is None

    def test_natural_mud(self):
        assert _classify_osm_tags({"natural": "mud"}) == "tidal_flat"

    def test_natural_beach(self):
        assert _classify_osm_tags({"natural": "beach"}) == "beach"

    def test_natural_bay(self):
        assert _classify_osm_tags({"natural": "bay"}) == "inlet"

    def test_natural_reef(self):
        assert _classify_osm_tags({"natural": "reef"}) == "reef"

    def test_natural_shoal(self):
        assert _classify_osm_tags({"natural": "shoal"}) == "shoal"

    def test_natural_rock(self):
        assert _classify_osm_tags({"natural": "rock"}) == "shoal"

    def test_natural_cape(self):
        assert _classify_osm_tags({"natural": "cape"}) == "point"

    def test_natural_headland(self):
        assert _classify_osm_tags({"natural": "headland"}) == "point"

    def test_natural_peninsula(self):
        assert _classify_osm_tags({"natural": "peninsula"}) == "point"

    def test_harbour(self):
        assert _classify_osm_tags({"harbour": "yes"}) == "inlet"

    # Oyster aquaculture
    def test_aquaculture_produce_oyster(self):
        assert (
            _classify_osm_tags({"landuse": "aquaculture", "produce": "oyster"})
            == "oyster_reef"
        )

    def test_aquaculture_product_oysters(self):
        assert (
            _classify_osm_tags({"landuse": "aquaculture", "product": "oysters"})
            == "oyster_reef"
        )

    def test_aquaculture_other_produce_returns_none(self):
        assert _classify_osm_tags({"landuse": "aquaculture", "produce": "fish"}) is None

    # Wrecks
    def test_historic_wreck(self):
        assert _classify_osm_tags({"historic": "wreck"}) == "wreck"

    def test_seamark_wreck(self):
        assert _classify_osm_tags({"seamark:type": "wreck"}) == "wreck"

    # Waterways
    def test_tidal_channel(self):
        assert _classify_osm_tags({"waterway": "tidal_channel"}) == "inlet"

    def test_river(self):
        assert _classify_osm_tags({"waterway": "river"}) == "inlet"

    def test_canal(self):
        assert _classify_osm_tags({"waterway": "canal"}) == "inlet"

    def test_stream(self):
        assert _classify_osm_tags({"waterway": "stream"}) == "inlet"

    def test_weir(self):
        assert _classify_osm_tags({"waterway": "weir"}) == "jetty"

    def test_dam(self):
        assert _classify_osm_tags({"waterway": "dam"}) == "jetty"

    def test_dock(self):
        assert _classify_osm_tags({"waterway": "dock"}) == "pier"

    # Piers / jetties / bridges
    def test_man_made_pier(self):
        assert _classify_osm_tags({"man_made": "pier"}) == "pier"

    def test_leisure_pier(self):
        assert _classify_osm_tags({"leisure": "pier"}) == "pier"

    def test_man_made_jetty(self):
        assert _classify_osm_tags({"man_made": "jetty"}) == "jetty"

    def test_groyne(self):
        assert _classify_osm_tags({"man_made": "groyne"}) == "jetty"

    def test_breakwater(self):
        assert _classify_osm_tags({"man_made": "breakwater"}) == "jetty"

    def test_wharf(self):
        assert _classify_osm_tags({"man_made": "wharf"}) == "pier"

    def test_bridge_with_highway(self):
        assert _classify_osm_tags({"bridge": "yes", "highway": "primary"}) == "bridge"

    def test_bridge_without_highway_returns_none(self):
        assert _classify_osm_tags({"bridge": "yes"}) is None

    # Marinas / boat ramps
    def test_amenity_marina(self):
        assert _classify_osm_tags({"amenity": "marina"}) == "marina"

    def test_leisure_marina(self):
        assert _classify_osm_tags({"leisure": "marina"}) == "marina"

    def test_boat_ramp(self):
        assert _classify_osm_tags({"amenity": "boat_ramp"}) == "pier"

    # Points (lighthouse / platform)
    def test_lighthouse(self):
        assert _classify_osm_tags({"man_made": "lighthouse"}) == "point"

    def test_offshore_platform(self):
        assert _classify_osm_tags({"man_made": "offshore_platform"}) == "point"

    # Buoys
    def test_seamark_buoy_lateral(self):
        assert _classify_osm_tags({"seamark:type": "buoy_lateral"}) == "buoy"

    def test_seamark_buoy_cardinal(self):
        assert _classify_osm_tags({"seamark:type": "buoy_cardinal"}) == "buoy"

    def test_man_made_buoy(self):
        assert _classify_osm_tags({"man_made": "buoy"}) == "buoy"

    # Leisure fishing / shop
    def test_leisure_fishing(self):
        assert _classify_osm_tags({"leisure": "fishing"}) == "fishing"

    def test_shop_fishing(self):
        assert _classify_osm_tags({"shop": "fishing"}) == "fishing_shop"

    def test_unrecognised_tags_returns_none(self):
        assert _classify_osm_tags({"tourism": "hotel"}) is None

    def test_empty_tags_returns_none(self):
        assert _classify_osm_tags({}) is None


# ─────────────────────────────────────────────────────────────────────────────
# _build_overpass_query
# ─────────────────────────────────────────────────────────────────────────────

BBOX = "25.0,-80.5,25.5,-80.0"


class TestBuildOverpassQuery:
    def test_empty_types_returns_empty_string(self):
        assert _build_overpass_query(BBOX, set()) == ""

    def test_grass_flat_includes_seagrass_tags(self):
        q = _build_overpass_query(BBOX, {"grass_flat"})
        assert '"wetland"="seagrass"' in q
        assert BBOX in q

    def test_wreck_includes_historic_and_seamark(self):
        q = _build_overpass_query(BBOX, {"wreck"})
        assert '"historic"="wreck"' in q
        assert '"seamark:type"="wreck"' in q

    def test_pier_includes_dock_and_boat_ramp(self):
        q = _build_overpass_query(BBOX, {"pier"})
        assert '"man_made"="pier"' in q
        assert '"waterway"="dock"' in q
        assert '"amenity"="boat_ramp"' in q

    def test_bridge_includes_highway_regex(self):
        q = _build_overpass_query(BBOX, {"bridge"})
        assert '"bridge"="yes"' in q
        assert "highway" in q

    def test_oyster_and_reef_both_trigger_reef_tags(self):
        q_oyster = _build_overpass_query(BBOX, {"oyster_reef"})
        q_reef = _build_overpass_query(BBOX, {"reef"})
        # oyster_reef uses aquaculture landuse tags (NOT natural=reef);
        # natural=reef is only added when the "reef" type is requested.
        assert '"natural"="reef"' not in q_oyster
        assert '"natural"="reef"' in q_reef
        assert '"landuse"="aquaculture"' in q_oyster

    def test_inlet_includes_tidal_channel_and_bay(self):
        q = _build_overpass_query(BBOX, {"inlet"})
        assert '"waterway"="tidal_channel"' in q
        assert '"natural"="bay"' in q

    def test_buoy_includes_all_seamark_variants(self):
        q = _build_overpass_query(BBOX, {"buoy"})
        assert "buoy_lateral" in q
        assert "buoy_cardinal" in q
        assert "buoy_safe_water" in q
        assert '"man_made"="buoy"' in q

    def test_output_directive_present(self):
        q = _build_overpass_query(BBOX, {"pier"})
        assert q.startswith("[out:json]")
        # struct types use named-set output: (...)->.s;.s out center;
        assert q.strip().endswith(".s out center;")

    def test_multiple_types_combined_in_single_query(self):
        q = _build_overpass_query(BBOX, {"pier", "jetty", "beach"})
        assert '"man_made"="pier"' in q
        assert '"man_made"="jetty"' in q
        assert '"natural"="beach"' in q


# ─────────────────────────────────────────────────────────────────────────────
# _deduplicate
# ─────────────────────────────────────────────────────────────────────────────


class TestDeduplicate:
    def test_empty_input(self):
        assert _deduplicate([]) == []

    def test_passthrough_unique_spots(self):
        spots = [
            {"lat": 25.0, "lng": -80.0, "type": "pier", "name": "Pier A"},
            {"lat": 26.0, "lng": -81.0, "type": "jetty", "name": "Jetty B"},
        ]
        assert len(_deduplicate(spots)) == 2

    def test_name_dedup_same_type(self):
        spots = [
            {"lat": 25.0, "lng": -80.0, "type": "pier", "name": "Sunshine Pier"},
            {"lat": 25.001, "lng": -80.001, "type": "pier", "name": "Sunshine Pier"},
        ]
        result = _deduplicate(spots)
        assert len(result) == 1
        assert result[0]["lat"] == 25.0  # first one kept

    def test_name_dedup_different_type_keeps_both(self):
        spots = [
            {"lat": 25.0, "lng": -80.0, "type": "pier", "name": "Marina Walk"},
            {"lat": 25.0, "lng": -80.0, "type": "marina", "name": "Marina Walk"},
        ]
        assert len(_deduplicate(spots)) == 2

    def test_name_dedup_case_insensitive(self):
        spots = [
            {"lat": 25.0, "lng": -80.0, "type": "bridge", "name": "Tampa Bay Bridge"},
            {
                "lat": 25.001,
                "lng": -80.001,
                "type": "bridge",
                "name": "tampa bay bridge",
            },
        ]
        assert len(_deduplicate(spots)) == 1

    def test_proximity_dedup_within_threshold(self):
        # 0.001° apart — well within the 0.002° default threshold
        spots = [
            {"lat": 25.000, "lng": -80.000, "type": "jetty", "name": ""},
            {"lat": 25.001, "lng": -80.001, "type": "jetty", "name": ""},
        ]
        assert len(_deduplicate(spots)) == 1

    def test_proximity_dedup_outside_threshold(self):
        # 0.005° apart — well outside the 3×3 grid neighbourhood for thresh=0.002
        spots = [
            {"lat": 25.000, "lng": -80.000, "type": "jetty", "name": ""},
            {"lat": 25.005, "lng": -80.005, "type": "jetty", "name": ""},
        ]
        assert len(_deduplicate(spots)) == 2

    def test_proximity_threshold_wider_for_habitats(self):
        # Habitat polygon types (grass_flat etc.) use thresh=0.0 — proximity
        # dedup is skipped so adjacent patches are never collapsed.
        spots = [
            {"lat": 25.000, "lng": -80.000, "type": "grass_flat", "name": ""},
            {"lat": 25.001, "lng": -80.001, "type": "grass_flat", "name": ""},
        ]
        assert len(_deduplicate(spots)) == 2

    def test_different_types_not_proximity_deduped(self):
        # Same location, different types → both kept
        spots = [
            {"lat": 25.0, "lng": -80.0, "type": "pier", "name": ""},
            {"lat": 25.0, "lng": -80.0, "type": "jetty", "name": ""},
        ]
        assert len(_deduplicate(spots)) == 2

    def test_anonymous_spots_deduplicated_by_proximity_only(self):
        # Four pier markers all within 0.001° of each other → one remains
        spots = [
            {"lat": 25.000, "lng": -80.000, "type": "pier", "name": ""},
            {"lat": 25.001, "lng": -80.000, "type": "pier", "name": ""},
            {"lat": 25.000, "lng": -80.001, "type": "pier", "name": ""},
            {"lat": 25.001, "lng": -80.001, "type": "pier", "name": ""},
        ]
        assert len(_deduplicate(spots)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# fetch_osm_structures (Overpass integration — mocked network)
# ─────────────────────────────────────────────────────────────────────────────


def _make_overpass_response(elements: List[Dict[str, Any]]) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {"elements": elements}
    return mock


class TestFetchOsmStructures:
    def test_returns_classified_spots_within_bbox(self):
        el = {
            "type": "node",
            "id": 1,
            "lat": 25.1,
            "lon": -80.2,
            "tags": {"man_made": "pier", "name": "City Pier"},
        }
        with patch.object(
            _fs_mod._HTTP, "post", return_value=_make_overpass_response([el])
        ):
            spots = fetch_osm_structures(25.0, -80.5, 25.5, -80.0, {"pier"})
        assert len(spots) == 1
        assert spots[0] == {
            "lat": 25.1,
            "lng": -80.2,
            "type": "pier",
            "name": "City Pier",
        }

    def test_way_center_coordinates_used(self):
        el = {
            "type": "way",
            "id": 2,
            "center": {"lat": 25.2, "lon": -80.3},
            "tags": {"natural": "beach"},
        }
        with patch.object(
            _fs_mod._HTTP, "post", return_value=_make_overpass_response([el])
        ):
            spots = fetch_osm_structures(25.0, -80.5, 25.5, -80.0, {"beach"})
        assert len(spots) == 1
        assert spots[0]["lat"] == 25.2 and spots[0]["lng"] == -80.3

    def test_element_outside_bbox_filtered_out(self):
        el = {
            "type": "node",
            "id": 3,
            "lat": 30.0,
            "lon": -80.2,  # outside bbox
            "tags": {"man_made": "pier"},
        }
        with patch.object(
            _fs_mod._HTTP, "post", return_value=_make_overpass_response([el])
        ):
            spots = fetch_osm_structures(25.0, -80.5, 25.5, -80.0, {"pier"})
        assert spots == []

    def test_element_wrong_type_filtered_out(self):
        # Element classifies as "beach" but only "pier" was requested
        el = {
            "type": "way",
            "id": 4,
            "center": {"lat": 25.1, "lon": -80.2},
            "tags": {"natural": "beach"},
        }
        with patch.object(
            _fs_mod._HTTP, "post", return_value=_make_overpass_response([el])
        ):
            spots = fetch_osm_structures(25.0, -80.5, 25.5, -80.0, {"pier"})
        assert spots == []

    def test_element_missing_coords_skipped(self):
        el = {"type": "way", "id": 5, "tags": {"man_made": "pier"}}
        with patch.object(
            _fs_mod._HTTP, "post", return_value=_make_overpass_response([el])
        ):
            spots = fetch_osm_structures(25.0, -80.5, 25.5, -80.0, {"pier"})
        assert spots == []

    def test_seamark_name_used_when_no_name_tag(self):
        el = {
            "type": "node",
            "id": 6,
            "lat": 25.1,
            "lon": -80.2,
            "tags": {"man_made": "buoy", "seamark:name": "Buoy 12A"},
        }
        with patch.object(
            _fs_mod._HTTP, "post", return_value=_make_overpass_response([el])
        ):
            spots = fetch_osm_structures(25.0, -80.5, 25.5, -80.0, {"buoy"})
        assert spots[0]["name"] == "Buoy 12A"

    def test_fallback_to_mirror_on_primary_failure(self):
        el = {
            "type": "node",
            "id": 7,
            "lat": 25.1,
            "lon": -80.2,
            "tags": {"natural": "reef"},
        }
        good_response = _make_overpass_response([el])

        call_count = {"n": 0}

        def side_effect(url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("primary down")
            return good_response

        with patch.object(_fs_mod._HTTP, "post", side_effect=side_effect):
            spots = fetch_osm_structures(25.0, -80.5, 25.5, -80.0, {"reef"})
        assert len(spots) == 1
        assert call_count["n"] == 2  # primary failed, mirror succeeded

    def test_empty_types_returns_empty_list_without_network_call(self):
        with patch("requests.post") as mock_post:
            spots = fetch_osm_structures(25.0, -80.5, 25.5, -80.0, set())
        mock_post.assert_not_called()
        assert spots == []


# ─────────────────────────────────────────────────────────────────────────────
# fetch_noaa_structures (NOAA ENC integration — mocked network)
# ─────────────────────────────────────────────────────────────────────────────


def _make_noaa_response(features: List[Dict[str, Any]]) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {"features": features}
    return mock


class TestFetchNoaaStructures:
    def test_wreck_type_queries_wreck_layer(self):
        feat = {
            "geometry": {"x": -80.2, "y": 25.1},
            "attributes": {"OBJNAM": "SS Tarpon", "INFORM": ""},
        }
        with patch.object(
            _fs_mod._HTTP, "get", return_value=_make_noaa_response([feat])
        ) as mock_get:
            spots = fetch_noaa_structures(25.0, -80.5, 25.5, -80.0, {"wreck"})
        assert len(spots) == 1
        assert spots[0]["type"] == "wreck"
        assert spots[0]["name"] == "SS Tarpon"
        # Layer 2 (wrecks) must appear in the URL
        url_called = mock_get.call_args[0][0]
        assert "/2/" in url_called

    def test_shoal_type_queries_obstruction_and_rock_layers(self):
        feat = {
            "geometry": {"x": -80.1, "y": 25.2},
            "attributes": {"OBJNAM": "Ledge Rock", "INFORM": ""},
        }
        responses = [_make_noaa_response([feat]), _make_noaa_response([])]
        with patch.object(_fs_mod._HTTP, "get", side_effect=responses) as mock_get:
            spots = fetch_noaa_structures(25.0, -80.5, 25.5, -80.0, {"shoal"})
        assert len(spots) == 1
        # Two GET calls: obstruction layer (3) then rock layer (4)
        urls = [call[0][0] for call in mock_get.call_args_list]
        assert any("/3/" in u for u in urls)
        assert any("/4/" in u for u in urls)

    def test_polygon_centroid_computed_for_ring_geometry(self):
        # A square ring around (25.1, -80.2)
        feat = {
            "geometry": {
                "rings": [
                    [
                        [-80.3, 25.0],
                        [-80.1, 25.0],
                        [-80.1, 25.2],
                        [-80.3, 25.2],
                        [-80.3, 25.0],
                    ]
                ]
            },
            "attributes": {"OBJNAM": "Rock Pile"},
        }
        with patch.object(
            _fs_mod._HTTP, "get", return_value=_make_noaa_response([feat])
        ):
            spots = fetch_noaa_structures(25.0, -80.5, 25.5, -80.0, {"wreck"})
        assert len(spots) == 1
        assert abs(spots[0]["lat"] - 25.08) < 0.02  # centroid ≈ (25.08, -80.2)
        assert abs(spots[0]["lng"] - (-80.2)) < 0.02

    def test_noaa_failure_returns_empty_list(self):
        with patch("requests.get", side_effect=ConnectionError("NOAA down")):
            spots = fetch_noaa_structures(25.0, -80.5, 25.5, -80.0, {"wreck"})
        assert spots == []

    def test_irrelevant_types_skip_noaa_call(self):
        with patch("requests.get") as mock_get:
            fetch_noaa_structures(25.0, -80.5, 25.5, -80.0, {"pier", "jetty"})
        mock_get.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# find_fish_structures (integration — mocked sub-calls)
# ─────────────────────────────────────────────────────────────────────────────


class TestFindFishStructures:
    def test_returns_combined_osm_and_noaa_with_tips(self):
        osm_spots = [{"lat": 25.1, "lng": -80.2, "type": "pier", "name": "City Pier"}]
        noaa_spots = [{"lat": 25.3, "lng": -80.4, "type": "wreck", "name": "SS Tarpon"}]

        with (
            patch(
                "services.fish_structures.fetch_osm_structures", return_value=osm_spots
            ),
            patch(
                "services.fish_structures.fetch_noaa_structures",
                return_value=noaa_spots,
            ),
        ):
            result = find_fish_structures(25.0, -80.5, 25.5, -80.0)

        assert len(result) == 2
        types_returned = {s["type"] for s in result}
        assert types_returned == {"pier", "wreck"}

    def test_deduplication_applied_across_sources(self):
        # Same wreck reported by both OSM and NOAA at effectively the same coords
        osm_spots = [
            {"lat": 25.1, "lng": -80.2, "type": "wreck", "name": "Urca de Lima"}
        ]
        noaa_spots = [
            {"lat": 25.1, "lng": -80.2, "type": "wreck", "name": "Urca de Lima"}
        ]

        with (
            patch(
                "services.fish_structures.fetch_osm_structures", return_value=osm_spots
            ),
            patch(
                "services.fish_structures.fetch_noaa_structures",
                return_value=noaa_spots,
            ),
        ):
            result = find_fish_structures(25.0, -80.5, 25.5, -80.0)

        assert len(result) == 1
        assert result[0]["name"] == "Urca de Lima"

    def test_type_filter_applied(self):
        osm_spots = [
            {"lat": 25.1, "lng": -80.2, "type": "pier", "name": ""},
            {"lat": 25.2, "lng": -80.3, "type": "wreck", "name": ""},
        ]
        with (
            patch(
                "services.fish_structures.fetch_osm_structures", return_value=osm_spots
            ),
            patch("services.fish_structures.fetch_noaa_structures", return_value=[]),
        ):
            result = find_fish_structures(25.0, -80.5, 25.5, -80.0, {"pier"})

        assert all(s["type"] == "pier" for s in result)

    def test_unrecognised_types_silently_ignored(self):
        with (
            patch("services.fish_structures.fetch_osm_structures", return_value=[]),
            patch("services.fish_structures.fetch_noaa_structures", return_value=[]),
        ):
            result = find_fish_structures(25.0, -80.5, 25.5, -80.0, {"not_a_type"})
        assert result == []

    def test_none_types_uses_all_valid_types(self):
        with (
            patch(
                "services.fish_structures.fetch_osm_structures", return_value=[]
            ) as m_osm,
            patch("services.fish_structures.fetch_noaa_structures", return_value=[]),
        ):
            find_fish_structures(25.0, -80.5, 25.5, -80.0, None)

        called_types = m_osm.call_args[0][4]  # fifth positional arg is 'types'
        assert called_types == set(VALID_TYPES)

    def test_tip_attached_for_every_valid_type(self):
        """Every spot type in VALID_TYPES must have a STRUCTURE_TIPS entry and produce a result."""
        for spot_type in VALID_TYPES:
            osm_spots = [{"lat": 25.1, "lng": -80.2, "type": spot_type, "name": ""}]
            with (
                patch(
                    "services.fish_structures.fetch_osm_structures",
                    return_value=osm_spots,
                ),
                patch(
                    "services.fish_structures.fetch_noaa_structures", return_value=[]
                ),
            ):
                result = find_fish_structures(25.0, -80.5, 25.5, -80.0, {spot_type})
            assert len(result) == 1, f"Expected a result for type '{spot_type}'"
            assert STRUCTURE_TIPS.get(spot_type), (
                f"Missing STRUCTURE_TIPS entry for type '{spot_type}'"
            )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/map/structures  (Flask test client)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("storage.sqlite.DB_PATH", str(tmp_path / "test_structures.db"))
    from storage.sqlite import init_db

    init_db()
    from app import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _structures_url(**params) -> str:
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"/api/map/structures?{qs}"


class TestMapStructuresEndpoint:
    def test_happy_path_returns_structures_and_count(self, client):
        spots = [
            {
                "lat": 25.1,
                "lng": -80.2,
                "type": "pier",
                "name": "City Pier",
                "tip": "tip A",
            },
            {
                "lat": 25.3,
                "lng": -80.4,
                "type": "wreck",
                "name": "SS Tarpon",
                "tip": "tip B",
            },
        ]
        with (
            patch("services.fish_structures.fetch_osm_structures", return_value=spots),
            patch("services.fish_structures.fetch_noaa_structures", return_value=[]),
        ):
            r = client.get(
                _structures_url(south=25.0, west=-80.5, north=25.5, east=-80.0)
            )

        assert r.status_code == 200
        data = r.get_json()
        assert data["count"] == 2
        assert len(data["structures"]) == 2

    def test_types_filter_forwarded_to_service(self, client):
        with patch("web.api.find_fish_structures", return_value=[]) as mock_fn:
            r = client.get(
                _structures_url(
                    south=25.0, west=-80.5, north=25.5, east=-80.0, types="pier,jetty"
                )
            )
        assert r.status_code == 200
        called_types = mock_fn.call_args[0][4]
        assert called_types == {"pier", "jetty"}

    def test_missing_bbox_param_returns_400(self, client):
        r = client.get("/api/map/structures?south=25.0&west=-80.5&north=25.5")
        assert r.status_code == 400

    def test_non_float_bbox_returns_400(self, client):
        r = client.get(_structures_url(south="abc", west=-80.5, north=25.5, east=-80.0))
        assert r.status_code == 400

    def test_south_greater_than_north_returns_400(self, client):
        r = client.get(_structures_url(south=26.0, west=-80.5, north=25.0, east=-80.0))
        assert r.status_code == 400

    def test_latitude_out_of_range_returns_400(self, client):
        r = client.get(_structures_url(south=-95.0, west=-80.5, north=25.5, east=-80.0))
        assert r.status_code == 400

    def test_longitude_out_of_range_returns_400(self, client):
        r = client.get(_structures_url(south=25.0, west=-190.0, north=25.5, east=-80.0))
        assert r.status_code == 400

    def test_invalid_types_only_returns_400(self, client):
        r = client.get(
            _structures_url(
                south=25.0, west=-80.5, north=25.5, east=-80.0, types="ghost,spaceship"
            )
        )
        assert r.status_code == 400

    def test_mixed_valid_invalid_types_uses_valid_subset(self, client):
        with patch("web.api.find_fish_structures", return_value=[]) as mock_fn:
            r = client.get(
                _structures_url(
                    south=25.0,
                    west=-80.5,
                    north=25.5,
                    east=-80.0,
                    types="pier,ghost_type",
                )
            )
        assert r.status_code == 200
        called_types = mock_fn.call_args[0][4]
        assert called_types == {"pier"}

    def test_empty_result_returns_200_with_empty_list(self, client):
        with patch("web.api.find_fish_structures", return_value=[]):
            r = client.get(
                _structures_url(south=25.0, west=-80.5, north=25.5, east=-80.0)
            )
        assert r.status_code == 200
        data = r.get_json()
        assert data == {"structures": [], "count": 0}

    def test_response_structure_fields_present(self, client):
        spots = [{"lat": 25.1, "lng": -80.2, "type": "reef", "name": "Reef X"}]
        with patch("web.api.find_fish_structures", return_value=spots):
            r = client.get(
                _structures_url(south=25.0, west=-80.5, north=25.5, east=-80.0)
            )
        s = r.get_json()["structures"][0]
        assert {"lat", "lng", "type", "name"} <= s.keys()

    def test_oversized_lat_span_returns_zoom_required(self, client):
        # 9-degree lat span > _STRUCT_MAX_LAT_SPAN (8); no service call expected
        with patch("services.fish_structures.find_fish_structures") as mock_fn:
            r = client.get(
                _structures_url(south=20.0, west=-80.5, north=29.0, east=-80.0)
            )
        assert r.status_code == 200
        data = r.get_json()
        assert data["zoom_required"] is True
        assert data["structures"] == []
        mock_fn.assert_not_called()

    def test_oversized_lng_span_returns_zoom_required(self, client):
        # 13-degree lng span > _STRUCT_MAX_LNG_SPAN (12)
        with patch("services.fish_structures.find_fish_structures") as mock_fn:
            r = client.get(
                _structures_url(south=25.0, west=-93.0, north=25.5, east=-80.0)
            )
        assert r.status_code == 200
        assert r.get_json()["zoom_required"] is True
        mock_fn.assert_not_called()

    def test_valid_viewport_does_not_set_zoom_required(self, client):
        with patch("services.fish_structures.find_fish_structures", return_value=[]):
            r = client.get(
                _structures_url(south=25.0, west=-80.5, north=25.5, east=-80.0)
            )
        data = r.get_json()
        assert "zoom_required" not in data


# ─────────────────────────────────────────────────────────────────────────────
# find_fish_structures — result cache
# ─────────────────────────────────────────────────────────────────────────────

_BBOX = (25.0, -80.5, 25.5, -80.0)
_SPOT = {"lat": 25.1, "lng": -80.2, "type": "pier", "name": "City Pier"}


class TestFindFishStructuresCache:
    def _call(self, types=None):
        return find_fish_structures(*_BBOX, types)

    def test_cache_hit_skips_network_on_second_call(self):
        with (
            patch(
                "services.fish_structures.fetch_osm_structures", return_value=[_SPOT]
            ) as m_osm,
            patch("services.fish_structures.fetch_noaa_structures", return_value=[]),
        ):
            first = self._call({"pier"})
            second = self._call({"pier"})

        assert first == second
        # fetch functions called only once despite two find_fish_structures calls
        assert m_osm.call_count == 1

    def test_different_types_are_cached_independently(self):
        pier_spot = {**_SPOT, "type": "pier"}
        wreck_spot = {**_SPOT, "type": "wreck", "name": "SS Tarpon"}

        def osm_side_effect(s, w, n, e, types):
            if "pier" in types:
                return [pier_spot]
            if "wreck" in types:
                return [wreck_spot]
            return []

        with (
            patch(
                "services.fish_structures.fetch_osm_structures",
                side_effect=osm_side_effect,
            ),
            patch("services.fish_structures.fetch_noaa_structures", return_value=[]),
        ):
            piers = self._call({"pier"})
            wrecks = self._call({"wreck"})
            # Re-fetch from cache — must not mix results
            piers2 = self._call({"pier"})

        assert all(s["type"] == "pier" for s in piers)
        assert all(s["type"] == "wreck" for s in wrecks)
        assert piers == piers2

    def test_cache_miss_after_ttl_expired(self):
        with (
            patch(
                "services.fish_structures.fetch_osm_structures", return_value=[_SPOT]
            ),
            patch("services.fish_structures.fetch_noaa_structures", return_value=[]),
        ):
            self._call({"pier"})

        # Expire the cache entry by backdating its timestamp
        for entry in _fs_mod._CACHE.values():
            entry["ts"] -= _fs_mod._CACHE_TTL + 1

        with (
            patch(
                "services.fish_structures.fetch_osm_structures", return_value=[_SPOT]
            ) as m_osm2,
            patch("services.fish_structures.fetch_noaa_structures", return_value=[]),
        ):
            self._call({"pier"})

        assert m_osm2.call_count == 1  # network hit again after TTL

    def test_cache_eviction_at_max_capacity(self):
        """Adding more than _CACHE_MAX entries should not raise and should cap size."""
        original_max = _fs_mod._CACHE_MAX
        _fs_mod._CACHE_MAX = 4
        try:
            with (
                patch("services.fish_structures.fetch_osm_structures", return_value=[]),
                patch(
                    "services.fish_structures.fetch_noaa_structures", return_value=[]
                ),
            ):
                # Each call uses a unique bbox so each gets its own cache key
                for i in range(6):
                    find_fish_structures(
                        float(i), -80.5, float(i) + 0.5, -80.0, {"pier"}
                    )
        finally:
            _fs_mod._CACHE_MAX = original_max

        assert len(_fs_mod._CACHE) <= 4

    def test_cache_clear_removes_all_entries(self):
        with (
            patch(
                "services.fish_structures.fetch_osm_structures", return_value=[_SPOT]
            ),
            patch("services.fish_structures.fetch_noaa_structures", return_value=[]),
        ):
            self._call({"pier"})

        assert len(_fs_mod._CACHE) >= 1
        cache_clear()
        assert len(_fs_mod._CACHE) == 0
