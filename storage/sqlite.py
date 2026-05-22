"""SQLite data-access layer for users, profiles, locations, forecasts, and catch logs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading as _threading
import time as _time
from datetime import datetime, timezone
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
    email_confirmed INTEGER NOT NULL DEFAULT 0,
    email_verification_token TEXT,
    email_verification_sent_at TEXT,
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

CREATE TABLE IF NOT EXISTS map_catches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lat         REAL NOT NULL,
    lng         REAL NOT NULL,
    species     TEXT NOT NULL,
    title       TEXT,
    bait        TEXT,
    weight_lb   REAL,
    length_in   REAL,
    notes       TEXT,
    image_url   TEXT,
    caught_at   TEXT NOT NULL DEFAULT (datetime('now')),
    is_public   INTEGER NOT NULL DEFAULT 1,
    likes_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_map_catches_user
ON map_catches(user_id, caught_at DESC);
CREATE INDEX IF NOT EXISTS idx_map_catches_public_time
ON map_catches(is_public, caught_at DESC);
CREATE INDEX IF NOT EXISTS idx_map_catches_bbox
ON map_catches(lat, lng);
CREATE INDEX IF NOT EXISTS idx_map_catches_bbox_time
ON map_catches(lat, lng, caught_at DESC);
CREATE INDEX IF NOT EXISTS idx_map_catches_public_time_geo
ON map_catches(is_public, caught_at DESC, lat, lng);

CREATE TABLE IF NOT EXISTS map_catch_comments (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    catch_id  INTEGER NOT NULL REFERENCES map_catches(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_map_catch_comments_catch
ON map_catch_comments(catch_id, created_at ASC);

CREATE TABLE IF NOT EXISTS map_catch_likes (
    catch_id INTEGER NOT NULL REFERENCES map_catches(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (catch_id, user_id)
);

CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_id TEXT NOT NULL UNIQUE,
    public_key    TEXT NOT NULL,
    sign_count    INTEGER NOT NULL DEFAULT 0,
    name          TEXT NOT NULL DEFAULT 'Passkey',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_webauthn_user
ON webauthn_credentials(user_id);

CREATE TABLE IF NOT EXISTS social_accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider      TEXT NOT NULL,
    provider_uid  TEXT NOT NULL,
    email         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider, provider_uid)
);
CREATE INDEX IF NOT EXISTS idx_social_accounts_user
ON social_accounts(user_id);

CREATE TABLE IF NOT EXISTS custom_map_markers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lat         REAL    NOT NULL,
    lng         REAL    NOT NULL,
    name        TEXT    NOT NULL DEFAULT '',
    type        TEXT    NOT NULL DEFAULT 'fishing',
    description TEXT    NOT NULL DEFAULT '',
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS suppressed_map_spots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    spot_key    TEXT    NOT NULL UNIQUE,
    lat         REAL    NOT NULL,
    lng         REAL    NOT NULL,
    type        TEXT    NOT NULL DEFAULT '',
    name        TEXT    NOT NULL DEFAULT '',
    suppressed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_suppressed_spots_bbox
ON suppressed_map_spots(lat, lng);

CREATE TABLE IF NOT EXISTS custom_habitats (
    id            TEXT    PRIMARY KEY,
    habitat_type  TEXT    NOT NULL DEFAULT 'general',
    name          TEXT    NOT NULL DEFAULT '',
    description   TEXT    NOT NULL DEFAULT '',
    fill_color    TEXT    NOT NULL DEFAULT '',
    geometry_json TEXT    NOT NULL DEFAULT '{}',
    lat           REAL,
    lng           REAL,
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_custom_habitats_bbox
ON custom_habitats(lat, lng, is_deleted);

CREATE TABLE IF NOT EXISTS habitat_overrides (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_key TEXT    NOT NULL UNIQUE,
    name        TEXT,
    description TEXT,
    fill_color  TEXT,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS custom_habitat_types (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    slug          TEXT    NOT NULL UNIQUE,
    default_color TEXT    NOT NULL DEFAULT '#8b5cf6',
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    is_deleted    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
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
        "webauthn_credentials",
        "social_accounts",
        "map_catches",
        "map_catch_comments",
        "map_catch_likes",
        "custom_map_markers",
        "suppressed_map_spots",
        "custom_habitats",
        "habitat_overrides",
        "custom_habitat_types",
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

    # Create custom_map_markers if it didn't exist before the SCHEMA ran it.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS custom_map_markers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            lat         REAL    NOT NULL,
            lng         REAL    NOT NULL,
            name        TEXT    NOT NULL DEFAULT '',
            type        TEXT    NOT NULL DEFAULT 'fishing',
            description TEXT    NOT NULL DEFAULT '',
            created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
            is_deleted  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS suppressed_map_spots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            spot_key    TEXT    NOT NULL UNIQUE,
            lat         REAL    NOT NULL,
            lng         REAL    NOT NULL,
            type        TEXT    NOT NULL DEFAULT '',
            name        TEXT    NOT NULL DEFAULT '',
            suppressed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_suppressed_spots_bbox
        ON suppressed_map_spots(lat, lng);
        """
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

    # webauthn_credentials table (added for passkey / biometric login support)
    if not _table_exists(conn, "webauthn_credentials"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS webauthn_credentials (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                credential_id TEXT NOT NULL UNIQUE,
                public_key    TEXT NOT NULL,
                sign_count    INTEGER NOT NULL DEFAULT 0,
                name          TEXT NOT NULL DEFAULT 'Passkey',
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_webauthn_user
            ON webauthn_credentials(user_id);
            """
        )

    # social_accounts table (added for Google / Apple social login support)
    if not _table_exists(conn, "social_accounts"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS social_accounts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider      TEXT NOT NULL,
                provider_uid  TEXT NOT NULL,
                email         TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(provider, provider_uid)
            );
            CREATE INDEX IF NOT EXISTS idx_social_accounts_user
            ON social_accounts(user_id);
            """
        )

    # map_catches + social tables (community catch logging on the map)
    if not _table_exists(conn, "map_catches"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS map_catches (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                lat         REAL NOT NULL,
                lng         REAL NOT NULL,
                species     TEXT NOT NULL,
                title       TEXT,
                bait        TEXT,
                weight_lb   REAL,
                length_in   REAL,
                notes       TEXT,
                image_url   TEXT,
                caught_at   TEXT NOT NULL DEFAULT (datetime('now')),
                is_public   INTEGER NOT NULL DEFAULT 1,
                likes_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_map_catches_user
            ON map_catches(user_id, caught_at DESC);
            CREATE INDEX IF NOT EXISTS idx_map_catches_public_time
            ON map_catches(is_public, caught_at DESC);
            CREATE INDEX IF NOT EXISTS idx_map_catches_bbox
            ON map_catches(lat, lng);
            CREATE INDEX IF NOT EXISTS idx_map_catches_bbox_time
            ON map_catches(lat, lng, caught_at DESC);

            CREATE TABLE IF NOT EXISTS map_catch_comments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                catch_id   INTEGER NOT NULL REFERENCES map_catches(id) ON DELETE CASCADE,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                body       TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_map_catch_comments_catch
            ON map_catch_comments(catch_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS map_catch_likes (
                catch_id INTEGER NOT NULL REFERENCES map_catches(id) ON DELETE CASCADE,
                user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                PRIMARY KEY (catch_id, user_id)
            );
            """
        )
    else:
        # map_catches already existed — add new columns if they were introduced
        # after the initial schema migration (idempotent: ignore if already present).
        existing_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(map_catches)").fetchall()
        }
        if "title" not in existing_cols:
            conn.execute("ALTER TABLE map_catches ADD COLUMN title TEXT")
        if "image_url" not in existing_cols:
            conn.execute("ALTER TABLE map_catches ADD COLUMN image_url TEXT")
        conn.commit()


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


def get_user_by_verification_token(token: str) -> Optional[dict[str, Any]]:
    """Return the user matching *token* if the token was sent within 2 hours.

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


def get_all_user_photo_paths(user_id: int) -> list[str]:
    """Return every stored photo path for *user_id* across all catch-log entries."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT photo1_path, photo2_path FROM catch_log WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    paths: list[str] = []
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
            "SELECT id, species, size, notes, caught_at, photo1_path, photo2_path FROM catch_log "
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
            "date": r["caught_at"],
            "photo1_path": r["photo1_path"],
            "photo2_path": r["photo2_path"],
        }
        for r in rows
    ]


_CATCH_LOG_SIZE_MAX = 50  # e.g. "24 inches"
_CATCH_LOG_NOTES_MAX = 1000  # free-text field


def add_log_entry(
    user_id: int, location_id: str, species: str, size: str = "", notes: str = ""
) -> int:
    conn = get_db()
    try:
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


def get_entry_photo_paths(
    user_id: int, entry_id: int
) -> Optional[tuple[Optional[str], Optional[str]]]:
    """Return (photo1_path, photo2_path) for the entry, or None if entry not found."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT photo1_path, photo2_path FROM catch_log WHERE id = ? AND user_id = ?",
            (entry_id, user_id),
        ).fetchone()
    finally:
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
            "SELECT id, location_id, species, size, notes, caught_at, photo1_path, photo2_path FROM catch_log "
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
            "photo1_path": r["photo1_path"],
            "photo2_path": r["photo2_path"],
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


# ── WebAuthn / passkey credential storage ─────────────────────────────────────


def save_webauthn_credential(
    user_id: int,
    credential_id: str,
    public_key: str,
    sign_count: int,
    name: str = "Passkey",
) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
        INSERT INTO webauthn_credentials (user_id, credential_id, public_key, sign_count, name)
        VALUES (?, ?, ?, ?, ?)
        """,
            (user_id, credential_id, public_key, sign_count, name),
        )
        conn.commit()
    finally:
        conn.close()


def get_webauthn_credentials(user_id: int) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM webauthn_credentials WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_account_credentials(user_id: int) -> dict[str, Any]:
    """Return passkeys and linked social accounts in one DB round-trip.

    Both tables are owned by the same user and rarely written to, so fetching
    them together halves the number of connections opened on the account page.
    """
    conn = get_db()
    try:
        passkey_rows = conn.execute(
            "SELECT * FROM webauthn_credentials WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()
        social_rows = conn.execute(
            "SELECT provider, email, created_at FROM social_accounts WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "passkeys": [dict(r) for r in passkey_rows],
        "social_accounts": [
            {
                "provider": r["provider"],
                "email": r["email"],
                "created_at": r["created_at"],
            }
            for r in social_rows
        ],
    }


def get_webauthn_credential_by_id(credential_id: str) -> Optional[dict[str, Any]]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM webauthn_credentials WHERE credential_id = ?",
            (credential_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def update_webauthn_sign_count(credential_id: str, sign_count: int) -> None:
    conn = get_db()
    try:
        conn.execute(
            "UPDATE webauthn_credentials SET sign_count = ? WHERE credential_id = ?",
            (sign_count, credential_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_webauthn_credential(credential_id: str, user_id: int) -> bool:
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM webauthn_credentials WHERE credential_id = ? AND user_id = ?",
            (credential_id, user_id),
        )
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
        conn.close()
    return deleted


# Social login (Google / Apple OAuth) --------------------------------------


def get_social_account(provider: str, provider_uid: str) -> Optional[dict[str, Any]]:
    """Return the user_id linked to a social provider account, or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT user_id FROM social_accounts WHERE provider = ? AND provider_uid = ?",
            (provider, provider_uid),
        ).fetchone()
    finally:
        conn.close()
    return {"user_id": row["user_id"]} if row else None


def link_social_account(
    user_id: int, provider: str, provider_uid: str, email: Optional[str] = None
) -> None:
    """Link an existing user account to a social provider."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO social_accounts (user_id, provider, provider_uid, email) "
            "VALUES (?, ?, ?, ?)",
            (user_id, provider, provider_uid, email),
        )
        conn.commit()
    finally:
        conn.close()


def create_social_user(
    username: str,
    email: Optional[str],
    provider: str,
    provider_uid: str,
    display_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> Optional[int]:
    """Create a new user for social login (no password) and link the social account.

    The user is created with email_confirmed=1 because the identity provider has
    already verified the email address.
    """
    email_val = email.strip().lower() if email else None
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO users "
            "(username, password_hash, email, email_confirmed, is_anonymous, display_name, avatar_url) "
            "VALUES (?, NULL, ?, 1, 0, ?, ?)",
            (username.strip(), email_val, display_name, avatar_url),
        )
        user_id = cur.lastrowid
        conn.execute("INSERT OR IGNORE INTO profiles (user_id) VALUES (?)", (user_id,))
        conn.execute("INSERT OR IGNORE INTO locations (user_id) VALUES (?)", (user_id,))
        conn.execute(
            "INSERT INTO social_accounts (user_id, provider, provider_uid, email) "
            "VALUES (?, ?, ?, ?)",
            (user_id, provider, provider_uid, email_val),
        )
        conn.commit()
        return user_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def update_user_social_profile(
    user_id: int,
    display_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> None:
    """Update display_name and/or avatar_url for an existing social login user.

    Only overwrites fields that are currently NULL — preserves any display name
    the user may have set themselves.
    """
    conn = get_db()
    try:
        if display_name:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ? AND display_name IS NULL",
                (display_name, user_id),
            )
        if avatar_url:
            conn.execute(
                "UPDATE users SET avatar_url = ? WHERE id = ? AND avatar_url IS NULL",
                (avatar_url, user_id),
            )
        conn.commit()
    finally:
        conn.close()


def get_social_accounts_for_user(user_id: int) -> list[dict[str, Any]]:
    """Return all social accounts linked to a user."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT provider, email, created_at FROM social_accounts WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"provider": r["provider"], "email": r["email"], "created_at": r["created_at"]}
        for r in rows
    ]


# ── Map catch log (community pins) ──────────────────────────────────────────

_MAP_CATCH_NOTES_MAX = 500
_MAP_CATCH_SPECIES_MAX = 100
_MAP_CATCH_BAIT_MAX = 80
_MAP_CATCH_TITLE_MAX = 120
_MAP_CATCH_IMAGE_URL_MAX = 500


def add_map_catch(
    user_id: int,
    lat: float,
    lng: float,
    species: str,
    *,
    title: str = "",
    bait: str = "",
    weight_lb: Optional[float] = None,
    length_in: Optional[float] = None,
    notes: str = "",
    image_url: str = "",
    is_public: bool = True,
    caught_at: Optional[str] = None,
) -> int:
    """Insert a map catch pin and return its new id."""
    conn = get_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO map_catches
              (user_id, lat, lng, species, title, bait, weight_lb, length_in,
               notes, image_url, is_public, caught_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
            """,
            (
                user_id,
                round(lat, 6),
                round(lng, 6),
                species.strip()[:_MAP_CATCH_SPECIES_MAX],
                title.strip()[:_MAP_CATCH_TITLE_MAX] if title else None,
                bait.strip()[:_MAP_CATCH_BAIT_MAX] if bait else None,
                weight_lb,
                length_in,
                notes.strip()[:_MAP_CATCH_NOTES_MAX] if notes else None,
                image_url.strip()[:_MAP_CATCH_IMAGE_URL_MAX] if image_url else None,
                1 if is_public else 0,
                caught_at,
            ),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def get_map_catches_in_bbox(
    sw_lat: float,
    sw_lng: float,
    ne_lat: float,
    ne_lng: float,
    *,
    viewer_user_id: Optional[int] = None,
    limit: int = 200,
    species_filter: str = "",
    days_back: int = 90,
) -> list[dict[str, Any]]:
    """Return public catch pins in a bounding box, plus the viewer's own private ones."""
    conn = get_db()
    try:
        # days_back is a positive integer; negate it for the SQLite date modifier
        lookback = f"-{abs(days_back)} days"
        params: list[Any] = [sw_lat, ne_lat, sw_lng, ne_lng, lookback]
        sql = """
            SELECT mc.id, mc.user_id, mc.lat, mc.lng, mc.species, mc.title,
                   mc.bait, mc.weight_lb, mc.length_in, mc.notes, mc.image_url,
                   mc.caught_at, mc.is_public, mc.likes_count,
                   COALESCE(u.display_name, u.username) AS angler_name
            FROM map_catches mc
            JOIN users u ON u.id = mc.user_id
            WHERE mc.lat BETWEEN ? AND ?
              AND mc.lng BETWEEN ? AND ?
              AND mc.caught_at >= datetime('now', ?)
              AND (mc.is_public = 1
        """
        if viewer_user_id is not None:
            sql += " OR mc.user_id = ?"
            params.append(viewer_user_id)
        sql += ")"

        if species_filter:
            sql += " AND LOWER(mc.species) LIKE ?"
            params.append(f"%{species_filter.lower()}%")

        sql += " ORDER BY mc.caught_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r["id"],
            "user_id": r["user_id"],
            "lat": r["lat"],
            "lng": r["lng"],
            "species": r["species"],
            "title": r["title"],
            "bait": r["bait"],
            "weight_lb": r["weight_lb"],
            "length_in": r["length_in"],
            "notes": r["notes"],
            "image_url": r["image_url"],
            "caught_at": r["caught_at"],
            "is_public": bool(r["is_public"]),
            "likes_count": r["likes_count"],
            "angler_name": r["angler_name"],
            "mine": viewer_user_id is not None and r["user_id"] == viewer_user_id,
        }
        for r in rows
    ]


def get_map_catch(catch_id: int) -> Optional[dict[str, Any]]:
    """Return a single catch record by id, or None."""
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT mc.*, COALESCE(u.display_name, u.username) AS angler_name
            FROM map_catches mc JOIN users u ON u.id = mc.user_id
            WHERE mc.id = ?
            """,
            (catch_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def delete_map_catch(catch_id: int, user_id: int) -> bool:
    """Delete a catch pin owned by user_id. Returns True if a row was removed."""
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM map_catches WHERE id = ? AND user_id = ?",
            (catch_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def toggle_map_catch_like(catch_id: int, user_id: int) -> tuple[bool, int]:
    """Toggle a like on a catch.  Returns (liked: bool, new_likes_count: int)."""
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT 1 FROM map_catch_likes WHERE catch_id = ? AND user_id = ?",
            (catch_id, user_id),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM map_catch_likes WHERE catch_id = ? AND user_id = ?",
                (catch_id, user_id),
            )
            conn.execute(
                "UPDATE map_catches SET likes_count = MAX(0, likes_count - 1) WHERE id = ?",
                (catch_id,),
            )
            liked = False
        else:
            conn.execute(
                "INSERT OR IGNORE INTO map_catch_likes (catch_id, user_id) VALUES (?, ?)",
                (catch_id, user_id),
            )
            conn.execute(
                "UPDATE map_catches SET likes_count = likes_count + 1 WHERE id = ?",
                (catch_id,),
            )
            liked = True
        conn.commit()
        row = conn.execute(
            "SELECT likes_count FROM map_catches WHERE id = ?", (catch_id,)
        ).fetchone()
        count = row["likes_count"] if row else 0
    finally:
        conn.close()
    return liked, count


def get_map_catch_comments(catch_id: int) -> list[dict[str, Any]]:
    """Return all comments on a catch pin, oldest first."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT mcc.id, mcc.catch_id, mcc.user_id, mcc.body, mcc.created_at,
                   COALESCE(u.display_name, u.username) AS angler_name
            FROM map_catch_comments mcc
            JOIN users u ON u.id = mcc.user_id
            WHERE mcc.catch_id = ?
            ORDER BY mcc.created_at ASC
            """,
            (catch_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "catch_id": r["catch_id"],
            "user_id": r["user_id"],
            "body": r["body"],
            "created_at": r["created_at"],
            "angler_name": r["angler_name"],
        }
        for r in rows
    ]


def add_map_catch_comment(catch_id: int, user_id: int, body: str) -> int:
    """Insert a comment on a catch pin and return its id."""
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO map_catch_comments (catch_id, user_id, body) VALUES (?, ?, ?)",
            (catch_id, user_id, body.strip()[:500]),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


_HOTSPOTS_CACHE: dict[tuple, dict[str, Any]] = {}
_HOTSPOTS_CACHE_TTL: float = 300.0  # 5 minutes
_HOTSPOTS_CACHE_LOCK = _threading.Lock()


def get_community_hotspots(
    days_back: int = 30,
    limit: int = 10,
    coast: str = "",
) -> list[dict[str, Any]]:
    """Return top catch locations aggregated over the last N days.

    Groups catch pins by rounded lat/lng (0.1° grid ~ 6 mi) so nearby catches
    cluster into a single hotspot rather than showing individual pins.
    Results are cached in-process for 5 minutes to avoid running the GROUP BY
    aggregate on every map load from concurrent users.
    """
    cache_key = (days_back, limit, coast)
    now = _time.time()
    entry = _HOTSPOTS_CACHE.get(cache_key)
    if entry and now - entry["ts"] < _HOTSPOTS_CACHE_TTL:
        return entry["data"]

    with _HOTSPOTS_CACHE_LOCK:
        entry = _HOTSPOTS_CACHE.get(cache_key)
        if entry and now - entry["ts"] < _HOTSPOTS_CACHE_TTL:
            return entry["data"]

        conn = get_db()
        try:
            lookback = f"-{abs(days_back)} days"
            params: list[Any] = [lookback]
            sql = """
                SELECT
                    ROUND(lat, 1) AS grid_lat,
                    ROUND(lng, 1) AS grid_lng,
                    COUNT(*) AS catch_count,
                    SUM(likes_count) AS total_likes,
                    GROUP_CONCAT(DISTINCT species) AS species_list,
                    MAX(caught_at) AS last_catch_at,
                    AVG(weight_lb) AS avg_weight
                FROM map_catches
                WHERE is_public = 1
                  AND caught_at >= datetime('now', ?)
                GROUP BY grid_lat, grid_lng
                ORDER BY catch_count DESC, total_likes DESC
                LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        result = [
            {
                "lat": r["grid_lat"],
                "lng": r["grid_lng"],
                "catch_count": r["catch_count"],
                "total_likes": r["total_likes"] or 0,
                "species": (r["species_list"] or "").split(",")[:5],
                "last_catch_at": r["last_catch_at"],
                "avg_weight": round(r["avg_weight"], 1) if r["avg_weight"] else None,
            }
            for r in rows
        ]
        _HOTSPOTS_CACHE[cache_key] = {"ts": now, "data": result}
        return result


def clear_hotspots_cache() -> None:
    """Invalidate the in-process community hotspots cache.  Intended for tests."""
    with _HOTSPOTS_CACHE_LOCK:
        _HOTSPOTS_CACHE.clear()


def get_recent_public_catches(
    limit: int = 20,
    species_filter: str = "",
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius_deg: float = 3.0,
) -> list[dict[str, Any]]:
    """Return the most recent public catch pins, optionally near a point."""
    conn = get_db()
    try:
        params: list[Any] = []
        sql = """
            SELECT mc.id, mc.user_id, mc.lat, mc.lng, mc.species, mc.title,
                   mc.bait, mc.weight_lb, mc.length_in, mc.notes, mc.image_url,
                   mc.caught_at, mc.likes_count,
                   COALESCE(u.display_name, u.username) AS angler_name
            FROM map_catches mc
            JOIN users u ON u.id = mc.user_id
            WHERE mc.is_public = 1
        """
        if lat is not None and lng is not None:
            sql += " AND mc.lat BETWEEN ? AND ? AND mc.lng BETWEEN ? AND ?"
            params += [
                lat - radius_deg,
                lat + radius_deg,
                lng - radius_deg,
                lng + radius_deg,
            ]
        if species_filter:
            sql += " AND LOWER(mc.species) LIKE ?"
            params.append(f"%{species_filter.lower()}%")
        sql += " ORDER BY mc.caught_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r["id"],
            "user_id": r["user_id"],
            "lat": r["lat"],
            "lng": r["lng"],
            "species": r["species"],
            "title": r["title"],
            "bait": r["bait"],
            "weight_lb": r["weight_lb"],
            "length_in": r["length_in"],
            "notes": r["notes"],
            "image_url": r["image_url"],
            "caught_at": r["caught_at"],
            "likes_count": r["likes_count"],
            "angler_name": r["angler_name"],
        }
        for r in rows
    ]


# Custom map markers (admin-editable) ----------------------------------------

VALID_MARKER_TYPES = frozenset(
    {
        "pier",
        "jetty",
        "bridge",
        "reef",
        "oyster_reef",
        "wreck",
        "inlet",
        "marina",
        "shoal",
        "point",
        "beach",
        "grass_flat",
        "tidal_flat",
        "saltmarsh",
        "mangrove",
        "buoy",
        "fishing",
        "fishing_shop",
    }
)


def _marker_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "lat": row["lat"],
        "lng": row["lng"],
        "name": row["name"],
        "type": row["type"],
        "description": row["description"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "custom": True,
    }


_CUSTOM_MARKERS_CACHE: Optional[list[dict[str, Any]]] = None
_CUSTOM_MARKERS_TS: float = 0.0
_CUSTOM_MARKERS_TTL: float = 300.0  # 5 minutes — admin writes are rare
_CUSTOM_MARKERS_LOCK = _threading.Lock()


def _invalidate_custom_markers_cache() -> None:
    global _CUSTOM_MARKERS_TS
    with _CUSTOM_MARKERS_LOCK:
        _CUSTOM_MARKERS_TS = 0.0


def get_custom_markers() -> list[dict[str, Any]]:
    """Return all non-deleted custom map markers, from in-memory cache when fresh."""
    global _CUSTOM_MARKERS_CACHE, _CUSTOM_MARKERS_TS
    now = _time.monotonic()
    with _CUSTOM_MARKERS_LOCK:
        if (
            _CUSTOM_MARKERS_CACHE is not None
            and now - _CUSTOM_MARKERS_TS < _CUSTOM_MARKERS_TTL
        ):
            return _CUSTOM_MARKERS_CACHE

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, lat, lng, name, type, description, created_by, created_at, updated_at "
            "FROM custom_map_markers WHERE is_deleted = 0 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    result = [_marker_row_to_dict(r) for r in rows]
    with _CUSTOM_MARKERS_LOCK:
        _CUSTOM_MARKERS_CACHE = result
        _CUSTOM_MARKERS_TS = _time.monotonic()
    return result


def create_custom_marker(
    lat: float, lng: float, name: str, type_: str, description: str, user_id: int
) -> dict[str, Any]:
    """Insert a new custom marker and return it."""
    if type_ not in VALID_MARKER_TYPES:
        type_ = "fishing"
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO custom_map_markers (lat, lng, name, type, description, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (lat, lng, name.strip(), type_, description.strip(), user_id),
        )
        row = conn.execute(
            "SELECT id, lat, lng, name, type, description, created_by, created_at, updated_at "
            "FROM custom_map_markers WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(
            f"custom marker INSERT succeeded but SELECT returned no row (lastrowid={cur.lastrowid})"
        )
    _invalidate_custom_markers_cache()
    return _marker_row_to_dict(row)


def update_custom_marker(
    marker_id: int,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    name: Optional[str] = None,
    type_: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Update fields on an existing custom marker; returns the updated dict or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM custom_map_markers WHERE id = ? AND is_deleted = 0",
            (marker_id,),
        ).fetchone()
        if not row:
            return None
        updates: list[str] = []
        params: list[Any] = []
        if lat is not None:
            updates.append("lat = ?")
            params.append(lat)
        if lng is not None:
            updates.append("lng = ?")
            params.append(lng)
        if name is not None:
            updates.append("name = ?")
            params.append(name.strip())
        if type_ is not None and type_ in VALID_MARKER_TYPES:
            updates.append("type = ?")
            params.append(type_)
        if description is not None:
            updates.append("description = ?")
            params.append(description.strip())
        updates.append("updated_at = datetime('now')")
        params.append(marker_id)
        conn.execute(
            f"UPDATE custom_map_markers SET {', '.join(updates)} WHERE id = ? AND is_deleted = 0",
            params,
        )
        updated = conn.execute(
            "SELECT id, lat, lng, name, type, description, created_by, created_at, updated_at "
            "FROM custom_map_markers WHERE id = ? AND is_deleted = 0",
            (marker_id,),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    if updated is None:
        return None
    _invalidate_custom_markers_cache()
    return _marker_row_to_dict(updated)


def delete_custom_marker(marker_id: int) -> bool:
    """Soft-delete a custom marker; returns True if a row was affected."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE custom_map_markers SET is_deleted = 1, updated_at = datetime('now') "
            "WHERE id = ? AND is_deleted = 0",
            (marker_id,),
        )
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount > 0:
        _invalidate_custom_markers_cache()
    return cur.rowcount > 0


# Suppressed map spots (admin-hidden OSM/NOAA spots) -------------------------

_SUPPRESSED_SPOTS_CACHE: Optional[list[dict[str, Any]]] = None
_SUPPRESSED_SPOTS_TS: float = 0.0
_SUPPRESSED_SPOTS_TTL = 300.0  # 5 minutes
_SUPPRESSED_SPOTS_LOCK = _threading.Lock()


def _invalidate_suppressed_spots_cache() -> None:
    global _SUPPRESSED_SPOTS_TS
    with _SUPPRESSED_SPOTS_LOCK:
        _SUPPRESSED_SPOTS_TS = 0.0


def get_suppressed_spots() -> list[dict[str, Any]]:
    """Return all suppressed spots, from in-memory cache when fresh."""
    global _SUPPRESSED_SPOTS_CACHE, _SUPPRESSED_SPOTS_TS
    now = _time.monotonic()
    with _SUPPRESSED_SPOTS_LOCK:
        if (
            _SUPPRESSED_SPOTS_CACHE is not None
            and now - _SUPPRESSED_SPOTS_TS < _SUPPRESSED_SPOTS_TTL
        ):
            return _SUPPRESSED_SPOTS_CACHE

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, spot_key, lat, lng, type, name, suppressed_by, created_at "
            "FROM suppressed_map_spots ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    result = [
        {
            "id": r["id"],
            "spot_key": r["spot_key"],
            "lat": r["lat"],
            "lng": r["lng"],
            "type": r["type"],
            "name": r["name"],
            "suppressed_by": r["suppressed_by"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    with _SUPPRESSED_SPOTS_LOCK:
        _SUPPRESSED_SPOTS_CACHE = result
        _SUPPRESSED_SPOTS_TS = _time.monotonic()
    return result


def add_suppressed_spot(
    spot_key: str, lat: float, lng: float, type_: str, name: str, user_id: Optional[int]
) -> tuple[dict[str, Any], bool]:
    """Suppress a spot by its key.

    Returns (row_dict, created) where created is False when the spot_key already existed.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO suppressed_map_spots "
            "(spot_key, lat, lng, type, name, suppressed_by) VALUES (?, ?, ?, ?, ?, ?)",
            (spot_key, lat, lng, type_, name.strip(), user_id),
        )
        created = cur.rowcount == 1
        row = conn.execute(
            "SELECT id, spot_key, lat, lng, type, name, suppressed_by, created_at "
            "FROM suppressed_map_spots WHERE spot_key = ?",
            (spot_key,),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    _invalidate_suppressed_spots_cache()
    return {
        "id": row["id"],
        "spot_key": row["spot_key"],
        "lat": row["lat"],
        "lng": row["lng"],
        "type": row["type"],
        "name": row["name"],
        "suppressed_by": row["suppressed_by"],
        "created_at": row["created_at"],
    }, created


def remove_suppressed_spot(suppression_id: int) -> bool:
    """Remove a suppression row by id; returns True if deleted."""
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM suppressed_map_spots WHERE id = ?",
            (suppression_id,),
        )
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount > 0:
        _invalidate_suppressed_spots_cache()
    return cur.rowcount > 0


# Custom habitats (admin-drawn habitat polygons/points) -----------------------

VALID_HABITAT_TYPES = frozenset(
    (
        "surf",
        "kelp",
        "mangrove",
        "grassflat",
        "estuary",
        "reef",
        "bottom",
        "general",
        "pelagic",
        "tidalflat",
    )
)


def _habitat_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    geom: dict[str, Any] = {}
    try:
        raw = row["geometry_json"]
        if raw:
            geom = json.loads(raw)
    except (ValueError, TypeError):
        pass
    return {
        "id": row["id"],
        "habitat_type": row["habitat_type"],
        "name": row["name"],
        "description": row["description"],
        "fill_color": row["fill_color"],
        "geometry": geom,
        "lat": row["lat"],
        "lng": row["lng"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "custom": True,
    }


_CUSTOM_HABITATS_CACHE: Optional[list[dict[str, Any]]] = None
_CUSTOM_HABITATS_TS: float = 0.0
_CUSTOM_HABITATS_TTL: float = 300.0
_CUSTOM_HABITATS_LOCK = _threading.Lock()


def _invalidate_custom_habitats_cache() -> None:
    global _CUSTOM_HABITATS_TS
    with _CUSTOM_HABITATS_LOCK:
        _CUSTOM_HABITATS_TS = 0.0


def get_all_custom_habitats() -> list[dict[str, Any]]:
    """Return all non-deleted custom habitats (cached)."""
    global _CUSTOM_HABITATS_CACHE, _CUSTOM_HABITATS_TS
    now = _time.monotonic()
    with _CUSTOM_HABITATS_LOCK:
        if (
            _CUSTOM_HABITATS_CACHE is not None
            and now - _CUSTOM_HABITATS_TS < _CUSTOM_HABITATS_TTL
        ):
            return _CUSTOM_HABITATS_CACHE

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, habitat_type, name, description, fill_color, geometry_json, lat, lng, "
            "created_by, created_at, updated_at FROM custom_habitats WHERE is_deleted = 0 ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()

    result = [_habitat_row_to_dict(r) for r in rows]
    with _CUSTOM_HABITATS_LOCK:
        _CUSTOM_HABITATS_CACHE = result
        _CUSTOM_HABITATS_TS = _time.monotonic()
    return result


def get_custom_habitats_in_bbox(
    south: float,
    west: float,
    north: float,
    east: float,
) -> list[dict[str, Any]]:
    """Return non-deleted custom habitats whose centroid lies within bbox."""
    result = []
    for h in get_all_custom_habitats():
        lat = h.get("lat")
        lng = h.get("lng")
        if lat is None or lng is None:
            result.append(h)
            continue
        if south <= lat <= north and west <= lng <= east:
            result.append(h)
    return result


def create_custom_habitat(
    habitat_id: str,
    habitat_type: str,
    name: str,
    description: str,
    fill_color: str,
    geometry: dict[str, Any],
    lat: Optional[float],
    lng: Optional[float],
    user_id: int,
) -> dict[str, Any]:
    """Insert a new custom habitat; returns the new row dict."""
    geometry_json = json.dumps(geometry)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO custom_habitats "
            "(id, habitat_type, name, description, fill_color, geometry_json, lat, lng, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                habitat_id,
                habitat_type,
                name.strip(),
                description.strip(),
                fill_color.strip(),
                geometry_json,
                lat,
                lng,
                user_id,
            ),
        )
        row = conn.execute(
            "SELECT id, habitat_type, name, description, fill_color, geometry_json, lat, lng, "
            "created_by, created_at, updated_at FROM custom_habitats WHERE id = ?",
            (habitat_id,),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    _invalidate_custom_habitats_cache()
    return _habitat_row_to_dict(row)


def update_custom_habitat(
    habitat_id: str,
    habitat_type: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    fill_color: Optional[str] = None,
    geometry: Optional[dict[str, Any]] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Update fields on an existing custom habitat; returns updated dict or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM custom_habitats WHERE id = ? AND is_deleted = 0",
            (habitat_id,),
        ).fetchone()
        if not row:
            return None
        updates: list[str] = []
        params: list[Any] = []
        if habitat_type is not None:
            updates.append("habitat_type = ?")
            params.append(habitat_type)
        if name is not None:
            updates.append("name = ?")
            params.append(name.strip())
        if description is not None:
            updates.append("description = ?")
            params.append(description.strip())
        if fill_color is not None:
            updates.append("fill_color = ?")
            params.append(fill_color.strip())
        if geometry is not None:
            updates.append("geometry_json = ?")
            params.append(json.dumps(geometry))
        if lat is not None:
            updates.append("lat = ?")
            params.append(lat)
        if lng is not None:
            updates.append("lng = ?")
            params.append(lng)
        updates.append("updated_at = datetime('now')")
        params.append(habitat_id)
        conn.execute(
            f"UPDATE custom_habitats SET {', '.join(updates)} WHERE id = ? AND is_deleted = 0",
            params,
        )
        updated = conn.execute(
            "SELECT id, habitat_type, name, description, fill_color, geometry_json, lat, lng, "
            "created_by, created_at, updated_at FROM custom_habitats WHERE id = ? AND is_deleted = 0",
            (habitat_id,),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    _invalidate_custom_habitats_cache()
    return _habitat_row_to_dict(updated) if updated else None


def delete_custom_habitat(habitat_id: str) -> bool:
    """Soft-delete a custom habitat; returns True if a row was affected."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE custom_habitats SET is_deleted = 1, updated_at = datetime('now') "
            "WHERE id = ? AND is_deleted = 0",
            (habitat_id,),
        )
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount > 0:
        _invalidate_custom_habitats_cache()
    return cur.rowcount > 0


def undelete_custom_habitat(habitat_id: str) -> Optional[dict[str, Any]]:
    """Restore a soft-deleted custom habitat; returns the row dict or None if not found."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE custom_habitats SET is_deleted = 0, updated_at = datetime('now') "
            "WHERE id = ? AND is_deleted = 1",
            (habitat_id,),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT id, habitat_type, name, description, fill_color, geometry_json, lat, lng, "
            "created_by, created_at, updated_at FROM custom_habitats WHERE id = ?",
            (habitat_id,),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    _invalidate_custom_habitats_cache()
    return _habitat_row_to_dict(row) if row else None


# Habitat overrides (admin-applied name/description/color for AI features) ----

_HABITAT_OVERRIDES_CACHE: Optional[dict[str, dict[str, Any]]] = None
_HABITAT_OVERRIDES_TS: float = 0.0
_HABITAT_OVERRIDES_TTL: float = 300.0
_HABITAT_OVERRIDES_LOCK = _threading.Lock()


def _invalidate_habitat_overrides_cache() -> None:
    global _HABITAT_OVERRIDES_TS
    with _HABITAT_OVERRIDES_LOCK:
        _HABITAT_OVERRIDES_TS = 0.0


def get_habitat_overrides() -> dict[str, dict[str, Any]]:
    """Return all active habitat overrides keyed by feature_key."""
    global _HABITAT_OVERRIDES_CACHE, _HABITAT_OVERRIDES_TS
    now = _time.monotonic()
    with _HABITAT_OVERRIDES_LOCK:
        if (
            _HABITAT_OVERRIDES_CACHE is not None
            and now - _HABITAT_OVERRIDES_TS < _HABITAT_OVERRIDES_TTL
        ):
            return _HABITAT_OVERRIDES_CACHE

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, feature_key, name, description, fill_color, created_by, created_at, updated_at "
            "FROM habitat_overrides WHERE is_deleted = 0"
        ).fetchall()
    finally:
        conn.close()

    result = {
        r["feature_key"]: {
            "id": r["id"],
            "feature_key": r["feature_key"],
            "name": r["name"],
            "description": r["description"],
            "fill_color": r["fill_color"],
            "created_by": r["created_by"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    }
    with _HABITAT_OVERRIDES_LOCK:
        _HABITAT_OVERRIDES_CACHE = result
        _HABITAT_OVERRIDES_TS = _time.monotonic()
    return result


def upsert_habitat_override(
    feature_key: str,
    name: Optional[str],
    description: Optional[str],
    fill_color: Optional[str],
    user_id: int,
) -> dict[str, Any]:
    """Create or update a habitat override; returns the row dict."""
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM habitat_overrides WHERE feature_key = ?",
            (feature_key,),
        ).fetchone()
        if existing:
            updates: list[str] = []
            params: list[Any] = []
            if name is not None:
                updates.append("name = ?")
                params.append(name.strip())
            if description is not None:
                updates.append("description = ?")
                params.append(description.strip())
            if fill_color is not None:
                updates.append("fill_color = ?")
                params.append(fill_color.strip())
            updates.extend(["is_deleted = 0", "updated_at = datetime('now')"])
            params.append(feature_key)
            conn.execute(
                f"UPDATE habitat_overrides SET {', '.join(updates)} WHERE feature_key = ?",
                params,
            )
        else:
            conn.execute(
                "INSERT INTO habitat_overrides (feature_key, name, description, fill_color, created_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (feature_key, name, description, fill_color, user_id),
            )
        row = conn.execute(
            "SELECT id, feature_key, name, description, fill_color, created_by, created_at, updated_at "
            "FROM habitat_overrides WHERE feature_key = ?",
            (feature_key,),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    _invalidate_habitat_overrides_cache()
    return {
        "id": row["id"],
        "feature_key": row["feature_key"],
        "name": row["name"],
        "description": row["description"],
        "fill_color": row["fill_color"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def delete_habitat_override(override_id: int) -> bool:
    """Soft-delete a habitat override; returns True if affected."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE habitat_overrides SET is_deleted = 1, updated_at = datetime('now') "
            "WHERE id = ? AND is_deleted = 0",
            (override_id,),
        )
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount > 0:
        _invalidate_habitat_overrides_cache()
    return cur.rowcount > 0


# Custom habitat types (admin-defined habitat categories) ---------------------

_CUSTOM_HABITAT_TYPES_CACHE: Optional[list[dict[str, Any]]] = None
_CUSTOM_HABITAT_TYPES_TS: float = 0.0
_CUSTOM_HABITAT_TYPES_TTL: float = 300.0
_CUSTOM_HABITAT_TYPES_LOCK = _threading.Lock()


def _invalidate_custom_habitat_types_cache() -> None:
    global _CUSTOM_HABITAT_TYPES_TS
    with _CUSTOM_HABITAT_TYPES_LOCK:
        _CUSTOM_HABITAT_TYPES_TS = 0.0


def get_custom_habitat_types() -> list[dict[str, Any]]:
    """Return all active admin-defined custom habitat types (cached)."""
    global _CUSTOM_HABITAT_TYPES_CACHE, _CUSTOM_HABITAT_TYPES_TS
    now = _time.monotonic()
    with _CUSTOM_HABITAT_TYPES_LOCK:
        if (
            _CUSTOM_HABITAT_TYPES_CACHE is not None
            and now - _CUSTOM_HABITAT_TYPES_TS < _CUSTOM_HABITAT_TYPES_TTL
        ):
            return _CUSTOM_HABITAT_TYPES_CACHE

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, slug, default_color, created_by, created_at "
            "FROM custom_habitat_types WHERE is_deleted = 0 ORDER BY name"
        ).fetchall()
    finally:
        conn.close()

    result = [
        {
            "id": r["id"],
            "name": r["name"],
            "slug": r["slug"],
            "default_color": r["default_color"],
            "created_by": r["created_by"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    with _CUSTOM_HABITAT_TYPES_LOCK:
        _CUSTOM_HABITAT_TYPES_CACHE = result
        _CUSTOM_HABITAT_TYPES_TS = _time.monotonic()
    return result


def create_custom_habitat_type(
    name: str, slug: str, default_color: str, user_id: int
) -> dict[str, Any]:
    """Insert a new custom habitat type; returns the new row dict."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO custom_habitat_types (name, slug, default_color, created_by) "
            "VALUES (?, ?, ?, ?)",
            (name.strip(), slug.strip(), default_color.strip(), user_id),
        )
        row = conn.execute(
            "SELECT id, name, slug, default_color, created_by, created_at "
            "FROM custom_habitat_types WHERE slug = ?",
            (slug.strip(),),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    _invalidate_custom_habitat_types_cache()
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "default_color": row["default_color"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def delete_custom_habitat_type(type_id: int) -> bool:
    """Soft-delete a custom habitat type; returns True if a row was affected."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE custom_habitat_types SET is_deleted = 1 WHERE id = ? AND is_deleted = 0",
            (type_id,),
        )
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount > 0:
        _invalidate_custom_habitat_types_cache()
    return cur.rowcount > 0
