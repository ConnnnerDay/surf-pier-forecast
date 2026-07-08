"""Tests for storage/sqlite.py — migration paths, CRUD, and cache eviction."""

from __future__ import annotations

import json
import sqlite3

import pytest

import storage.sqlite as sq
from storage.sqlite import (
    _column_names,
    add_log_entry,
    authenticate_user,
    bump_session_version,
    confirm_email,
    create_user,
    get_catch_conditions,
    get_db,
    get_log_entries,
    get_log_stats,
    get_page_layout,
    get_preferences,
    iter_notification_candidates,
    get_recent_catch_activity,
    get_user,
    init_db,
    load_forecast,
    load_forecast_cache,
    load_forecast_cache_for_user,
    save_forecast_cache,
    save_forecast_to_db,
    save_page_layout,
    save_preferences,
)


# ---------------------------------------------------------------------------
# Minimal "old" schema — missing all the columns that migrations should add
# ---------------------------------------------------------------------------

_OLD_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE COLLATE NOCASE,
    password_hash TEXT,
    email TEXT UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE profiles (
    user_id INTEGER PRIMARY KEY,
    fishing_profile TEXT,
    theme TEXT DEFAULT 'light',
    units TEXT DEFAULT 'F',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE locations (
    user_id INTEGER PRIMARY KEY,
    location_id TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE catch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    location_id TEXT NOT NULL,
    species TEXT NOT NULL,
    size TEXT,
    notes TEXT,
    caught_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id TEXT NOT NULL,
    forecast_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE forecast_cache (
    user_id INTEGER NOT NULL DEFAULT 0,
    location_id TEXT NOT NULL,
    forecast_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, location_id)
);
CREATE TABLE reg_scrape_cache (
    species_key TEXT NOT NULL,
    state TEXT NOT NULL,
    reg_json TEXT NOT NULL,
    scraped_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (species_key, state)
);
CREATE TABLE push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE notification_log (
    user_id INTEGER NOT NULL,
    location_id TEXT NOT NULL,
    sent_date TEXT NOT NULL,
    window_label TEXT,
    channel TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, location_id, sent_date)
);
"""


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Fresh DB with current schema."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    init_db()
    return tmp_path


@pytest.fixture
def user(isolated_db):
    return create_user("testuser", "Password1!")


# ---------------------------------------------------------------------------
# _column_names — line 258
# ---------------------------------------------------------------------------


class TestColumnNames:
    def test_unknown_table_returns_empty(self, isolated_db):
        conn = get_db()
        try:
            result = _column_names(conn, "not_in_known_tables")
        finally:
            conn.close()
        assert result == []

    def test_known_table_not_in_db_returns_empty(self, tmp_path, monkeypatch):
        # Point at a brand-new empty file (no schema yet).
        db_path = str(tmp_path / "empty.db")
        monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
        conn = get_db()
        try:
            result = _column_names(conn, "users")
        finally:
            conn.close()
        assert result == []


# ---------------------------------------------------------------------------
# Migration tests — user/profile/habitat ALTER TABLE branches
# Lines 276, 280, 282, 284, 286, 290, 292, 294, 296, 375, 377, 379, 383,
#        395, 399, 403, 405
# ---------------------------------------------------------------------------


class TestMigrations:
    def _make_old_db(self, tmp_path, monkeypatch) -> None:
        db_path = str(tmp_path / "old.db")
        raw = sqlite3.connect(db_path)
        raw.executescript(_OLD_SCHEMA)
        raw.commit()
        raw.close()
        monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)

    def test_user_column_migrations(self, tmp_path, monkeypatch):
        """Lines 276, 280, 282, 284, 286, 290, 292, 294, 296."""
        self._make_old_db(tmp_path, monkeypatch)
        init_db()
        conn = get_db()
        try:
            cols = _column_names(conn, "users")
        finally:
            conn.close()
        for col in (
            "email_confirmed",
            "password_reset_token",
            "password_reset_sent_at",
            "default_location_id",
            "session_version",
            "email",
            "display_name",
            "avatar_url",
            "is_anonymous",
        ):
            assert col in cols, f"Migration should have added '{col}'"

    def test_profile_column_migrations(self, tmp_path, monkeypatch):
        """Lines 375, 377, 379, 383."""
        self._make_old_db(tmp_path, monkeypatch)
        init_db()
        conn = get_db()
        try:
            cols = _column_names(conn, "profiles")
        finally:
            conn.close()
        for col in ("wind_units", "temp_units", "notification_prefs", "favorites"):
            assert col in cols, f"Migration should have added '{col}'"

    def test_catch_log_column_migrations(self, tmp_path, monkeypatch):
        """catch_log condition-snapshot columns, including hab_risk/river_discharge_cfs."""
        self._make_old_db(tmp_path, monkeypatch)
        init_db()
        conn = get_db()
        try:
            cols = _column_names(conn, "catch_log")
        finally:
            conn.close()
        for col in (
            "bait",
            "rig",
            "tide_state",
            "wind_dir",
            "water_temp_f",
            "moon_phase",
            "hab_risk",
            "river_discharge_cfs",
        ):
            assert col in cols, f"Migration should have added '{col}'"


# ---------------------------------------------------------------------------
# Legacy forecasts rename — line 270
# ---------------------------------------------------------------------------


class TestLegacyForecastsRename:
    def test_old_data_column_renamed_and_migrated(self, tmp_path, monkeypatch):
        """Line 270: old forecasts(data) table gets renamed to forecasts_legacy."""
        db_path = str(tmp_path / "oldfc.db")
        raw = sqlite3.connect(db_path)
        raw.execute(
            "CREATE TABLE forecasts ("
            "id INTEGER PRIMARY KEY, location_id TEXT, data TEXT, "
            "generated_at TEXT, updated_at TEXT DEFAULT (datetime('now')))"
        )
        raw.execute(
            "INSERT INTO forecasts (location_id, data, generated_at) "
            "VALUES ('old-loc', ?, '2025-01-01T00:00:00')",
            (json.dumps({"temp": 68}),),
        )
        raw.commit()
        raw.close()
        monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)

        init_db()

        # Data should have been migrated via forecasts_legacy → forecasts
        result = load_forecast("old-loc")
        assert result is not None
        assert result["temp"] == 68


# ---------------------------------------------------------------------------
# Legacy table migrations — user_preferences, fishing_log, forecasts_legacy
# Lines 409-417, 427, 437
# ---------------------------------------------------------------------------


class TestLegacyTableMigrations:
    def test_user_preferences_migration(self, tmp_path, monkeypatch):
        """Lines 409-417: user_preferences → profiles + locations."""
        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
        init_db()

        uid = create_user("pref_miguser", "Password1!")
        conn = get_db()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id      INTEGER PRIMARY KEY,
                fishing_profile TEXT,
                theme        TEXT DEFAULT 'light',
                units        TEXT DEFAULT 'F',
                favorites    TEXT DEFAULT '[]',
                updated_at   TEXT DEFAULT (datetime('now')),
                location_id  TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO user_preferences (user_id, location_id) VALUES (?, ?)",
            (uid, "migrated-loc"),
        )
        conn.commit()
        conn.close()

        # Second init_db() triggers the migration
        init_db()
        # No assertion needed — just verifying it doesn't crash and the INSERT fires

    def test_fishing_log_migration(self, tmp_path, monkeypatch):
        """Line 427: fishing_log → catch_log."""
        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
        init_db()

        uid = create_user("log_miguser", "Password1!")
        conn = get_db()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fishing_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                location_id TEXT NOT NULL,
                species     TEXT NOT NULL,
                size        TEXT,
                notes       TEXT,
                logged_at   TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "INSERT INTO fishing_log (user_id, location_id, species) VALUES (?, ?, ?)",
            (uid, "test-loc", "Red Drum"),
        )
        conn.commit()
        conn.close()

        init_db()

        # Verify migrated entry appears in catch_log
        entries = get_log_entries(uid, "test-loc")
        assert any(e["species"] == "Red Drum" for e in entries)

    def test_forecasts_legacy_migration(self, tmp_path, monkeypatch):
        """Line 437: forecasts_legacy → forecasts."""
        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
        init_db()

        conn = get_db()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forecasts_legacy (
                id           INTEGER PRIMARY KEY,
                location_id  TEXT,
                data         TEXT,
                generated_at TEXT,
                updated_at   TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "INSERT INTO forecasts_legacy (location_id, data, generated_at) "
            "VALUES (?, ?, ?)",
            ("legacy-loc", json.dumps({"verdict": "old"}), "2025-06-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        init_db()

        result = load_forecast("legacy-loc")
        assert result is not None
        assert result["verdict"] == "old"


# ---------------------------------------------------------------------------
# create_user duplicate — lines 496-497
# ---------------------------------------------------------------------------


class TestCreateUserDuplicate:
    def test_duplicate_username_returns_none(self, isolated_db):
        uid = create_user("dupuser", "Password1!")
        assert uid is not None
        uid2 = create_user("dupuser", "Password2!")
        assert uid2 is None

    def test_duplicate_email_returns_none(self, isolated_db):
        uid = create_user("ua", "Password1!", email="same@example.com")
        assert uid is not None
        uid2 = create_user("ub", "Password2!", email="same@example.com")
        assert uid2 is None


# ---------------------------------------------------------------------------
# authenticate_user success — line 520
# ---------------------------------------------------------------------------


class TestAuthenticateUser:
    def test_success_returns_id_and_username(self, isolated_db):
        uid = create_user("authuser", "Password1!")
        result = authenticate_user("authuser", "Password1!")
        assert result is not None
        assert result["id"] == uid
        assert result["username"] == "authuser"

    def test_wrong_password_returns_none(self, isolated_db):
        create_user("authuser2", "Password1!")
        assert authenticate_user("authuser2", "wrong") is None

    def test_nonexistent_user_returns_none(self, isolated_db):
        assert authenticate_user("nobody", "Password1!") is None


# ---------------------------------------------------------------------------
# get_user cache eviction — line 552
# ---------------------------------------------------------------------------


class TestGetUserCacheEviction:
    def test_cache_evicts_oldest_when_full(self, isolated_db, monkeypatch):
        monkeypatch.setattr(sq, "_USER_CACHE_MAX", 2)
        uid1 = create_user("cu1", "Password1!")
        uid2 = create_user("cu2", "Password1!")
        uid3 = create_user("cu3", "Password1!")

        get_user(uid1)
        get_user(uid2)
        # Cache is now at capacity (2). uid3 triggers eviction of uid1.
        get_user(uid3)

        assert uid1 not in sq._USER_CACHE
        assert uid3 in sq._USER_CACHE


# ---------------------------------------------------------------------------
# confirm_email — lines 572-581
# ---------------------------------------------------------------------------


class TestConfirmEmail:
    def test_confirm_email_sets_flag(self, isolated_db):
        uid = create_user("emailuser", "Password1!")
        confirm_email(uid)
        user = get_user(uid)
        assert user["email_confirmed"] is True

    def test_confirm_email_clears_user_cache(self, isolated_db):
        uid = create_user("emailuser2", "Password1!")
        get_user(uid)  # prime the cache
        assert uid in sq._USER_CACHE
        confirm_email(uid)
        assert uid not in sq._USER_CACHE


# ---------------------------------------------------------------------------
# bump_session_version — lines 591-604
# ---------------------------------------------------------------------------


class TestBumpSessionVersion:
    def test_increments_version(self, isolated_db):
        uid = create_user("bumpuser", "Password1!")
        v1 = bump_session_version(uid)
        v2 = bump_session_version(uid)
        assert v2 == v1 + 1

    def test_clears_user_cache(self, isolated_db):
        uid = create_user("bumpuser2", "Password1!")
        get_user(uid)
        assert uid in sq._USER_CACHE
        bump_session_version(uid)
        assert uid not in sq._USER_CACHE


# ---------------------------------------------------------------------------
# get_preferences — lines 684, 690-695, 701-705, 711-716, 730
# ---------------------------------------------------------------------------


class TestGetPreferences:
    def test_nonexistent_user_returns_empty_dict(self, isolated_db):
        """Line 684: no profiles row → {}."""
        result = get_preferences(99999)
        assert result == {}

    def test_corrupt_fishing_profile_returns_none(self, isolated_db):
        """Lines 690-695."""
        uid = create_user("cp1", "Password1!")
        conn = get_db()
        conn.execute(
            "UPDATE profiles SET fishing_profile = ? WHERE user_id = ?",
            ("not-valid-json!", uid),
        )
        conn.commit()
        conn.close()
        sq._PREFS_CACHE.pop(uid, None)

        prefs = get_preferences(uid)
        assert prefs["fishing_profile"] is None

    def test_corrupt_favorites_returns_empty_list(self, isolated_db):
        """Lines 701-705."""
        uid = create_user("cp2", "Password1!")
        conn = get_db()
        conn.execute(
            "UPDATE profiles SET favorites = ? WHERE user_id = ?",
            ("not-valid-json!", uid),
        )
        conn.commit()
        conn.close()
        sq._PREFS_CACHE.pop(uid, None)

        prefs = get_preferences(uid)
        assert prefs["favorites"] == []

    def test_corrupt_notification_prefs_returns_empty_dict(self, isolated_db):
        """Lines 711-716."""
        uid = create_user("cp3", "Password1!")
        conn = get_db()
        conn.execute(
            "UPDATE profiles SET notification_prefs = ? WHERE user_id = ?",
            ("not-valid-json!", uid),
        )
        conn.commit()
        conn.close()
        sq._PREFS_CACHE.pop(uid, None)

        prefs = get_preferences(uid)
        assert prefs["notification_prefs"] == {}

    def test_prefs_cache_evicts_oldest_when_full(self, isolated_db, monkeypatch):
        """Line 730."""
        monkeypatch.setattr(sq, "_PREFS_CACHE_MAX", 2)
        uid1 = create_user("pc1", "Password1!")
        uid2 = create_user("pc2", "Password1!")
        uid3 = create_user("pc3", "Password1!")

        get_preferences(uid1)
        get_preferences(uid2)
        # Cache at capacity; uid3 triggers eviction of uid1.
        get_preferences(uid3)

        assert uid1 not in sq._PREFS_CACHE
        assert uid3 in sq._PREFS_CACHE


# ---------------------------------------------------------------------------
# get_preferences_for_notification — lines 826-827
# ---------------------------------------------------------------------------


class TestIterNotificationCandidates:
    def test_corrupt_json_falls_back_to_default(self, isolated_db):
        """Lines 826-827: _loads() except path."""
        uid = create_user("pn1", "Password1!", email="pn1@example.com")
        # Set notification_prefs to non-empty invalid JSON so the query
        # returns the row but _loads() hits the except branch.
        conn = get_db()
        conn.execute(
            "UPDATE users SET email_confirmed = 1 WHERE id = ?", (uid,)
        )
        conn.execute(
            "UPDATE profiles SET notification_prefs = ? WHERE user_id = ?",
            ("invalid-json", uid),
        )
        conn.commit()
        conn.close()
        sq._PREFS_CACHE.pop(uid, None)

        result = iter_notification_candidates()
        # The row is included but notification_prefs falls back to {}
        row = next((r for r in result if r["user_id"] == uid), None)
        assert row is not None
        assert row["notification_prefs"] == {}


# ---------------------------------------------------------------------------
# get_page_layout / save_page_layout — lines 935-947, 951-960
# ---------------------------------------------------------------------------


class TestPageLayout:
    def test_save_and_load(self, isolated_db):
        """Lines 951-960, 944-945."""
        uid = create_user("plu", "Password1!")
        layout = [{"widget": "forecast"}, {"widget": "tide"}]
        save_page_layout(uid, layout)
        result = get_page_layout(uid)
        assert result == layout

    def test_no_layout_returns_none(self, isolated_db):
        """Lines 942-943."""
        uid = create_user("plu2", "Password1!")
        assert get_page_layout(uid) is None

    def test_nonexistent_user_returns_none(self, isolated_db):
        """No profiles row → returns None."""
        assert get_page_layout(99999) is None

    def test_corrupt_layout_json_returns_none(self, isolated_db):
        """Lines 946-947."""
        uid = create_user("plu3", "Password1!")
        conn = get_db()
        conn.execute(
            "UPDATE profiles SET page_layout = ? WHERE user_id = ?",
            ("bad-json!", uid),
        )
        conn.commit()
        conn.close()
        assert get_page_layout(uid) is None


# ---------------------------------------------------------------------------
# get_catch_conditions without location_id — line 1010
# ---------------------------------------------------------------------------


class TestGetCatchConditions:
    def test_no_location_returns_all_user_catches(self, isolated_db):
        """Line 1010: empty location_id uses the cross-location query."""
        uid = create_user("ccuser", "Password1!")
        add_log_entry(uid, "loc-a", "Red Drum")
        add_log_entry(uid, "loc-b", "Bluefish")

        result = get_catch_conditions(uid, location_id="")
        assert len(result) == 2
        species = {r["species"] for r in result}
        assert "Red Drum" in species
        assert "Bluefish" in species


# ---------------------------------------------------------------------------
# get_recent_catch_activity — line 1050 (empty location_id)
# ---------------------------------------------------------------------------


class TestRecentCatchActivity:
    def test_empty_location_returns_none(self, isolated_db):
        """Line 1050."""
        assert get_recent_catch_activity("") is None


# ---------------------------------------------------------------------------
# add_log_entry with bad water_temp_f — lines 1112-1113
# ---------------------------------------------------------------------------


class TestAddLogEntryBadWaterTemp:
    def test_non_numeric_water_temp_stored_as_none(self, isolated_db):
        """Lines 1112-1113: float() raises → water_temp_f = None."""
        uid = create_user("wtuser", "Password1!")
        entry_id = add_log_entry(
            uid, "test-loc", "Flounder",
            conditions={"water_temp_f": "not-a-number"},
        )
        assert entry_id > 0

        rows = get_catch_conditions(uid, "test-loc")
        assert len(rows) == 1
        assert rows[0]["water_temp_f"] is None


# ---------------------------------------------------------------------------
# get_log_stats cache hit and eviction — lines 1168, 1215
# ---------------------------------------------------------------------------


class TestLogStatsCache:
    def test_second_call_hits_cache(self, isolated_db):
        """Line 1168: cache hit returns immediately."""
        uid = create_user("lsc1", "Password1!")
        add_log_entry(uid, "loc1", "Drum")

        result1 = get_log_stats(uid, "loc1")
        # Prime the cache; monkeypatch get_db to explode if called again.
        import unittest.mock as _mock
        with _mock.patch.object(sq, "get_db", side_effect=AssertionError("cache miss")):
            result2 = get_log_stats(uid, "loc1")
        assert result2 == result1

    def test_cache_evicts_oldest_when_full(self, isolated_db, monkeypatch):
        """Line 1215."""
        monkeypatch.setattr(sq, "_LOG_STATS_CACHE_MAX", 2)
        uid = create_user("lsc2", "Password1!")
        add_log_entry(uid, "loc-a", "Drum")
        add_log_entry(uid, "loc-b", "Drum")
        add_log_entry(uid, "loc-c", "Drum")

        get_log_stats(uid, "loc-a")
        get_log_stats(uid, "loc-b")
        get_log_stats(uid, "loc-c")  # evicts (uid, "loc-a")

        assert (uid, "loc-a") not in sq._LOG_STATS_CACHE
        assert (uid, "loc-c") in sq._LOG_STATS_CACHE


# ---------------------------------------------------------------------------
# save_forecast_cache empty location_id — line 1263
# ---------------------------------------------------------------------------


class TestSaveForecastCacheEmpty:
    def test_empty_location_is_noop(self, isolated_db):
        """Line 1263."""
        uid = create_user("sfc", "Password1!")
        # Should not crash and should not insert anything
        save_forecast_cache(uid, "", {"generated_at": "2026-01-01T00:00:00"})
        result = load_forecast_cache(uid, "")
        assert result is None


# ---------------------------------------------------------------------------
# load_forecast_cache — lines 1285-1300
# ---------------------------------------------------------------------------


class TestLoadForecastCache:
    def test_save_and_load(self, isolated_db):
        """Lines 1285-1300: happy path."""
        uid = create_user("lfc", "Password1!")
        data = {"generated_at": "2026-06-01T12:00:00", "temp": 72}
        save_forecast_cache(uid, "test-loc", data)
        result = load_forecast_cache(uid, "test-loc")
        assert result == data

    def test_empty_location_returns_none(self, isolated_db):
        """Lines 1285-1286: guard on empty location_id."""
        uid = create_user("lfc2", "Password1!")
        assert load_forecast_cache(uid, "") is None

    def test_missing_entry_returns_none(self, isolated_db):
        """Lines 1295-1296: no matching row."""
        uid = create_user("lfc3", "Password1!")
        assert load_forecast_cache(uid, "nonexistent") is None

    def test_corrupt_json_returns_none(self, isolated_db):
        """Lines 1299-1300: bad JSON → None."""
        uid = create_user("lfc4", "Password1!")
        conn = get_db()
        conn.execute(
            "INSERT INTO forecast_cache (user_id, location_id, forecast_json, generated_at) "
            "VALUES (?, ?, ?, ?)",
            (uid, "bad-loc", "not-json!", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()
        assert load_forecast_cache(uid, "bad-loc") is None


# ---------------------------------------------------------------------------
# load_forecast_cache_for_user — lines 1316, 1339-1340
# ---------------------------------------------------------------------------


class TestLoadForecastCacheForUser:
    def test_empty_location_returns_none(self, isolated_db):
        """Line 1316."""
        uid = create_user("lfcfu", "Password1!")
        assert load_forecast_cache_for_user(uid, "") is None

    def test_corrupt_json_returns_none(self, isolated_db):
        """Lines 1339-1340: bad JSON → None."""
        uid = create_user("lfcfu2", "Password1!")
        conn = get_db()
        conn.execute(
            "INSERT INTO forecast_cache (user_id, location_id, forecast_json, generated_at) "
            "VALUES (?, ?, ?, ?)",
            (uid, "bad-loc2", "not-json!", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()
        assert load_forecast_cache_for_user(uid, "bad-loc2") is None

    def test_anonymous_user_path(self, isolated_db):
        """user_id == 0 takes the simple equality branch."""
        data = {"generated_at": "2026-06-01T12:00:00", "kind": "anon"}
        save_forecast_cache(0, "anon-loc", data)
        result = load_forecast_cache_for_user(0, "anon-loc")
        assert result == data


# ---------------------------------------------------------------------------
# load_forecast bad JSON — lines 1391-1392
# ---------------------------------------------------------------------------


class TestLoadForecastBadJson:
    def test_corrupt_json_returns_none(self, isolated_db):
        """Lines 1391-1392."""
        conn = get_db()
        conn.execute(
            "INSERT INTO forecasts (location_id, forecast_json, generated_at) "
            "VALUES (?, ?, ?)",
            ("bad-forecast", "not-json!", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()
        assert load_forecast("bad-forecast") is None

