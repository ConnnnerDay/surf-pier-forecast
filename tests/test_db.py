"""Tests for storage.db module -- forecast cache and catch log stats."""

import pytest

from storage.db import (
    add_log_entry,
    create_user,
    delete_forecast,
    get_log_stats,
    init_db,
    list_cached_locations,
    load_forecast,
    save_forecast_to_db,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the DB to a temp file and initialize schema."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("storage.db.DB_PATH", db_path)
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

    def test_upsert_replaces(self):
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
