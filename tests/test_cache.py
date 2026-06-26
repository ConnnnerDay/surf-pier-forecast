"""Tests for storage.cache module."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

import storage.cache
from storage.cache import (
    CACHE_MAX_AGE_HOURS,
    _cache_path,
    _forecast_age_minutes,
    _human_age,
    _is_stale,
    _load_json_fallback,
    _save_json,
    load_cached_forecast,
    prune_old_forecasts,
    save_forecast,
)
from storage.sqlite import save_forecast_cache as sqlite_save_forecast_cache
from storage.sqlite import save_forecast_to_db


def _fresh_ts() -> str:
    """Return a recent timezone-aware ISO timestamp (5 minutes ago in UTC)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()


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
        data = {"generated_at": _fresh_ts(), "location": "test", "temp": 72}
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
        data = {"generated_at": _fresh_ts(), "species": ["drum"]}
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

        monkeypatch.setattr("storage.cache.save_forecast_cache", _boom)
        save_forecast(data, "backup-test")
        json_path = isolated_storage / "forecast_backup-test.json"
        assert json_path.exists()
        with open(json_path) as f:
            assert json.load(f) == data

    def test_cache_is_scoped_by_user_and_location(self):
        data_u1 = {"generated_at": _fresh_ts(), "owner": 1}
        data_u2 = {"generated_at": _fresh_ts(), "owner": 2}
        save_forecast(data_u1, "loc1", user_id=1)
        save_forecast(data_u2, "loc1", user_id=2)

        assert load_cached_forecast("loc1", user_id=1)["owner"] == 1
        assert load_cached_forecast("loc1", user_id=2)["owner"] == 2

    def test_stale_cache_returns_none(self):
        old = datetime.now(ZoneInfo("America/New_York")) - timedelta(
            hours=CACHE_MAX_AGE_HOURS + 2
        )
        save_forecast({"generated_at": old.isoformat()}, "stale-loc", user_id=9)
        assert load_cached_forecast("stale-loc", user_id=9) is None

    def test_pre_midnight_data_is_stale(self):
        """Data from before today's UTC midnight is stale even if < 4 hours old."""
        today_midnight = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # One second before midnight: still yesterday's date in UTC.
        ts = (today_midnight - timedelta(seconds=1)).isoformat()
        save_forecast({"generated_at": ts}, "premidnight-loc", user_id=8)
        assert load_cached_forecast("premidnight-loc", user_id=8) is None

    def test_pre_midnight_data_returned_with_include_stale(self):
        """include_stale=True should still surface pre-midnight data."""
        today_midnight = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        ts = (today_midnight - timedelta(seconds=1)).isoformat()
        data = {"generated_at": ts, "verdict": "yesterday"}
        save_forecast(data, "premidnight-include", user_id=8)
        loaded = load_cached_forecast("premidnight-include", user_id=8, include_stale=True)
        assert loaded == data

    def test_stale_cache_can_be_loaded_for_async_refresh(self):
        old = datetime.now(ZoneInfo("America/New_York")) - timedelta(
            hours=CACHE_MAX_AGE_HOURS + 2
        )
        data = {"generated_at": old.isoformat(), "verdict": "stale"}
        save_forecast(data, "stale-loc-include", user_id=9)
        loaded = load_cached_forecast(
            "stale-loc-include", user_id=9, include_stale=True
        )
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


class TestIsStale:
    def test_missing_generated_at_not_stale(self):
        assert _is_stale({}) is False

    def test_naive_datetime_treated_as_utc(self):
        # A naive timestamp from today (no tzinfo) should NOT be stale.
        ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        assert _is_stale({"generated_at": ts}) is False

    def test_unparseable_date_not_stale(self):
        assert _is_stale({"generated_at": "not-a-date"}) is False

    def test_fresh_today_not_stale(self):
        ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        assert _is_stale({"generated_at": ts}) is False

    def test_over_4h_stale(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=CACHE_MAX_AGE_HOURS + 1)).isoformat()
        assert _is_stale({"generated_at": ts}) is True

    def test_yesterday_stale_regardless_of_age(self):
        today_midnight = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        ts = (today_midnight - timedelta(seconds=1)).isoformat()
        assert _is_stale({"generated_at": ts}) is True


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


# ---------------------------------------------------------------------------
# Lines 74-75: _is_stale exception handler in midnight-staleness check
# ---------------------------------------------------------------------------


class TestIsStaleExceptionPath:
    def test_age_ok_but_unparseable_generated_at_returns_false(self, monkeypatch):
        """Lines 74-75: if _forecast_age_minutes returns a non-None age but
        the subsequent fromisoformat() in the midnight check raises, return False."""
        monkeypatch.setattr("storage.cache._forecast_age_minutes", lambda f: 5.0)
        assert _is_stale({"generated_at": "not-a-date"}) is False


# ---------------------------------------------------------------------------
# Lines 112-113: stale SQLite hit with include_stale=True
# ---------------------------------------------------------------------------


class TestStaleSQLiteHitIncludeStale:
    def test_stale_sqlite_result_returned_when_include_stale(self):
        """Lines 112-113: stale row in forecast_cache + include_stale=True should be
        stored in _MEM_CACHE and returned (bypassing the delete+return-None path)."""
        old_ts = "2020-01-01T00:00:00+00:00"
        data = {"generated_at": old_ts, "tag": "stale-sqlite"}
        # Write directly to SQLite so _MEM_CACHE is NOT populated
        sqlite_save_forecast_cache(7, "stale-sqlite-loc", data)
        storage.cache._MEM_CACHE.pop(("stale-sqlite-loc", 7), None)

        result = load_cached_forecast("stale-sqlite-loc", user_id=7, include_stale=True)
        assert result is not None
        assert result["tag"] == "stale-sqlite"
        # Must now be in _MEM_CACHE
        assert ("stale-sqlite-loc", 7) in storage.cache._MEM_CACHE


# ---------------------------------------------------------------------------
# Lines 117-118: fresh SQLite hit populates _MEM_CACHE
# ---------------------------------------------------------------------------


class TestFreshSQLiteHitPopulatesMemCache:
    def test_fresh_sqlite_result_added_to_mem_cache(self):
        """Lines 117-118: a non-stale SQLite result should be written into _MEM_CACHE."""
        ts = _fresh_ts()
        data = {"generated_at": ts, "tag": "fresh-sqlite"}
        save_forecast(data, "fresh-sqlite-loc", user_id=11)
        # Evict from _MEM_CACHE so the SQLite path is exercised
        storage.cache._MEM_CACHE.pop(("fresh-sqlite-loc", 11), None)

        result = load_cached_forecast("fresh-sqlite-loc", user_id=11)
        assert result is not None
        assert result["tag"] == "fresh-sqlite"
        assert ("fresh-sqlite-loc", 11) in storage.cache._MEM_CACHE


# ---------------------------------------------------------------------------
# Lines 123-125: historical `forecasts` table fallback
# ---------------------------------------------------------------------------


class TestHistoricalForecastsFallback:
    def test_forecasts_table_fallback_when_cache_empty(self):
        """Lines 123-125: when forecast_cache has no entry, load_forecast() (historical
        table) is tried and its result is cached in _MEM_CACHE and returned."""
        ts = _fresh_ts()
        data = {"generated_at": ts, "tag": "historical-table"}
        # Write only to the historical `forecasts` table, not `forecast_cache`
        save_forecast_to_db("hist-fallback-loc", data)
        storage.cache._MEM_CACHE.clear()

        result = load_cached_forecast("hist-fallback-loc")
        assert result is not None
        assert result["tag"] == "historical-table"


# ---------------------------------------------------------------------------
# Line 131: stale JSON fallback returns None (include_stale=False)
# ---------------------------------------------------------------------------


class TestStaleJsonFallbackReturnsNone:
    def test_stale_json_file_returns_none(self, isolated_storage):
        """Line 131: a stale JSON fallback file with include_stale=False returns None."""
        old_ts = "2020-01-01T00:00:00+00:00"
        data = {"generated_at": old_ts, "tag": "stale-json"}
        path = isolated_storage / "forecast_stale-json-loc.json"
        path.write_text(json.dumps(data))
        storage.cache._MEM_CACHE.clear()

        result = load_cached_forecast("stale-json-loc")
        assert result is None


# ---------------------------------------------------------------------------
# Lines 142-143: prune_old_forecasts removes rows and clears _MEM_CACHE
# Lines 145-147: prune_old_forecasts exception is swallowed
# ---------------------------------------------------------------------------


class TestPruneOldForecasts:
    def test_prune_removes_rows_and_clears_mem_cache(self):
        """Lines 142-143: removing stale rows logs and clears _MEM_CACHE."""
        old_ts = "2020-01-01T00:00:00"
        sqlite_save_forecast_cache(0, "prune-old-loc", {"generated_at": old_ts, "x": 1})
        storage.cache._MEM_CACHE[("prune-old-loc", 0)] = {"tag": "should-be-evicted"}

        removed = prune_old_forecasts(max_age_days=7)

        assert removed >= 1
        assert storage.cache._MEM_CACHE == {}

    def test_prune_exception_returns_zero(self, monkeypatch):
        """Lines 145-147: if prune_forecast_cache raises, prune_old_forecasts returns 0."""

        def _boom(*_a, **_kw):
            raise RuntimeError("db error")

        monkeypatch.setattr("storage.cache.prune_forecast_cache", _boom)
        assert prune_old_forecasts() == 0


# ---------------------------------------------------------------------------
# Line 153: _mem_cache_set evicts oldest entry when cache is full
# ---------------------------------------------------------------------------


class TestMemCacheSetEviction:
    def test_evicts_oldest_when_full(self, monkeypatch):
        """Line 153: when _MEM_CACHE is at capacity, the oldest entry is popped."""
        monkeypatch.setattr("storage.cache._MEM_CACHE_MAX", 1)
        storage.cache._MEM_CACHE.clear()

        ts = _fresh_ts()
        save_forecast({"generated_at": ts, "tag": "first"}, "evict-loc-a")
        save_forecast({"generated_at": ts, "tag": "second"}, "evict-loc-b")

        # Only one entry may remain
        assert len(storage.cache._MEM_CACHE) == 1


# ---------------------------------------------------------------------------
# Lines 203-204: _load_json_fallback with corrupt JSON file
# ---------------------------------------------------------------------------


class TestLoadJsonFallbackCorrupt:
    def test_corrupt_json_file_returns_none(self, isolated_storage):
        """Lines 203-204: a corrupt JSON file in the fallback path returns None."""
        path = isolated_storage / "forecast_corrupt-loc.json"
        path.write_text("{not: valid json!!!")
        storage.cache._MEM_CACHE.clear()

        result = load_cached_forecast("corrupt-loc")
        assert result is None


# ---------------------------------------------------------------------------
# Lines 214-215: _save_json write error is swallowed
# ---------------------------------------------------------------------------


class TestSaveJsonWriteError:
    def test_write_error_is_logged_not_raised(self, monkeypatch):
        """Lines 214-215: if the JSON file can't be written, the exception is caught."""
        monkeypatch.setattr(
            "storage.cache._cache_path",
            lambda loc="": "/nonexistent-parent-dir-xyz/forecast.json",
        )
        # Must not raise
        _save_json({"generated_at": _fresh_ts()})


# ---------------------------------------------------------------------------
# Lines 225-226: _migrate_json_to_db failure is swallowed
# ---------------------------------------------------------------------------


class TestMigrateJsonToDbFailure:
    def test_migration_failure_is_swallowed_and_data_still_returned(
        self, isolated_storage, monkeypatch
    ):
        """Lines 225-226: save_forecast_cache failure during JSON-to-DB migration is
        caught; the function returns the JSON data regardless."""
        ts = _fresh_ts()
        data = {"generated_at": ts, "tag": "migrate-fail"}
        path = isolated_storage / "forecast_mig-fail-loc.json"
        path.write_text(json.dumps(data))
        storage.cache._MEM_CACHE.clear()

        def _boom(*_a, **_kw):
            raise RuntimeError("db down")

        monkeypatch.setattr("storage.cache.save_forecast_cache", _boom)

        result = load_cached_forecast("mig-fail-loc")
        assert result is not None
        assert result["tag"] == "migrate-fail"
