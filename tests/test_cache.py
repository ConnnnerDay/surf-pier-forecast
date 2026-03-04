"""Tests for storage.cache module."""

import json
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from zoneinfo import ZoneInfo

from storage.cache import (
    CACHE_MAX_AGE_HOURS,
    _cache_path,
    _forecast_age_minutes,
    _human_age,
    load_cached_forecast,
    save_forecast,
)


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Redirect both JSON cache dir and SQLite DB to temp folder."""
    monkeypatch.setattr("storage.cache.CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("storage.cache.CACHE_FILE", str(tmp_path / "forecast.json"))
    # Point the DB to a temp file so tests don't touch the real database
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    # Initialize the schema in the temp DB
    from storage.sqlite import init_db
    init_db()
    return tmp_path


class TestCachePath:
    def test_default_path(self, isolated_storage):
        assert _cache_path("") == str(isolated_storage / "forecast.json")

    def test_location_specific_path(self, isolated_storage):
        assert _cache_path("wrightsville-beach-nc").endswith(
            "forecast_wrightsville-beach-nc.json"
        )


class TestSaveAndLoad:
    def test_roundtrip_via_db(self):
        """Save and load should work through SQLite for location-specific forecasts."""
        data = {"generated_at": "2026-03-01T12:00:00", "location": "test", "temp": 72}
        save_forecast(data, "loc1")
        loaded = load_cached_forecast("loc1")
        assert loaded == data

    def test_roundtrip_no_location_uses_json(self, isolated_storage):
        """Without a location_id, falls back to JSON only."""
        data = {"generated_at": "2026-03-01T12:00:00", "temp": 65}
        save_forecast(data, "")
        loaded = load_cached_forecast("")
        assert loaded == data

    def test_load_missing_returns_none(self):
        assert load_cached_forecast("nonexistent") is None

    def test_json_fallback_migration(self, isolated_storage):
        """Legacy JSON file should be migrated to DB on first read."""
        data = {"generated_at": "2026-02-01T12:00:00", "species": ["drum"]}
        # Write directly to JSON (simulating legacy file)
        path = isolated_storage / "forecast_legacy-loc.json"
        path.write_text(json.dumps(data))

        loaded = load_cached_forecast("legacy-loc")
        assert loaded == data

        # Second load should come from DB (even if we delete the JSON)
        path.unlink()
        loaded2 = load_cached_forecast("legacy-loc")
        assert loaded2 == data

    def test_json_fallback_written_if_db_fails(self, isolated_storage, monkeypatch):
        """If DB write fails, JSON fallback is written."""
        data = {"generated_at": "2026-03-01T12:00:00"}

        def _boom(*_args, **_kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr("storage.sqlite.save_forecast_cache", _boom)
        save_forecast(data, "backup-test")
        json_path = isolated_storage / "forecast_backup-test.json"
        assert json_path.exists()
        with open(json_path) as f:
            assert json.load(f) == data


    def test_cache_is_scoped_by_user_and_location(self):
        data_u1 = {"generated_at": "2026-03-01T12:00:00", "owner": 1}
        data_u2 = {"generated_at": "2026-03-01T12:00:00", "owner": 2}
        save_forecast(data_u1, "loc1", user_id=1)
        save_forecast(data_u2, "loc1", user_id=2)

        assert load_cached_forecast("loc1", user_id=1)["owner"] == 1
        assert load_cached_forecast("loc1", user_id=2)["owner"] == 2

    def test_stale_cache_returns_none(self):
        old = datetime.now(ZoneInfo("America/New_York")) - timedelta(hours=CACHE_MAX_AGE_HOURS + 2)
        save_forecast({"generated_at": old.isoformat()}, "stale-loc", user_id=9)
        assert load_cached_forecast("stale-loc", user_id=9) is None

    def test_stale_cache_can_be_loaded_for_async_refresh(self):
        old = datetime.now(ZoneInfo("America/New_York")) - timedelta(hours=CACHE_MAX_AGE_HOURS + 2)
        data = {"generated_at": old.isoformat(), "verdict": "stale"}
        save_forecast(data, "stale-loc-include", user_id=9)
        loaded = load_cached_forecast("stale-loc-include", user_id=9, include_stale=True)
        assert loaded == data


class TestForecastAge:
    def test_valid_age(self):
        now = datetime.now(ZoneInfo("America/New_York"))
        thirty_min_ago = now - timedelta(minutes=30)
        forecast = {"generated_at": thirty_min_ago.isoformat()}
        age = _forecast_age_minutes(forecast)
        assert age is not None
        assert 29 <= age <= 31

    def test_missing_field_returns_none(self):
        assert _forecast_age_minutes({}) is None

    def test_bad_format_returns_none(self):
        assert _forecast_age_minutes({"generated_at": "not-a-date"}) is None


class TestHumanAge:
    def test_none_returns_empty(self):
        assert _human_age(None) == ""

    def test_just_now(self):
        assert _human_age(0.5) == "just now"

    def test_minutes(self):
        assert _human_age(15) == "15 min ago"

    def test_one_hour(self):
        assert _human_age(60) == "1 hr ago"

    def test_multiple_hours(self):
        assert _human_age(180) == "3 hrs ago"

    def test_one_day(self):
        assert _human_age(1440) == "1 day ago"

    def test_multiple_days(self):
        assert _human_age(4320) == "3 days ago"