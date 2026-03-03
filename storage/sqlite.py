"""SQLite data-access layer for users, profiles, locations, forecasts, and catch logs."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE COLLATE NOCASE,
    password_hash TEXT,
    is_anonymous  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id        INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    fishing_profile TEXT,
    theme          TEXT DEFAULT 'light',
    units          TEXT DEFAULT 'F',
    favorites      TEXT DEFAULT '[]',
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS locations (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    location_id  TEXT,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS forecasts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id   TEXT NOT NULL,
    forecast_json TEXT NOT NULL,
    generated_at  TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_forecasts_location_time
ON forecasts(location_id, generated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS forecast_cache (
    user_id       INTEGER NOT NULL DEFAULT 0,
    location_id   TEXT NOT NULL,
    forecast_json TEXT NOT NULL,
    generated_at  TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, location_id)
);
CREATE INDEX IF NOT EXISTS idx_forecast_cache_updated
ON forecast_cache(updated_at DESC);

CREATE TABLE IF NOT EXISTS catch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    location_id TEXT NOT NULL,
    species     TEXT NOT NULL,
    size        TEXT,
    notes       TEXT,
    caught_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_catch_log_user_loc_time
ON catch_log(user_id, location_id, caught_at DESC, id DESC);
"""


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> List[str]:
    if not _table_exists(conn, table):
        return []
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def _run_migrations(conn: sqlite3.Connection) -> None:
    # Forecast schema migration: legacy forecasts(data) -> forecasts(forecast_json) history table
    if _table_exists(conn, "forecasts"):
        cols = set(_column_names(conn, "forecasts"))
        if "data" in cols and "forecast_json" not in cols:
            conn.execute("ALTER TABLE forecasts RENAME TO forecasts_legacy")

    conn.executescript(SCHEMA)

    # Legacy user preferences -> profiles + locations
    if _table_exists(conn, "user_preferences"):
        conn.execute(
            """
            INSERT OR IGNORE INTO profiles (user_id, fishing_profile, theme, units, favorites, updated_at)
            SELECT user_id, fishing_profile, COALESCE(theme, 'light'), COALESCE(units, 'F'),
                   COALESCE(favorites, '[]'), COALESCE(updated_at, datetime('now'))
            FROM user_preferences
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO locations (user_id, location_id, updated_at)
            SELECT user_id, location_id, COALESCE(updated_at, datetime('now'))
            FROM user_preferences
            """
        )

    # Legacy fishing_log -> catch_log
    if _table_exists(conn, "fishing_log"):
        conn.execute(
            """
            INSERT OR IGNORE INTO catch_log (id, user_id, location_id, species, size, notes, caught_at)
            SELECT id, user_id, location_id, species, size, notes, COALESCE(logged_at, datetime('now'))
            FROM fishing_log
            """
        )

    # Legacy forecasts_legacy -> new forecasts
    if _table_exists(conn, "forecasts_legacy"):
        conn.execute(
            """
            INSERT INTO forecasts (location_id, forecast_json, generated_at, created_at)
            SELECT location_id, data, generated_at, COALESCE(updated_at, datetime('now'))
            FROM forecasts_legacy
            """
        )


def init_db() -> None:
    conn = get_db()
    try:
        _run_migrations(conn)
        conn.commit()
    finally:
        conn.close()


# User auth -----------------------------------------------------------------

def create_user(username: str, password: str) -> Optional[int]:
    pw_hash = generate_password_hash(password)
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, is_anonymous) VALUES (?, ?, 0)",
            (username.strip(), pw_hash),
        )
        user_id = cur.lastrowid
        conn.execute("INSERT OR IGNORE INTO profiles (user_id) VALUES (?)", (user_id,))
        conn.execute("INSERT OR IGNORE INTO locations (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return user_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ? AND is_anonymous = 0",
        (username.strip(),),
    ).fetchone()
    conn.close()
    if row and row["password_hash"] and check_password_hash(row["password_hash"], password):
        return {"id": row["id"], "username": row["username"]}
    return None


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row["id"], "username": row["username"]}


# Profiles + locations ------------------------------------------------------

def get_preferences(user_id: int) -> Dict[str, Any]:
    conn = get_db()
    row = conn.execute(
        """
        SELECT l.location_id, p.theme, p.units, p.fishing_profile, p.favorites
        FROM profiles p
        LEFT JOIN locations l ON l.user_id = p.user_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {}

    profile = None
    if row["fishing_profile"]:
        try:
            profile = json.loads(row["fishing_profile"])
        except Exception:
            profile = None

    favorites: List[str] = []
    if row["favorites"]:
        try:
            favorites = json.loads(row["favorites"])
        except Exception:
            favorites = []

    return {
        "location_id": row["location_id"],
        "theme": row["theme"] or "light",
        "units": row["units"] or "F",
        "fishing_profile": profile,
        "favorites": favorites,
    }


def save_preferences(user_id: int, **kwargs: Any) -> None:
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO profiles (user_id) VALUES (?)", (user_id,))
        conn.execute("INSERT OR IGNORE INTO locations (user_id) VALUES (?)", (user_id,))

        if "location_id" in kwargs:
            conn.execute(
                "UPDATE locations SET location_id = ?, updated_at = datetime('now') WHERE user_id = ?",
                (kwargs.get("location_id"), user_id),
            )

        profile_sets = []
        vals: List[Any] = []
        map_fields = {
            "theme": "theme",
            "units": "units",
            "fishing_profile": "fishing_profile",
            "favorites": "favorites",
        }
        for key, col in map_fields.items():
            if key not in kwargs:
                continue
            val = kwargs[key]
            if key in {"fishing_profile", "favorites"}:
                val = json.dumps(val) if val is not None else None
            profile_sets.append(f"{col} = ?")
            vals.append(val)

        if profile_sets:
            profile_sets.append("updated_at = datetime('now')")
            vals.append(user_id)
            conn.execute(f"UPDATE profiles SET {', '.join(profile_sets)} WHERE user_id = ?", vals)

        conn.commit()
    finally:
        conn.close()


# Catch log -----------------------------------------------------------------

def get_log_entries(user_id: int, location_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, species, size, notes, caught_at FROM catch_log "
        "WHERE user_id = ? AND location_id = ? ORDER BY caught_at DESC, id DESC LIMIT ?",
        (user_id, location_id, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "species": r["species"],
            "size": r["size"],
            "notes": r["notes"],
            "date": r["caught_at"],
        }
        for r in rows
    ]


def add_log_entry(user_id: int, location_id: str, species: str, size: str = "", notes: str = "") -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO catch_log (user_id, location_id, species, size, notes) VALUES (?, ?, ?, ?, ?)",
        (user_id, location_id, species.strip(), size.strip(), notes.strip()),
    )
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return entry_id


def delete_log_entry(user_id: int, entry_id: int) -> bool:
    conn = get_db()
    cur = conn.execute("DELETE FROM catch_log WHERE id = ? AND user_id = ?", (entry_id, user_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_log_stats(user_id: int, location_id: str) -> Dict[str, Any]:
    conn = get_db()
    total = conn.execute(
        "SELECT COUNT(*) AS cnt FROM catch_log WHERE user_id = ? AND location_id = ?",
        (user_id, location_id),
    ).fetchone()["cnt"]

    species_rows = conn.execute(
        "SELECT species, COUNT(*) AS cnt FROM catch_log "
        "WHERE user_id = ? AND location_id = ? GROUP BY LOWER(species) ORDER BY cnt DESC",
        (user_id, location_id),
    ).fetchall()

    last = conn.execute(
        "SELECT caught_at FROM catch_log WHERE user_id = ? AND location_id = ? "
        "ORDER BY caught_at DESC, id DESC LIMIT 1",
        (user_id, location_id),
    ).fetchone()

    monthly_rows = conn.execute(
        "SELECT strftime('%m', caught_at) AS month, COUNT(*) AS cnt FROM catch_log "
        "WHERE user_id = ? AND location_id = ? GROUP BY month ORDER BY month",
        (user_id, location_id),
    ).fetchall()
    conn.close()

    species_breakdown = [{"species": r["species"], "count": r["cnt"]} for r in species_rows[:10]]
    monthly_counts = {int(r["month"]): r["cnt"] for r in monthly_rows}

    return {
        "total": total,
        "unique_species": len(species_rows),
        "top_species": species_rows[0]["species"] if species_rows else None,
        "last_date": last["caught_at"].split(" ")[0] if last else None,
        "species_breakdown": species_breakdown,
        "monthly_counts": monthly_counts,
    }


# Forecast cache -------------------------------------------------------------

def save_forecast_to_db(location_id: str, data: Dict[str, Any]) -> None:
    if not location_id:
        return
    generated_at = data.get("generated_at") or datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO forecasts (location_id, forecast_json, generated_at) VALUES (?, ?, ?)",
        (location_id, json.dumps(data), generated_at),
    )
    conn.commit()
    conn.close()


def save_forecast_cache(user_id: int, location_id: str, data: Dict[str, Any]) -> None:
    if not location_id:
        return
    generated_at = data.get("generated_at") or datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO forecast_cache (user_id, location_id, forecast_json, generated_at, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, location_id)
        DO UPDATE SET
            forecast_json = excluded.forecast_json,
            generated_at = excluded.generated_at,
            updated_at = datetime('now')
        """,
        (user_id, location_id, json.dumps(data), generated_at),
    )
    conn.commit()
    conn.close()


def load_forecast_cache(user_id: int, location_id: str) -> Optional[Dict[str, Any]]:
    if not location_id:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT forecast_json FROM forecast_cache WHERE user_id = ? AND location_id = ?",
        (user_id, location_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return json.loads(row["forecast_json"])
    except Exception:
        return None


def delete_forecast_cache(user_id: int, location_id: str) -> bool:
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM forecast_cache WHERE user_id = ? AND location_id = ?",
        (user_id, location_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def load_forecast(location_id: str) -> Optional[Dict[str, Any]]:
    if not location_id:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT forecast_json FROM forecasts WHERE location_id = ? "
        "ORDER BY generated_at DESC, id DESC LIMIT 1",
        (location_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return json.loads(row["forecast_json"])
    except Exception:
        return None


def list_cached_locations() -> List[Dict[str, str]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT location_id, MAX(generated_at) AS generated_at, MAX(created_at) AS updated_at "
        "FROM forecasts GROUP BY location_id ORDER BY MAX(created_at) DESC"
    ).fetchall()
    conn.close()
    return [
        {"location_id": r["location_id"], "generated_at": r["generated_at"], "updated_at": r["updated_at"]}
        for r in rows
    ]


def delete_forecast(location_id: str) -> bool:
    conn = get_db()
    cur = conn.execute("DELETE FROM forecasts WHERE location_id = ?", (location_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted
