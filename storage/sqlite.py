"""SQLite data-access layer for users, profiles, locations, forecasts, and catch logs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

# Dummy hash used in authenticate_user to ensure a constant-time password check
# is always performed, regardless of whether the username exists.  This
# prevents an attacker from enumerating valid usernames by measuring how long
# the login endpoint takes to respond.
_DUMMY_HASH = generate_password_hash("__sentinel__", method="pbkdf2:sha256")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE COLLATE NOCASE,
    password_hash TEXT,
    email         TEXT UNIQUE COLLATE NOCASE,
    email_confirmed INTEGER NOT NULL DEFAULT 0,
    email_verification_token TEXT,
    email_verification_sent_at TEXT,
    password_reset_token TEXT,
    password_reset_sent_at TEXT,
    default_location_id TEXT,
    is_anonymous  INTEGER NOT NULL DEFAULT 0,
    session_version INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id        INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    fishing_profile TEXT,
    theme          TEXT DEFAULT 'light',
    units          TEXT DEFAULT 'F',
    wind_units     TEXT DEFAULT 'knots',
    temp_units     TEXT DEFAULT 'F',
    notification_prefs TEXT DEFAULT '{}',
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

CREATE TABLE IF NOT EXISTS reg_scrape_cache (
    species_key TEXT NOT NULL,
    state       TEXT NOT NULL,
    reg_json    TEXT NOT NULL,
    scraped_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (species_key, state)
);
"""


def get_db() -> sqlite3.Connection:
    """Open and return a new SQLite connection with row-factory set.

    ``journal_mode=WAL`` is a persistent database setting applied once in
    ``init_db()``.  ``foreign_keys=ON`` must be set per-connection (it is a
    connection-level pragma that SQLite resets on every new connection), so it
    remains here.
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


_KNOWN_TABLES = frozenset({
    "users", "profiles", "locations", "forecasts",
    "forecast_cache", "catch_log", "reg_scrape_cache",
})


def _column_names(conn: sqlite3.Connection, table: str) -> List[str]:
    if table not in _KNOWN_TABLES or not _table_exists(conn, table):
        return []
    # PRAGMA does not support parameter binding; validate against the known-table
    # whitelist above before interpolating the name into the statement.
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def _run_migrations(conn: sqlite3.Connection) -> None:
    # Forecast schema migration: legacy forecasts(data) -> forecasts(forecast_json) history table
    if _table_exists(conn, "forecasts"):
        cols = set(_column_names(conn, "forecasts"))
        if "data" in cols and "forecast_json" not in cols:
            conn.execute("ALTER TABLE forecasts RENAME TO forecasts_legacy")

    conn.executescript(SCHEMA)

    user_cols = set(_column_names(conn, "users"))
    if "email_confirmed" not in user_cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN email_confirmed INTEGER NOT NULL DEFAULT 0"
        )
    if "password_reset_token" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_reset_token TEXT")
    if "password_reset_sent_at" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_reset_sent_at TEXT")
    if "default_location_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN default_location_id TEXT")
    if "session_version" not in user_cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0"
        )
    if "email" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT UNIQUE COLLATE NOCASE")
    if "email_verification_token" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_verification_token TEXT")
    if "email_verification_sent_at" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_verification_sent_at TEXT")

    profile_cols = set(_column_names(conn, "profiles"))
    if "wind_units" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN wind_units TEXT DEFAULT 'knots'")
    if "temp_units" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN temp_units TEXT DEFAULT 'F'")
    if "notification_prefs" not in profile_cols:
        conn.execute(
            "ALTER TABLE profiles ADD COLUMN notification_prefs TEXT DEFAULT '{}'"
        )

    catch_log_cols = set(_column_names(conn, "catch_log"))
    if "photo1_path" not in catch_log_cols:
        conn.execute("ALTER TABLE catch_log ADD COLUMN photo1_path TEXT")
    if "photo2_path" not in catch_log_cols:
        conn.execute("ALTER TABLE catch_log ADD COLUMN photo2_path TEXT")

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


def _prune_old_forecasts(conn: sqlite3.Connection) -> None:
    """Keep only the most-recent row per location in the forecasts history table.

    The ``forecasts`` table is an append-only log used for historical reference.
    Without pruning it grows without bound.  We retain the single newest row
    per location so ``load_forecast()`` still works as a last-resort fallback.
    """
    conn.execute(
        """
        DELETE FROM forecasts
        WHERE id NOT IN (
            SELECT MAX(id) FROM forecasts GROUP BY location_id
        )
        """
    )


def init_db() -> None:
    conn = get_db()
    try:
        # WAL mode is a persistent database-level setting — set it once here
        # rather than on every connection in get_db() to avoid redundant work.
        conn.execute("PRAGMA journal_mode=WAL")
        _run_migrations(conn)
        _prune_old_forecasts(conn)
        conn.commit()
    finally:
        conn.close()


# User auth -----------------------------------------------------------------


def create_user(username: str, password: str, email: Optional[str] = None) -> Optional[int]:
    # Explicitly specify the algorithm so we are not dependent on Werkzeug's
    # default changing in a future release.
    pw_hash = generate_password_hash(password, method="pbkdf2:sha256")
    email_val = email.strip().lower() if email else None
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, email, is_anonymous) VALUES (?, ?, ?, 0)",
            (username.strip(), pw_hash, email_val),
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
    try:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ? AND is_anonymous = 0",
            (username.strip(),),
        ).fetchone()
    finally:
        conn.close()
    # Always run the hash check to prevent timing-based user enumeration.
    # When no matching row exists, compare against the dummy hash so the
    # response time is indistinguishable from a real (failed) comparison.
    stored_hash = (row["password_hash"] if (row and row["password_hash"]) else _DUMMY_HASH)
    password_ok = check_password_hash(stored_hash, password)
    if not password_ok or not row:
        return None
    return {"id": row["id"], "username": row["username"]}


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, username, email, email_confirmed, default_location_id, session_version "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "email_confirmed": bool(row["email_confirmed"]),
        "default_location_id": row["default_location_id"],
        "session_version": row["session_version"],
    }


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Return the user with the given email address (case-insensitive), or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE email = ? COLLATE NOCASE AND is_anonymous = 0",
            (email.strip().lower(),),
        ).fetchone()
    finally:
        conn.close()
    return {"id": row["id"]} if row else None


def _hash_verification_token(token: str) -> str:
    """Return the SHA-256 hex digest of a verification token.

    Tokens are stored hashed so that a database read alone cannot produce a
    working verification URL.  The raw token travels only in the email link.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def set_email_verification_token(user_id: int, token: str) -> None:
    """Hash *token* and store it along with the current timestamp."""
    token_hash = _hash_verification_token(token)
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET email_verification_token = ?, "
            "email_verification_sent_at = datetime('now') WHERE id = ?",
            (token_hash, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_email_verification_sent_at(user_id: int) -> Optional[str]:
    """Return the ISO timestamp of the last verification email, or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT email_verification_sent_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    return row["email_verification_sent_at"] if row else None


def get_user_by_verification_token(token: str) -> Optional[Dict[str, Any]]:
    """Return the user matching *token* if the token was sent within 24 hours.

    The token is hashed before querying so the raw value never touches the DB.
    """
    token_hash = _hash_verification_token(token)
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, email_confirmed, email_verification_sent_at "
            "FROM users WHERE email_verification_token = ? AND is_anonymous = 0",
            (token_hash,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    # Expire tokens after 2 hours to limit the window if a link is leaked.
    sent_at_raw = row["email_verification_sent_at"]
    if sent_at_raw:
        try:
            sent_at = datetime.fromisoformat(sent_at_raw).replace(tzinfo=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            if (now - sent_at).total_seconds() > 7200:
                return None
        except Exception:
            pass
    return {"id": row["id"], "email_confirmed": bool(row["email_confirmed"])}


def confirm_email(user_id: int) -> None:
    """Mark the user's email as confirmed and clear the verification token."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET email_confirmed = 1, email_verification_token = NULL, "
            "email_verification_sent_at = NULL WHERE id = ?",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()


def bump_session_version(user_id: int) -> int:
    """Increment the session version for a user and return the new value.

    Incrementing the version invalidates all existing sessions for that user
    (on other devices/browsers) because ``_load_user`` rejects sessions whose
    stored version no longer matches the DB value.
    """
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET session_version = session_version + 1 WHERE id = ?",
            (user_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT session_version FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["session_version"] if row else 0
    finally:
        conn.close()


def change_password(user_id: int, new_password: str) -> int:
    """Hash *new_password* and store it, then bump and return the new session_version.

    Bumping the version invalidates every other active session so the user is
    effectively logged out everywhere except the current device (which receives
    the new version in its cookie immediately after this call).
    """
    pw_hash = generate_password_hash(new_password, method="pbkdf2:sha256")
    conn = get_db()
    try:
        # Also clear any pending email-verification token: it was issued under
        # the old credentials, so it should not remain valid after a password
        # change (the user can request a new verification email afterwards).
        conn.execute(
            "UPDATE users SET password_hash = ?, session_version = session_version + 1, "
            "email_verification_token = NULL, email_verification_sent_at = NULL "
            "WHERE id = ?",
            (pw_hash, user_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT session_version FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["session_version"] if row else 0
    finally:
        conn.close()


def get_user_password_hash(user_id: int) -> Optional[str]:
    """Return the stored password hash for *user_id*, or None if not found."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["password_hash"] if row else None
    finally:
        conn.close()


def get_all_user_photo_paths(user_id: int) -> List[str]:
    """Return every stored photo path for *user_id* across all catch-log entries."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT photo1_path, photo2_path FROM catch_log WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    paths: List[str] = []
    for row in rows:
        if row["photo1_path"]:
            paths.append(row["photo1_path"])
        if row["photo2_path"]:
            paths.append(row["photo2_path"])
    return paths


def delete_user(user_id: int) -> None:
    """Permanently delete a user and all their data.

    The users table has ON DELETE CASCADE on every child table (profiles,
    locations, catch_log, forecast_cache), so a single DELETE removes
    everything.  Callers must delete uploaded files from disk before calling
    this function, because the file paths are stored in catch_log rows.
    """
    conn = get_db()
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# Profiles + locations ------------------------------------------------------


def get_preferences(user_id: int) -> Dict[str, Any]:
    conn = get_db()
    row = conn.execute(
        """
        SELECT l.location_id, p.theme, p.units, p.wind_units, p.temp_units,
               p.notification_prefs, p.fishing_profile, p.favorites
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
            logger.warning(
                "Corrupt fishing_profile JSON for user_id=%s; resetting to None",
                user_id,
            )
            profile = None

    favorites: List[str] = []
    if row["favorites"]:
        try:
            favorites = json.loads(row["favorites"])
        except Exception:
            logger.warning(
                "Corrupt favorites JSON for user_id=%s; resetting to []", user_id
            )
            favorites = []

    notification_prefs: Dict[str, Any] = {}
    if row["notification_prefs"]:
        try:
            notification_prefs = json.loads(row["notification_prefs"])
        except Exception:
            logger.warning(
                "Corrupt notification_prefs JSON for user_id=%s; resetting to {}",
                user_id,
            )
            notification_prefs = {}

    return {
        "location_id": row["location_id"],
        "theme": row["theme"] or "light",
        "units": row["units"] or "F",
        "wind_units": row["wind_units"] or "knots",
        "temp_units": row["temp_units"] or "F",
        "notification_prefs": notification_prefs,
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
            "wind_units": "wind_units",
            "temp_units": "temp_units",
            "notification_prefs": "notification_prefs",
            "fishing_profile": "fishing_profile",
            "favorites": "favorites",
        }
        for key, col in map_fields.items():
            if key not in kwargs:
                continue
            val = kwargs[key]
            if key in {"fishing_profile", "favorites", "notification_prefs"}:
                val = json.dumps(val) if val is not None else None
            profile_sets.append(f"{col} = ?")
            vals.append(val)

        if "default_location_id" in kwargs:
            conn.execute(
                "UPDATE users SET default_location_id = ? WHERE id = ?",
                (kwargs.get("default_location_id"), user_id),
            )

        if profile_sets:
            profile_sets.append("updated_at = datetime('now')")
            vals.append(user_id)
            conn.execute(
                f"UPDATE profiles SET {', '.join(profile_sets)} WHERE user_id = ?", vals
            )

        conn.commit()
    finally:
        conn.close()


# Catch log -----------------------------------------------------------------


def get_log_entries(
    user_id: int, location_id: str, limit: int = 50
) -> List[Dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, species, size, notes, caught_at, photo1_path, photo2_path FROM catch_log "
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
            "photo1_path": r["photo1_path"],
            "photo2_path": r["photo2_path"],
        }
        for r in rows
    ]


_CATCH_LOG_SIZE_MAX = 50    # e.g. "24 inches"
_CATCH_LOG_NOTES_MAX = 1000  # free-text field


def add_log_entry(
    user_id: int, location_id: str, species: str, size: str = "", notes: str = ""
) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO catch_log (user_id, location_id, species, size, notes) VALUES (?, ?, ?, ?, ?)",
        (
            user_id,
            location_id,
            species.strip()[:100],
            size.strip()[:_CATCH_LOG_SIZE_MAX],
            notes.strip()[:_CATCH_LOG_NOTES_MAX],
        ),
    )
    conn.commit()
    entry_id = cur.lastrowid or 0
    conn.close()
    return entry_id


def delete_log_entry(user_id: int, entry_id: int) -> bool:
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM catch_log WHERE id = ? AND user_id = ?", (entry_id, user_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_entry_photo_paths(
    user_id: int, entry_id: int
) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Return (photo1_path, photo2_path) for the entry, or None if entry not found."""
    conn = get_db()
    row = conn.execute(
        "SELECT photo1_path, photo2_path FROM catch_log WHERE id = ? AND user_id = ?",
        (entry_id, user_id),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return (row["photo1_path"], row["photo2_path"])


def attach_photos_to_entry(
    user_id: int,
    entry_id: int,
    *,
    photo1_path: Optional[str] = None,
    photo2_path: Optional[str] = None,
) -> bool:
    """Update photo slots for an entry.  Only slots explicitly provided are updated.

    Returns True if the entry exists and was updated, False otherwise.
    """
    if photo1_path is None and photo2_path is None:
        return False

    conn = get_db()
    try:
        if photo1_path is not None and photo2_path is not None:
            cur = conn.execute(
                "UPDATE catch_log SET photo1_path = ?, photo2_path = ? WHERE id = ? AND user_id = ?",
                (photo1_path, photo2_path, entry_id, user_id),
            )
        elif photo1_path is not None:
            cur = conn.execute(
                "UPDATE catch_log SET photo1_path = ? WHERE id = ? AND user_id = ?",
                (photo1_path, entry_id, user_id),
            )
        else:
            cur = conn.execute(
                "UPDATE catch_log SET photo2_path = ? WHERE id = ? AND user_id = ?",
                (photo2_path, entry_id, user_id),
            )
        conn.commit()
        ok = cur.rowcount > 0
    finally:
        conn.close()
    return ok


def get_log_stats(user_id: int, location_id: str) -> Dict[str, Any]:
    """Return aggregate statistics for a user's catch log at a location.

    Uses a single query with a CTE to avoid 4 separate roundtrips to SQLite.
    """
    conn = get_db()
    try:
        # One pass over the table for all aggregates.
        species_rows = conn.execute(
            "SELECT species, COUNT(*) AS cnt FROM catch_log "
            "WHERE user_id = ? AND location_id = ? GROUP BY LOWER(species) ORDER BY cnt DESC",
            (user_id, location_id),
        ).fetchall()

        agg = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                MAX(caught_at) AS last_caught_at,
                strftime('%m', caught_at) AS month,
                COUNT(*) AS month_cnt
            FROM catch_log
            WHERE user_id = ? AND location_id = ?
            GROUP BY strftime('%m', caught_at)
            ORDER BY month
            """,
            (user_id, location_id),
        ).fetchall()
    finally:
        conn.close()

    # Build monthly counts and derive total from species aggregation.
    total = sum(r["cnt"] for r in species_rows)
    monthly_counts = {int(r["month"]): r["month_cnt"] for r in agg if r["month"]}
    last_date_raw = max(
        (r["last_caught_at"] for r in agg if r["last_caught_at"]), default=None
    )
    last_date = last_date_raw[:10] if last_date_raw else None

    return {
        "total": total,
        "unique_species": len(species_rows),
        "top_species": species_rows[0]["species"] if species_rows else None,
        "last_date": last_date,
        "species_breakdown": [
            {"species": r["species"], "count": r["cnt"]} for r in species_rows[:10]
        ],
        "monthly_counts": monthly_counts,
    }


def get_recent_logs(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, location_id, species, size, notes, caught_at, photo1_path, photo2_path FROM catch_log "
        "WHERE user_id = ? ORDER BY caught_at DESC, id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "location_id": r["location_id"],
            "species": r["species"],
            "size": r["size"],
            "notes": r["notes"],
            "date": r["caught_at"],
            "photo1_path": r["photo1_path"],
            "photo2_path": r["photo2_path"],
        }
        for r in rows
    ]


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
        {
            "location_id": r["location_id"],
            "generated_at": r["generated_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def delete_forecast(location_id: str) -> bool:
    conn = get_db()
    cur = conn.execute("DELETE FROM forecasts WHERE location_id = ?", (location_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted
