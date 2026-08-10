"""Minimal sqlite3 shim for the legacy modules ported from v1.

v1's storage/sqlite.py is a full data-access layer for users, profiles,
forecasts, and catch logs — v2 replaces all of that with SQLAlchemy models
(see app/models/). The only pieces of the ported v1 code
(storage/species_images.py, storage/reg_scraper.py) that still touch SQLite
directly are two small read-through caches keyed by species/state, which
this file provides a standalone connection + schema for, kept in a
separate file from v2's main app.db so the two schemas never collide.
"""

from __future__ import annotations

import os
import sqlite3

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "legacy_cache.db"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS species_image_cache (
    species_key TEXT PRIMARY KEY,
    found       INTEGER NOT NULL DEFAULT 0,
    image_json  TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reg_scrape_cache (
    species_key TEXT NOT NULL,
    state       TEXT NOT NULL,
    reg_json    TEXT NOT NULL,
    scraped_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (species_key, state)
);
"""

_initialized = False


def get_db() -> sqlite3.Connection:
    global _initialized
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=2.0)
    conn.row_factory = sqlite3.Row
    if not _initialized:
        conn.executescript(_SCHEMA)
        conn.commit()
        _initialized = True
    return conn
