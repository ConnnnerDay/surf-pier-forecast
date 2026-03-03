"""Tests for storage.db module -- forecast cache and catch log stats."""

import pytest

from storage.sqlite import (
    add_log_entry,
    attach_photos_to_entry,
    create_user,
    delete_forecast,
    delete_log_entry,
    get_entry_photo_paths,
    get_log_entries,
    get_log_stats,
    get_recent_logs,
    init_db,
    list_cached_locations,
    load_forecast,
    save_forecast_to_db,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the DB to a temp file and initialize schema."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    init_db()
    return tmp_path


# ---------------------------------------------------------------------------
# Forecast cache in SQLite
# ---------------------------------------------------------------------------

class TestForecastDB:
    def test_save_and_load(self):
        data = {"generated_at": "2026-03-01T12:00:00", "temp": 72}
        save_forecast_to_db("test-loc", data)
        loaded = load_forecast("test-loc")
        assert loaded == data

    def test_load_missing_returns_none(self):
        assert load_forecast("nonexistent") is None

    def test_load_empty_id_returns_none(self):
        assert load_forecast("") is None

    def test_latest_by_timestamp(self):
        save_forecast_to_db("loc1", {"generated_at": "2026-01-01T00:00:00", "v": 1})
        save_forecast_to_db("loc1", {"generated_at": "2026-02-01T00:00:00", "v": 2})
        loaded = load_forecast("loc1")
        assert loaded["v"] == 2
        assert loaded["generated_at"] == "2026-02-01T00:00:00"

    def test_list_cached_locations(self):
        save_forecast_to_db("loc-a", {"generated_at": "2026-01-01T00:00:00"})
        save_forecast_to_db("loc-b", {"generated_at": "2026-02-01T00:00:00"})
        locs = list_cached_locations()
        ids = [l["location_id"] for l in locs]
        assert "loc-a" in ids
        assert "loc-b" in ids

    def test_delete_forecast(self):
        save_forecast_to_db("del-me", {"generated_at": "2026-01-01T00:00:00"})
        assert load_forecast("del-me") is not None
        assert delete_forecast("del-me") is True
        assert load_forecast("del-me") is None

    def test_delete_nonexistent_returns_false(self):
        assert delete_forecast("nope") is False

    def test_save_empty_id_is_noop(self):
        """save_forecast_to_db with empty id should not crash."""
        save_forecast_to_db("", {"generated_at": "2026-01-01T00:00:00"})
        assert load_forecast("") is None


# ---------------------------------------------------------------------------
# Enhanced catch log stats
# ---------------------------------------------------------------------------

class TestLogStats:
    def _make_user(self):
        uid = create_user("testuser", "pass1234")
        assert uid is not None
        return uid

    def test_empty_stats(self):
        uid = self._make_user()
        stats = get_log_stats(uid, "loc1")
        assert stats["total"] == 0
        assert stats["unique_species"] == 0
        assert stats["top_species"] is None
        assert stats["species_breakdown"] == []
        assert stats["monthly_counts"] == {}

    def test_stats_with_entries(self):
        uid = self._make_user()
        add_log_entry(uid, "loc1", "Red drum", size="18 in")
        add_log_entry(uid, "loc1", "Red drum", size="22 in")
        add_log_entry(uid, "loc1", "Bluefish")
        stats = get_log_stats(uid, "loc1")
        assert stats["total"] == 3
        assert stats["unique_species"] == 2
        assert stats["top_species"] == "Red drum"
        assert len(stats["species_breakdown"]) == 2
        assert stats["species_breakdown"][0]["species"] == "Red drum"
        assert stats["species_breakdown"][0]["count"] == 2

    def test_stats_scoped_by_location(self):
        uid = self._make_user()
        add_log_entry(uid, "loc1", "Drum")
        add_log_entry(uid, "loc2", "Bluefish")
        stats1 = get_log_stats(uid, "loc1")
        stats2 = get_log_stats(uid, "loc2")
        assert stats1["total"] == 1
        assert stats2["total"] == 1
        assert stats1["top_species"] == "Drum"
        assert stats2["top_species"] == "Bluefish"


# ---------------------------------------------------------------------------
# Photo columns migration + photo DB functions
# ---------------------------------------------------------------------------

class TestPhotoDB:
    def _make_user(self):
        uid = create_user("photouser", "pass5678")
        assert uid is not None
        return uid

    def test_photo_columns_exist_after_init(self):
        """After init_db(), catch_log must have photo1_path and photo2_path columns."""
        import sqlite3
        from storage.sqlite import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(catch_log)").fetchall()]
        conn.close()
        assert "photo1_path" in cols
        assert "photo2_path" in cols

    def test_new_entry_has_null_photos(self):
        uid = self._make_user()
        entry_id = add_log_entry(uid, "loc1", "Pompano")
        paths = get_entry_photo_paths(uid, entry_id)
        assert paths == (None, None)

    def test_get_entry_photo_paths_unknown_entry(self):
        uid = self._make_user()
        assert get_entry_photo_paths(uid, 99999) is None

    def test_attach_photo1(self):
        uid = self._make_user()
        entry_id = add_log_entry(uid, "loc1", "Drum")
        ok = attach_photos_to_entry(uid, entry_id, photo1_path="uploads/1/abc.jpg")
        assert ok is True
        paths = get_entry_photo_paths(uid, entry_id)
        assert paths == ("uploads/1/abc.jpg", None)

    def test_attach_both_photos(self):
        uid = self._make_user()
        entry_id = add_log_entry(uid, "loc1", "Bluefish")
        attach_photos_to_entry(uid, entry_id, photo1_path="uploads/1/p1.jpg", photo2_path="uploads/1/p2.jpg")
        paths = get_entry_photo_paths(uid, entry_id)
        assert paths == ("uploads/1/p1.jpg", "uploads/1/p2.jpg")

    def test_attach_wrong_user_returns_false(self):
        uid = self._make_user()
        entry_id = add_log_entry(uid, "loc1", "Tautog")
        ok = attach_photos_to_entry(uid + 100, entry_id, photo1_path="uploads/99/x.jpg")
        assert ok is False

    def test_attach_no_paths_returns_false(self):
        uid = self._make_user()
        entry_id = add_log_entry(uid, "loc1", "Flounder")
        ok = attach_photos_to_entry(uid, entry_id)
        assert ok is False

    def test_get_log_entries_includes_photo_fields(self):
        uid = self._make_user()
        entry_id = add_log_entry(uid, "loc1", "Redfish")
        attach_photos_to_entry(uid, entry_id, photo1_path="uploads/1/r.jpg")
        entries = get_log_entries(uid, "loc1")
        assert len(entries) == 1
        assert entries[0]["photo1_path"] == "uploads/1/r.jpg"
        assert entries[0]["photo2_path"] is None

    def test_get_recent_logs_includes_photo_fields(self):
        uid = self._make_user()
        entry_id = add_log_entry(uid, "loc1", "Snook")
        attach_photos_to_entry(uid, entry_id, photo1_path="uploads/1/s.png")
        logs = get_recent_logs(uid)
        assert logs[0]["photo1_path"] == "uploads/1/s.png"
        assert logs[0]["photo2_path"] is None
