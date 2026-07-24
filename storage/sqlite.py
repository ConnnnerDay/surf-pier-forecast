"""SQLite data-access layer for users, profiles, locations, forecasts, and catch logs."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time as _time
from datetime import datetime
from typing import Any, Optional

from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process TTL cache for get_preferences
# ---------------------------------------------------------------------------
# Prefs change only on explicit profile/settings saves (save_preferences).
# Caching avoids a SQLite round-trip + 3× JSON parses on every authenticated
# page render.  Invalidated immediately on write so changes take effect at
# the next request.
_PREFS_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_PREFS_CACHE_TTL: int = 300  # 5 minutes
_PREFS_CACHE_MAX: int = 512

# ---------------------------------------------------------------------------
# Short-lived per-request user cache for get_user
# ---------------------------------------------------------------------------
# On a dashboard load the browser fires ~12 async API requests in parallel;
# each triggers a before_request that calls get_user once.  A 15-second TTL
# collapses all of those into a single DB hit while keeping the session_version
# invalidation lag negligible for a fishing-forecast context.
_USER_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_USER_CACHE_TTL: int = 15  # seconds
_USER_CACHE_MAX: int = 512

# ---------------------------------------------------------------------------
# Short-lived cache for get_log_stats
# ---------------------------------------------------------------------------
# get_log_stats fires 2 queries on every dashboard render (one per logged-in
# user).  The result is only used for badge display so 2-minute staleness is
# acceptable.  Invalidated on add/delete so the badge updates after a new catch
# is logged in the same session.
_LOG_STATS_CACHE: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_LOG_STATS_CACHE_TTL: int = 120  # 2 minutes
_LOG_STATS_CACHE_MAX: int = 256

# Dummy hash used in authenticate_user to ensure a constant-time password check
# is always performed, regardless of whether the username exists.  This
# prevents an attacker from enumerating valid usernames by measuring how long
# the login endpoint takes to respond.
_DUMMY_HASH = generate_password_hash("__sentinel__", method="scrypt")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "app.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE COLLATE NOCASE,
    password_hash TEXT,
    email         TEXT UNIQUE COLLATE NOCASE,
    email_confirmed INTEGER NOT NULL DEFAULT 1,
    password_reset_token TEXT,
    password_reset_sent_at TEXT,
    default_location_id TEXT,
    is_anonymous  INTEGER NOT NULL DEFAULT 0,
    session_version INTEGER NOT NULL DEFAULT 0,
    display_name  TEXT,
    avatar_url    TEXT,
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
CREATE INDEX IF NOT EXISTS idx_forecast_cache_generated_at
ON forecast_cache(generated_at);

CREATE TABLE IF NOT EXISTS catch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    location_id TEXT NOT NULL,
    species     TEXT NOT NULL,
    size        TEXT,
    notes       TEXT,
    bait          TEXT,
    rig           TEXT,
    tide_state    TEXT,
    wind_dir      TEXT,
    water_temp_f  REAL,
    moon_phase    TEXT,
    hab_risk      TEXT,
    river_discharge_cfs REAL,
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

CREATE TABLE IF NOT EXISTS species_image_cache (
    species_key TEXT PRIMARY KEY,
    found       INTEGER NOT NULL DEFAULT 0,
    image_json  TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint    TEXT    NOT NULL UNIQUE,
    p256dh      TEXT    NOT NULL,
    auth        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user
ON push_subscriptions(user_id);

CREATE TABLE IF NOT EXISTS notification_log (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    location_id TEXT    NOT NULL,
    sent_date   TEXT    NOT NULL,
    window_label TEXT,
    channel     TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, location_id, sent_date)
);
"""


def get_db() -> sqlite3.Connection:
    """Open and return a new SQLite connection with row-factory set.

    ``journal_mode=WAL`` is a persistent database setting applied once in
    ``init_db()``.  ``foreign_keys=ON`` must be set per-connection (it is a
    connection-level pragma that SQLite resets on every new connection), so it
    remains here.  ``busy_timeout`` is set so that concurrent writers retry for
    up to 2 s before raising OperationalError instead of failing immediately.
    """
    conn = sqlite3.connect(DB_PATH, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # Use memory for temporary tables (avoids disk I/O for intermediate results).
    conn.execute("PRAGMA temp_store=MEMORY")
    # Memory-map up to 128 MB of the DB file for faster sequential reads.
    conn.execute("PRAGMA mmap_size=134217728")
    # In WAL mode synchronous=NORMAL is safe: committed transactions survive OS
    # crashes (the WAL file is synced before the commit returns) while removing
    # the extra fsync that FULL mode adds after every write.
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


_KNOWN_TABLES = frozenset(
    {
        "users",
        "profiles",
        "locations",
        "forecasts",
        "forecast_cache",
        "catch_log",
        "reg_scrape_cache",
        "species_image_cache",
    }
)


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
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
        # Registration always auto-confirms (no email-verification flow exists
        # to unblock an account otherwise), so default existing rows to
        # confirmed too — matching the fresh-schema default below — rather
        # than stranding pre-existing accounts behind a route that doesn't exist.
        conn.execute(
            "ALTER TABLE users ADD COLUMN email_confirmed INTEGER NOT NULL DEFAULT 1"
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
    if "display_name" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
    if "avatar_url" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
    if "is_anonymous" not in user_cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN is_anonymous INTEGER NOT NULL DEFAULT 0"
        )
    if "is_admin" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")

    # Catch-log condition snapshot columns (added for catch-pattern learning).
    if _table_exists(conn, "catch_log"):
        catch_cols = set(_column_names(conn, "catch_log"))
        for col, decl in (
            ("bait", "TEXT"),
            ("rig", "TEXT"),
            ("tide_state", "TEXT"),
            ("wind_dir", "TEXT"),
            ("water_temp_f", "REAL"),
            ("moon_phase", "TEXT"),
            ("hab_risk", "TEXT"),
            ("river_discharge_cfs", "REAL"),
        ):
            if col not in catch_cols:
                conn.execute(f"ALTER TABLE catch_log ADD COLUMN {col} {decl}")

    # Seed the built-in admin account (dev / local use).
    _ADMIN_USERNAME = "admin"
    _ADMIN_PASSWORD = "admin"
    _ADMIN_LOCATION = "wrightsville-beach-nc"
    _ADMIN_PROFILE = json.dumps({
        "completed": True,
        "fishing_types": ["surf", "pier", "bridge"],
        "live_bait": "sometimes",
        "cut_bait": "yes",
        "lures": "no",
        "experience": "intermediate",
        "targets": [],
        "preferred_times": ["anytime"],
        "primary_goal": "exploring",
        "condition_tolerance": "moderate",
        "tide_preference": "any",
        "session_frequency": "monthly",
        "catch_release": "sometimes",
    })
    _admin_row = conn.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
        (_ADMIN_USERNAME,),
    ).fetchone()
    if _admin_row is None:
        _admin_pw = generate_password_hash(_ADMIN_PASSWORD, method="scrypt")
        _admin_cur = conn.execute(
            "INSERT INTO users (username, password_hash, email_confirmed, is_admin, is_anonymous)"
            " VALUES (?, ?, 1, 1, 0)",
            (_ADMIN_USERNAME, _admin_pw),
        )
        _admin_id = _admin_cur.lastrowid
        conn.execute("INSERT OR IGNORE INTO profiles (user_id) VALUES (?)", (_admin_id,))
        conn.execute("INSERT OR IGNORE INTO locations (user_id) VALUES (?)", (_admin_id,))
        conn.execute(
            "UPDATE profiles SET fishing_profile = ?, updated_at = datetime('now') WHERE user_id = ?",
            (_ADMIN_PROFILE, _admin_id),
        )
        conn.execute(
            "UPDATE locations SET location_id = ?, updated_at = datetime('now') WHERE user_id = ?",
            (_ADMIN_LOCATION, _admin_id),
        )
        conn.execute(
            "UPDATE users SET default_location_id = ? WHERE id = ?",
            (_ADMIN_LOCATION, _admin_id),
        )
    else:
        _admin_id = _admin_row["id"]
        conn.execute(
            "UPDATE users SET is_admin = 1 WHERE id = ?",
            (_admin_id,),
        )
    # Revoke admin from every other account so only the seeded admin has it.
    conn.execute(
        "UPDATE users SET is_admin = 0 WHERE id != ?",
        (_admin_id,),
    )

    profile_cols = set(_column_names(conn, "profiles"))
    if "wind_units" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN wind_units TEXT DEFAULT 'knots'")
    if "temp_units" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN temp_units TEXT DEFAULT 'F'")
    if "notification_prefs" not in profile_cols:
        conn.execute(
            "ALTER TABLE profiles ADD COLUMN notification_prefs TEXT DEFAULT '{}'"
        )
    if "favorites" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN favorites TEXT DEFAULT '[]'")
    if "timezone" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN timezone TEXT")
    if "page_layout" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN page_layout TEXT")

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


def create_user(
    username: str, password: str, email: Optional[str] = None
) -> Optional[int]:
    # Explicitly specify the algorithm so we are not dependent on Werkzeug's
    # default changing in a future release.
    pw_hash = generate_password_hash(password, method="scrypt")
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


def authenticate_user(username: str, password: str) -> Optional[dict[str, Any]]:
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
    stored_hash = (
        row["password_hash"] if (row and row["password_hash"]) else _DUMMY_HASH
    )
    password_ok = check_password_hash(stored_hash, password)
    if not password_ok or not row:
        return None
    return {"id": row["id"], "username": row["username"]}


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    entry = _USER_CACHE.get(user_id)
    if entry and _time.time() - entry[0] < _USER_CACHE_TTL:
        return entry[1]
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, username, email, email_confirmed, default_location_id, "
            "session_version, display_name, avatar_url, is_admin "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        _USER_CACHE.pop(user_id, None)
        return None
    result: dict[str, Any] = {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "email_confirmed": bool(row["email_confirmed"]),
        "default_location_id": row["default_location_id"],
        "session_version": row["session_version"],
        "display_name": row["display_name"],
        "avatar_url": row["avatar_url"],
        "is_admin": bool(row["is_admin"]),
    }
    if len(_USER_CACHE) >= _USER_CACHE_MAX:
        _USER_CACHE.pop(next(iter(_USER_CACHE)))
    _USER_CACHE[user_id] = (_time.time(), result)
    return result


def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
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


def confirm_email(user_id: int) -> None:
    """Mark the user's email as confirmed."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET email_confirmed = 1 WHERE id = ?",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()
    _USER_CACHE.pop(user_id, None)


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
        _USER_CACHE.pop(user_id, None)


def change_password(user_id: int, new_password: str) -> int:
    """Hash *new_password* and store it, then bump and return the new session_version.

    Bumping the version invalidates every other active session so the user is
    effectively logged out everywhere except the current device (which receives
    the new version in its cookie immediately after this call).
    """
    pw_hash = generate_password_hash(new_password, method="scrypt")
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET password_hash = ?, session_version = session_version + 1 "
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
        _USER_CACHE.pop(user_id, None)


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
    _USER_CACHE.pop(user_id, None)


# Profiles + locations ------------------------------------------------------


def get_preferences(user_id: int) -> dict[str, Any]:
    entry = _PREFS_CACHE.get(user_id)
    if entry and _time.time() - entry[0] < _PREFS_CACHE_TTL:
        return entry[1]

    conn = get_db()
    try:
        row = conn.execute(
            """
        SELECT l.location_id, p.theme, p.units, p.wind_units, p.temp_units,
               p.notification_prefs, p.fishing_profile, p.favorites, p.timezone
        FROM profiles p
        LEFT JOIN locations l ON l.user_id = p.user_id
        WHERE p.user_id = ?
        """,
            (user_id,),
        ).fetchone()
    finally:
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

    favorites: list[str] = []
    if row["favorites"]:
        try:
            favorites = json.loads(row["favorites"])
        except Exception:
            logger.warning(
                "Corrupt favorites JSON for user_id=%s; resetting to []", user_id
            )
            favorites = []

    notification_prefs: dict[str, Any] = {}
    if row["notification_prefs"]:
        try:
            notification_prefs = json.loads(row["notification_prefs"])
        except Exception:
            logger.warning(
                "Corrupt notification_prefs JSON for user_id=%s; resetting to {}",
                user_id,
            )
            notification_prefs = {}

    result = {
        "location_id": row["location_id"],
        "theme": row["theme"] or "light",
        "units": row["units"] or "F",
        "wind_units": row["wind_units"] or "knots",
        "temp_units": row["temp_units"] or "F",
        "notification_prefs": notification_prefs,
        "fishing_profile": profile,
        "favorites": favorites,
        "timezone": row["timezone"] or "",
    }
    if len(_PREFS_CACHE) >= _PREFS_CACHE_MAX:
        _PREFS_CACHE.pop(next(iter(_PREFS_CACHE)))
    _PREFS_CACHE[user_id] = (_time.time(), result)
    return result


def save_preferences(user_id: int, **kwargs: Any) -> None:
    _PREFS_CACHE.pop(user_id, None)  # invalidate before write
    if "default_location_id" in kwargs:
        _USER_CACHE.pop(user_id, None)
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
        vals: list[Any] = []
        map_fields = {
            "theme": "theme",
            "units": "units",
            "wind_units": "wind_units",
            "temp_units": "temp_units",
            "notification_prefs": "notification_prefs",
            "fishing_profile": "fishing_profile",
            "favorites": "favorites",
            "timezone": "timezone",
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


# Notifications --------------------------------------------------------------


def iter_notification_candidates() -> list[dict[str, Any]]:
    """Return verified users who have a non-empty notification_prefs object.

    Each row: {user_id, email, notification_prefs, fishing_profile, favorites,
    default_location_id, timezone}.  Callers still decide whether each user is
    actually opted in (prefs['enabled']) and how to reach them; this only does
    the cheap SQL-side filtering (confirmed email present, prefs not empty).
    """
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT u.id AS user_id, u.email AS email,
                   u.default_location_id AS default_location_id,
                   p.notification_prefs AS notification_prefs,
                   p.fishing_profile AS fishing_profile,
                   p.favorites AS favorites,
                   p.timezone AS timezone
            FROM users u
            JOIN profiles p ON p.user_id = u.id
            WHERE u.email IS NOT NULL
              AND u.email_confirmed = 1
              AND p.notification_prefs IS NOT NULL
              AND p.notification_prefs NOT IN ('', '{}')
            """
        ).fetchall()
    finally:
        conn.close()

    def _loads(raw: Any, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "user_id": row["user_id"],
                "email": row["email"],
                "default_location_id": row["default_location_id"],
                "notification_prefs": _loads(row["notification_prefs"], {}),
                "fishing_profile": _loads(row["fishing_profile"], {}) or {},
                "favorites": _loads(row["favorites"], []) or [],
                "timezone": row["timezone"] or "",
            }
        )
    return out


def was_notified(user_id: int, location_id: str, sent_date: str) -> bool:
    """True if this user/location was already notified on *sent_date* (YYYY-MM-DD)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM notification_log WHERE user_id = ? AND location_id = ? AND sent_date = ?",
            (user_id, location_id, sent_date),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def record_notification(
    user_id: int,
    location_id: str,
    sent_date: str,
    window_label: str = "",
    channel: str = "",
) -> None:
    """Record that a notification fired (idempotent per user/location/day)."""
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO notification_log
                (user_id, location_id, sent_date, window_label, channel)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, location_id, sent_date, window_label, channel),
        )
        conn.commit()
    finally:
        conn.close()


def add_push_subscription(
    user_id: int, endpoint: str, p256dh: str, auth: str
) -> None:
    """Store (or refresh) a Web Push subscription for a user."""
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id = excluded.user_id,
                p256dh = excluded.p256dh,
                auth = excluded.auth
            """,
            (user_id, endpoint, p256dh, auth),
        )
        conn.commit()
    finally:
        conn.close()


def get_push_subscriptions(user_id: int) -> list[dict[str, str]]:
    """Return all stored Web Push subscriptions for a user."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"endpoint": r["endpoint"], "p256dh": r["p256dh"], "auth": r["auth"]}
        for r in rows
    ]


def delete_push_subscription(endpoint: str) -> None:
    """Remove a single push subscription by endpoint (e.g. after a 410 Gone)."""
    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
        conn.commit()
    finally:
        conn.close()


# Page layout ----------------------------------------------------------------


def get_page_layout(user_id: int) -> Optional[list[Any]]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT page_layout FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["page_layout"]:
        return None
    try:
        return json.loads(row["page_layout"])
    except Exception:
        return None


def save_page_layout(user_id: int, layout: list[Any]) -> None:
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO profiles (user_id) VALUES (?)", (user_id,))
        conn.execute(
            "UPDATE profiles SET page_layout = ?, updated_at = datetime('now') WHERE user_id = ?",
            (json.dumps(layout), user_id),
        )
        conn.commit()
    finally:
        conn.close()


# Catch log -----------------------------------------------------------------


def get_log_entries(
    user_id: int, location_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, species, size, notes, bait, rig, caught_at FROM catch_log "
            "WHERE user_id = ? AND location_id = ? ORDER BY caught_at DESC, id DESC LIMIT ?",
            (user_id, location_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "species": r["species"],
            "size": r["size"],
            "notes": r["notes"],
            "bait": r["bait"],
            "rig": r["rig"],
            "date": r["caught_at"],
        }
        for r in rows
    ]


def get_catch_conditions(
    user_id: int, location_id: str = "", limit: int = 500
) -> list[dict[str, Any]]:
    """Return catch-log rows with their captured condition snapshot.

    When *location_id* is empty, returns catches across all of the user's
    locations (for cross-location pattern analysis).
    """
    conn = get_db()
    try:
        if location_id:
            rows = conn.execute(
                "SELECT species, bait, rig, tide_state, wind_dir, water_temp_f, moon_phase, "
                "hab_risk, river_discharge_cfs, "
                "caught_at FROM catch_log WHERE user_id = ? AND location_id = ? "
                "ORDER BY caught_at DESC, id DESC LIMIT ?",
                (user_id, location_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT species, bait, rig, tide_state, wind_dir, water_temp_f, moon_phase, "
                "hab_risk, river_discharge_cfs, "
                "caught_at FROM catch_log WHERE user_id = ? "
                "ORDER BY caught_at DESC, id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
    finally:
        conn.close()
    return [
        {
            "species": r["species"],
            "bait": r["bait"],
            "rig": r["rig"],
            "tide_state": r["tide_state"],
            "wind_dir": r["wind_dir"],
            "water_temp_f": r["water_temp_f"],
            "moon_phase": r["moon_phase"],
            "hab_risk": r["hab_risk"],
            "river_discharge_cfs": r["river_discharge_cfs"],
            "date": r["caught_at"],
        }
        for r in rows
    ]


_CATCH_LOG_SIZE_MAX = 50  # e.g. "24 inches"
_CATCH_LOG_NOTES_MAX = 1000  # free-text field
_CATCH_LOG_BAIT_MAX = 60  # e.g. "live shrimp"


def get_recent_catch_activity(
    location_id: str, days: int = 7, min_contributors: int = 3
) -> Optional[dict[str, Any]]:
    """Aggregate recent shared catches at a location (privacy-preserving).

    Only counts catches from users who opted in (``fishing_profile.share_catches``)
    and only returns anything when at least *min_contributors* distinct anglers
    contributed — k-anonymity so no single user's activity is identifiable.
    Returns ``None`` when the threshold isn't met. The result never exposes
    individual users: just totals and the top species.
    """
    if not location_id:
        return None
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT cl.user_id AS uid, cl.species AS species
            FROM catch_log cl
            JOIN profiles p ON p.user_id = cl.user_id
            WHERE cl.location_id = ?
              AND cl.caught_at >= datetime('now', ?)
              AND json_extract(p.fishing_profile, '$.share_catches') = 1
            """,
            (location_id, f"-{int(days)} days"),
        ).fetchall()
    finally:
        conn.close()

    contributors = {r["uid"] for r in rows}
    if len(contributors) < min_contributors:
        return None

    species_counts: dict[str, int] = {}
    for r in rows:
        name = (r["species"] or "").strip()
        if name:
            species_counts[name] = species_counts.get(name, 0) + 1
    top = sorted(species_counts.items(), key=lambda kv: -kv[1])[:5]

    return {
        "count": len(rows),
        "contributors": len(contributors),
        "days": int(days),
        "top_species": [{"species": n, "count": c} for n, c in top],
    }


def add_log_entry(
    user_id: int,
    location_id: str,
    species: str,
    size: str = "",
    notes: str = "",
    bait: str = "",
    rig: str = "",
    conditions: Optional[dict[str, Any]] = None,
) -> int:
    """Insert a catch-log entry, optionally snapshotting the conditions.

    *conditions* (when supplied) captures the forecast at catch time so the
    pattern-analysis can later correlate catches with tide/wind/temp/moon.
    Recognized keys: tide_state, wind_dir, water_temp_f, moon_phase, hab_risk,
    river_discharge_cfs.
    """
    c = conditions or {}
    tide_state = (str(c.get("tide_state") or "")[:20]) or None
    wind_dir = (str(c.get("wind_dir") or "")[:8]) or None
    moon_phase = (str(c.get("moon_phase") or "")[:32]) or None
    hab_risk = (str(c.get("hab_risk") or "")[:16]) or None
    bait_val = (bait.strip()[:_CATCH_LOG_BAIT_MAX]) or None
    rig_val = (rig.strip()[:_CATCH_LOG_BAIT_MAX]) or None
    try:
        water_temp_f = (
            float(c["water_temp_f"]) if c.get("water_temp_f") is not None else None
        )
    except (TypeError, ValueError):
        water_temp_f = None
    try:
        river_discharge_cfs = (
            float(c["river_discharge_cfs"])
            if c.get("river_discharge_cfs") is not None
            else None
        )
    except (TypeError, ValueError):
        river_discharge_cfs = None

    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO catch_log "
            "(user_id, location_id, species, size, notes, bait, rig, tide_state, "
            "wind_dir, water_temp_f, moon_phase, hab_risk, river_discharge_cfs) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                location_id,
                species.strip()[:100],
                size.strip()[:_CATCH_LOG_SIZE_MAX],
                notes.strip()[:_CATCH_LOG_NOTES_MAX],
                bait_val,
                rig_val,
                tide_state,
                wind_dir,
                water_temp_f,
                moon_phase,
                hab_risk,
                river_discharge_cfs,
            ),
        )
        conn.commit()
        entry_id = cur.lastrowid or 0
    finally:
        conn.close()
    _LOG_STATS_CACHE.pop((user_id, location_id), None)
    return entry_id


def delete_log_entry(user_id: int, entry_id: int) -> bool:
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM catch_log WHERE id = ? AND user_id = ?", (entry_id, user_id)
        )
        conn.commit()
        ok = cur.rowcount > 0
    finally:
        conn.close()
    for k in [k for k in _LOG_STATS_CACHE if k[0] == user_id]:
        _LOG_STATS_CACHE.pop(k, None)
    return ok


def get_log_stats(user_id: int, location_id: str) -> dict[str, Any]:
    """Return aggregate statistics for a user's catch log at a location.

    Uses a single query with a CTE to avoid 4 separate roundtrips to SQLite.
    Results are cached for 2 minutes so repeated dashboard renders don't re-query.
    """
    cache_key = (user_id, location_id)
    entry = _LOG_STATS_CACHE.get(cache_key)
    if entry and _time.time() - entry[0] < _LOG_STATS_CACHE_TTL:
        return entry[1]

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

    result = {
        "total": total,
        "unique_species": len(species_rows),
        "top_species": species_rows[0]["species"] if species_rows else None,
        "last_date": last_date,
        "species_breakdown": [
            {"species": r["species"], "count": r["cnt"]} for r in species_rows[:10]
        ],
        "monthly_counts": monthly_counts,
    }
    if len(_LOG_STATS_CACHE) >= _LOG_STATS_CACHE_MAX:
        _LOG_STATS_CACHE.pop(next(iter(_LOG_STATS_CACHE)))
    _LOG_STATS_CACHE[cache_key] = (_time.time(), result)
    return result


def get_recent_logs(user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, location_id, species, size, notes, caught_at FROM catch_log "
            "WHERE user_id = ? ORDER BY caught_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "location_id": r["location_id"],
            "species": r["species"],
            "size": r["size"],
            "notes": r["notes"],
            "date": r["caught_at"],
        }
        for r in rows
    ]


# Forecast cache -------------------------------------------------------------


def save_forecast_to_db(location_id: str, data: dict[str, Any]) -> None:
    if not location_id:
        return
    generated_at = data.get("generated_at") or datetime.utcnow().isoformat()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO forecasts (location_id, forecast_json, generated_at) VALUES (?, ?, ?)",
            (location_id, json.dumps(data), generated_at),
        )
        conn.commit()
    finally:
        conn.close()


def save_forecast_cache(user_id: int, location_id: str, data: dict[str, Any]) -> None:
    if not location_id:
        return
    generated_at = data.get("generated_at") or datetime.utcnow().isoformat()
    conn = get_db()
    try:
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
    finally:
        conn.close()


def load_forecast_cache(user_id: int, location_id: str) -> Optional[dict[str, Any]]:
    if not location_id:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT forecast_json FROM forecast_cache WHERE user_id = ? AND location_id = ?",
            (user_id, location_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row["forecast_json"])
    except Exception:
        return None


def load_forecast_cache_for_user(
    user_id: int, location_id: str
) -> Optional[dict[str, Any]]:
    """Load forecast cache preferring user-specific row, falling back to anonymous.

    Combines the two separate load_forecast_cache(uid) + load_forecast_cache(0)
    calls in storage.cache into a single DB connection + single query.

    Returns the user-specific forecast if it exists; otherwise the anonymous
    (user_id=0) one; otherwise None.  Anonymous users (user_id=0) just execute
    a plain equality match.
    """
    if not location_id:
        return None
    conn = get_db()
    try:
        if user_id == 0:
            row = conn.execute(
                "SELECT forecast_json FROM forecast_cache "
                "WHERE user_id = 0 AND location_id = ?",
                (location_id,),
            ).fetchone()
        else:
            # Fetch both rows in one pass; user-specific row sorts first.
            row = conn.execute(
                "SELECT forecast_json FROM forecast_cache "
                "WHERE location_id = ? AND user_id IN (?, 0) "
                "ORDER BY (user_id = ?) DESC LIMIT 1",
                (location_id, user_id, user_id),
            ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row["forecast_json"])
    except Exception:
        return None


def delete_forecast_cache(user_id: int, location_id: str) -> bool:
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM forecast_cache WHERE user_id = ? AND location_id = ?",
            (user_id, location_id),
        )
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
        conn.close()
    return deleted


def prune_forecast_cache(max_age_days: int = 7) -> int:
    """Delete forecast_cache rows whose generated_at is older than max_age_days.

    Returns the number of rows removed.  Safe to call at startup — the DELETE
    is indexed on generated_at and completes in milliseconds even on large DBs.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM forecast_cache WHERE generated_at < datetime('now', ?)",
            (f"-{max_age_days} days",),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def load_forecast(location_id: str) -> Optional[dict[str, Any]]:
    if not location_id:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT forecast_json FROM forecasts WHERE location_id = ? "
            "ORDER BY generated_at DESC, id DESC LIMIT 1",
            (location_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row["forecast_json"])
    except Exception:
        return None


def list_cached_locations() -> list[dict[str, str]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT location_id, MAX(generated_at) AS generated_at, MAX(created_at) AS updated_at "
            "FROM forecasts GROUP BY location_id ORDER BY MAX(created_at) DESC"
        ).fetchall()
    finally:
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
    try:
        cur = conn.execute(
            "DELETE FROM forecasts WHERE location_id = ?", (location_id,)
        )
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
        conn.close()
    return deleted

