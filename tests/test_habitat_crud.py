"""Tests for storage/sqlite.py custom-habitat, habitat-override, and
custom-habitat-type CRUD functions (admin map-drawing features) — previously
untested.
"""

from __future__ import annotations

import pytest

from storage.sqlite import (
    create_custom_habitat,
    create_custom_habitat_type,
    create_user,
    delete_custom_habitat,
    delete_custom_habitat_type,
    delete_habitat_override,
    get_all_custom_habitats,
    get_custom_habitat_types,
    get_custom_habitats_in_bbox,
    get_habitat_overrides,
    init_db,
    undelete_custom_habitat,
    update_custom_habitat,
    upsert_habitat_override,
)
import storage.sqlite as sqlite_mod


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    init_db()
    # Reset module-level habitat caches so they don't leak from a previous
    # test's (different) tmp_path database.
    monkeypatch.setattr(sqlite_mod, "_CUSTOM_HABITATS_CACHE", None)
    monkeypatch.setattr(sqlite_mod, "_CUSTOM_HABITATS_TS", 0.0)
    monkeypatch.setattr(sqlite_mod, "_HABITAT_OVERRIDES_CACHE", None)
    monkeypatch.setattr(sqlite_mod, "_HABITAT_OVERRIDES_TS", 0.0)
    monkeypatch.setattr(sqlite_mod, "_CUSTOM_HABITAT_TYPES_CACHE", None)
    monkeypatch.setattr(sqlite_mod, "_CUSTOM_HABITAT_TYPES_TS", 0.0)
    return tmp_path


@pytest.fixture
def user_id():
    return create_user("habitat_admin", "Aa123456")


_GEOM = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}


class TestCreateAndGetCustomHabitat:
    def test_create_returns_populated_dict(self, user_id):
        out = create_custom_habitat(
            "hab-1", "reef", "Test Reef", "A nice reef", "#336699", _GEOM, 34.2, -77.8, user_id
        )
        assert out["id"] == "hab-1"
        assert out["habitat_type"] == "reef"
        assert out["name"] == "Test Reef"
        assert out["geometry"] == _GEOM
        assert out["fill_opacity"] == 0.35
        assert out["stroke_weight"] == 2.5
        assert out["custom"] is True

    def test_create_strips_whitespace(self, user_id):
        out = create_custom_habitat(
            "hab-2", "surf", "  Spaced Name  ", "  desc  ", "  #fff  ", _GEOM, 1.0, 2.0, user_id
        )
        assert out["name"] == "Spaced Name"
        assert out["description"] == "desc"
        assert out["fill_color"] == "#fff"

    def test_get_all_includes_created_habitat(self, user_id):
        create_custom_habitat("hab-3", "kelp", "Kelp Bed", "", "#0a0", _GEOM, 1.0, 1.0, user_id)
        all_habitats = get_all_custom_habitats()
        ids = [h["id"] for h in all_habitats]
        assert "hab-3" in ids

    def test_get_all_uses_cache_on_second_call(self, user_id, monkeypatch):
        create_custom_habitat("hab-4", "reef", "Reef", "", "#000", _GEOM, 1.0, 1.0, user_id)
        first = get_all_custom_habitats()
        # Sabotage get_db so a second DB hit would raise; cache should avoid it.
        monkeypatch.setattr(sqlite_mod, "get_db", lambda: (_ for _ in ()).throw(AssertionError("hit db")))
        second = get_all_custom_habitats()
        assert second == first


class TestCustomHabitatsInBbox:
    def test_filters_by_bbox(self, user_id):
        create_custom_habitat("in-box", "reef", "In", "", "#000", _GEOM, 10.0, 10.0, user_id)
        create_custom_habitat("out-box", "reef", "Out", "", "#000", _GEOM, 50.0, 50.0, user_id)
        result = get_custom_habitats_in_bbox(0.0, 0.0, 20.0, 20.0)
        ids = [h["id"] for h in result]
        assert "in-box" in ids
        assert "out-box" not in ids

    def test_includes_habitats_with_no_coordinates(self, user_id):
        create_custom_habitat("no-coords", "reef", "NoCoords", "", "#000", _GEOM, None, None, user_id)
        result = get_custom_habitats_in_bbox(0.0, 0.0, 1.0, 1.0)
        ids = [h["id"] for h in result]
        assert "no-coords" in ids


class TestUpdateCustomHabitat:
    def test_updates_provided_fields_only(self, user_id):
        create_custom_habitat("hab-5", "reef", "Old Name", "Old desc", "#111", _GEOM, 1.0, 1.0, user_id)
        updated = update_custom_habitat("hab-5", name="New Name")
        assert updated["name"] == "New Name"
        assert updated["description"] == "Old desc"
        assert updated["fill_color"] == "#111"

    def test_updates_geometry(self, user_id):
        create_custom_habitat("hab-6", "reef", "X", "", "#111", _GEOM, 1.0, 1.0, user_id)
        new_geom = {"type": "Point", "coordinates": [5, 5]}
        updated = update_custom_habitat("hab-6", geometry=new_geom)
        assert updated["geometry"] == new_geom

    def test_returns_none_for_unknown_id(self):
        assert update_custom_habitat("nonexistent", name="X") is None

    def test_returns_none_for_deleted_habitat(self, user_id):
        create_custom_habitat("hab-7", "reef", "X", "", "#111", _GEOM, 1.0, 1.0, user_id)
        delete_custom_habitat("hab-7")
        assert update_custom_habitat("hab-7", name="Y") is None


class TestDeleteAndUndeleteCustomHabitat:
    def test_delete_returns_true_and_hides_from_listing(self, user_id):
        create_custom_habitat("hab-8", "reef", "X", "", "#111", _GEOM, 1.0, 1.0, user_id)
        assert delete_custom_habitat("hab-8") is True
        ids = [h["id"] for h in get_all_custom_habitats()]
        assert "hab-8" not in ids

    def test_delete_unknown_id_returns_false(self):
        assert delete_custom_habitat("nope") is False

    def test_delete_already_deleted_returns_false(self, user_id):
        create_custom_habitat("hab-9", "reef", "X", "", "#111", _GEOM, 1.0, 1.0, user_id)
        delete_custom_habitat("hab-9")
        assert delete_custom_habitat("hab-9") is False

    def test_undelete_restores_habitat(self, user_id):
        create_custom_habitat("hab-10", "reef", "X", "", "#111", _GEOM, 1.0, 1.0, user_id)
        delete_custom_habitat("hab-10")
        restored = undelete_custom_habitat("hab-10")
        assert restored is not None
        assert restored["id"] == "hab-10"
        ids = [h["id"] for h in get_all_custom_habitats()]
        assert "hab-10" in ids

    def test_undelete_non_deleted_returns_none(self, user_id):
        create_custom_habitat("hab-11", "reef", "X", "", "#111", _GEOM, 1.0, 1.0, user_id)
        assert undelete_custom_habitat("hab-11") is None

    def test_undelete_unknown_id_returns_none(self):
        assert undelete_custom_habitat("nope") is None


class TestHabitatOverrides:
    def test_upsert_creates_new_override(self, user_id):
        out = upsert_habitat_override(
            "feature-1", "Custom Name", "Custom desc", "#abcdef", user_id
        )
        assert out["feature_key"] == "feature-1"
        assert out["name"] == "Custom Name"
        assert out["fill_color"] == "#abcdef"

    def test_upsert_updates_existing_override(self, user_id):
        upsert_habitat_override("feature-2", "Name1", "Desc1", "#111", user_id)
        out = upsert_habitat_override("feature-2", "Name2", None, None, user_id)
        assert out["name"] == "Name2"
        # Untouched fields retain prior values.
        assert out["description"] == "Desc1"
        assert out["fill_color"] == "#111"

    def test_geometry_clear_sets_null(self, user_id):
        upsert_habitat_override(
            "feature-3", "Name", "Desc", "#111", user_id, geometry='{"type":"Point"}'
        )
        out = upsert_habitat_override(
            "feature-3", None, None, None, user_id, geometry_clear=True
        )
        assert out["geometry_json"] is None

    def test_get_habitat_overrides_keyed_by_feature(self, user_id):
        upsert_habitat_override("feature-4", "Name", "Desc", "#111", user_id)
        overrides = get_habitat_overrides()
        assert "feature-4" in overrides
        assert overrides["feature-4"]["name"] == "Name"

    def test_get_habitat_overrides_uses_cache(self, user_id, monkeypatch):
        upsert_habitat_override("feature-5", "Name", "Desc", "#111", user_id)
        first = get_habitat_overrides()
        monkeypatch.setattr(sqlite_mod, "get_db", lambda: (_ for _ in ()).throw(AssertionError("hit db")))
        second = get_habitat_overrides()
        assert second == first

    def test_delete_habitat_override(self, user_id):
        out = upsert_habitat_override("feature-6", "Name", "Desc", "#111", user_id)
        assert delete_habitat_override(out["id"]) is True
        assert "feature-6" not in get_habitat_overrides()

    def test_delete_unknown_override_returns_false(self):
        assert delete_habitat_override(999999) is False

    def test_undelete_via_upsert_after_delete(self, user_id):
        # Deleting then upserting again should resurrect (is_deleted reset to 0).
        out = upsert_habitat_override("feature-7", "Name", "Desc", "#111", user_id)
        delete_habitat_override(out["id"])
        assert "feature-7" not in get_habitat_overrides()
        upsert_habitat_override("feature-7", "NewName", None, None, user_id)
        assert "feature-7" in get_habitat_overrides()


class TestCustomHabitatTypes:
    def test_create_and_list(self, user_id):
        out = create_custom_habitat_type("Oyster Bar", "oyster-bar", "#998877", user_id)
        assert out["name"] == "Oyster Bar"
        assert out["slug"] == "oyster-bar"
        types = get_custom_habitat_types()
        slugs = [t["slug"] for t in types]
        assert "oyster-bar" in slugs

    def test_create_strips_whitespace(self, user_id):
        out = create_custom_habitat_type("  Spaced  ", "  spaced  ", "  #000  ", user_id)
        assert out["name"] == "Spaced"
        assert out["slug"] == "spaced"
        assert out["default_color"] == "#000"

    def test_list_is_sorted_by_name(self, user_id):
        create_custom_habitat_type("Zebra Mussel Bed", "zebra", "#000", user_id)
        create_custom_habitat_type("Artificial Reef", "artificial", "#000", user_id)
        names = [t["name"] for t in get_custom_habitat_types()]
        assert names.index("Artificial Reef") < names.index("Zebra Mussel Bed")

    def test_delete_hides_from_listing(self, user_id):
        out = create_custom_habitat_type("Temp Type", "temp-type", "#000", user_id)
        assert delete_custom_habitat_type(out["id"]) is True
        slugs = [t["slug"] for t in get_custom_habitat_types()]
        assert "temp-type" not in slugs

    def test_delete_unknown_id_returns_false(self):
        assert delete_custom_habitat_type(999999) is False

    def test_delete_already_deleted_returns_false(self, user_id):
        out = create_custom_habitat_type("Temp2", "temp2", "#000", user_id)
        delete_custom_habitat_type(out["id"])
        assert delete_custom_habitat_type(out["id"]) is False

    def test_get_types_uses_cache(self, user_id, monkeypatch):
        create_custom_habitat_type("Cached Type", "cached-type", "#000", user_id)
        first = get_custom_habitat_types()
        monkeypatch.setattr(sqlite_mod, "get_db", lambda: (_ for _ in ()).throw(AssertionError("hit db")))
        second = get_custom_habitat_types()
        assert second == first
