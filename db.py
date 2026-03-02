"""
Database module for user accounts and persistent data.

Uses SQLite -- no extra server or dependencies needed.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "app.db")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT  NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    location_id     TEXT,
    theme           TEXT    DEFAULT 'light',
    units           TEXT    DEFAULT 'F',
    fishing_profile TEXT,
    favorites       TEXT    DEFAULT '[]',
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fishing_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    location_id TEXT    NOT NULL,
    species     TEXT    NOT NULL,
    size        TEXT,
    notes       TEXT,
    logged_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def get_db() -> sqlite3.Connection:
    """Open a connection to the SQLite database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript(_SCHEMA)
    conn.close()


# ---------------------------------------------------------------------------
# User accounts
# ---------------------------------------------------------------------------

def create_user(username: str, password: str) -> Optional[int]:
    """Create a new user.  Returns the user ID, or None if username taken."""
    pw_hash = generate_password_hash(password)
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username.strip(), pw_hash),
        )
        user_id = cur.lastrowid
        # Create empty preferences row
        conn.execute(
            "INSERT INTO user_preferences (user_id) VALUES (?)",
            (user_id,),
        )
        conn.commit()
        return user_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Verify credentials.  Returns user dict or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username.strip(),),
    ).fetchone()
    conn.close()
    if row and check_password_hash(row["password_hash"], password):
        return {"id": row["id"], "username": row["username"]}
    return None


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Look up a user by ID."""
    conn = get_db()
    row = conn.execute(
        "SELECT id, username FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row:
        return {"id": row["id"], "username": row["username"]}
    return None


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

def get_preferences(user_id: int) -> Dict[str, Any]:
    """Get all preferences for a user."""
    conn = get_db()
    row = conn.execute(
        "SELECT location_id, theme, units, fishing_profile, favorites "
        "FROM user_preferences WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {}
    profile = None
    if row["fishing_profile"]:
        try:
            profile = json.loads(row["fishing_profile"])
        except (json.JSONDecodeError, TypeError):
            pass
    favs = []
    if row["favorites"]:
        try:
            favs = json.loads(row["favorites"])
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "location_id": row["location_id"],
        "theme": row["theme"] or "light",
        "units": row["units"] or "F",
        "fishing_profile": profile,
        "favorites": favs,
    }


def save_preferences(user_id: int, **kwargs: Any) -> None:
    """Update one or more preference fields."""
    allowed = {"location_id", "theme", "units", "fishing_profile", "favorites"}
    sets = []
    vals: list = []
    for key, val in kwargs.items():
        if key not in allowed:
            continue
        if key in ("fishing_profile", "favorites"):
            val = json.dumps(val) if val is not None else None
        sets.append(f"{key} = ?")
        vals.append(val)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    vals.append(user_id)
    conn = get_db()
    conn.execute(
        f"UPDATE user_preferences SET {', '.join(sets)} WHERE user_id = ?",
        vals,
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fishing log
# ---------------------------------------------------------------------------

def get_log_entries(
    user_id: int, location_id: str, limit: int = 50
) -> List[Dict[str, Any]]:
    """Get fishing log entries for a user at a specific location."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, species, size, notes, logged_at FROM fishing_log "
        "WHERE user_id = ? AND location_id = ? ORDER BY logged_at DESC LIMIT ?",
        (user_id, location_id, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "species": r["species"],
            "size": r["size"],
            "notes": r["notes"],
            "date": r["logged_at"],
        }
        for r in rows
    ]


def add_log_entry(
    user_id: int,
    location_id: str,
    species: str,
    size: str = "",
    notes: str = "",
) -> int:
    """Add a fishing log entry.  Returns the new entry ID."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO fishing_log (user_id, location_id, species, size, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, location_id, species.strip(), size.strip(), notes.strip()),
    )
    entry_id = cur.lastrowid
    conn.commit()
    conn.close()
    return entry_id


def delete_log_entry(user_id: int, entry_id: int) -> bool:
    """Delete a fishing log entry (only if it belongs to this user)."""
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM fishing_log WHERE id = ? AND user_id = ?",
        (entry_id, user_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_log_stats(user_id: int, location_id: str) -> Dict[str, Any]:
    """Get aggregate stats for a user's fishing log at a location."""
    conn = get_db()
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM fishing_log WHERE user_id = ? AND location_id = ?",
        (user_id, location_id),
    ).fetchone()["cnt"]

    species_rows = conn.execute(
        "SELECT species, COUNT(*) as cnt FROM fishing_log "
        "WHERE user_id = ? AND location_id = ? GROUP BY LOWER(species) ORDER BY cnt DESC",
        (user_id, location_id),
    ).fetchall()

    last = conn.execute(
        "SELECT logged_at FROM fishing_log "
        "WHERE user_id = ? AND location_id = ? ORDER BY logged_at DESC LIMIT 1",
        (user_id, location_id),
    ).fetchone()
    conn.close()

    unique = len(species_rows)
    top_species = species_rows[0]["species"] if species_rows else None
    last_date = last["logged_at"].split(" ")[0] if last else None

    return {
        "total": total,
        "unique_species": unique,
        "top_species": top_species,
        "last_date": last_date,
    }
