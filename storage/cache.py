"""Forecast cache -- SQLite primary, JSON file fallback for migration.

Public API (unchanged):
    load_cached_forecast(location_id) -> dict | None
    save_forecast(data, location_id) -> None
    _forecast_age_minutes(forecast) -> float | None
    _human_age(minutes) -> str
    CACHE_MAX_AGE_HOURS
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Maximum age (in hours) before a cached forecast is considered stale
# and automatically refreshed on the next page load.
CACHE_MAX_AGE_HOURS = 4

# Legacy JSON cache directory (kept for migration / fallback reads)
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(CACHE_DIR, "forecast.json")


# ---------------------------------------------------------------------------
# Primary storage: SQLite via storage.db
# ---------------------------------------------------------------------------

def load_cached_forecast(location_id: str = "") -> Optional[Dict[str, Any]]:
    """Load the cached forecast, trying SQLite first then JSON fallback."""
    if not location_id:
        return _load_json_fallback(location_id)

    from storage.sqlite import load_forecast
    result = load_forecast(location_id)
    if result is not None:
        return result

    # Fallback: try legacy JSON file and migrate it to DB if found
    result = _load_json_fallback(location_id)
    if result is not None:
        _migrate_json_to_db(location_id, result)
    return result


def save_forecast(data: Dict[str, Any], location_id: str = "") -> None:
    """Persist the forecast to SQLite; JSON is fallback-only for resilience."""
    if not location_id:
        _save_json(data, location_id)
        return

    try:
        from storage.sqlite import save_forecast_to_db
        save_forecast_to_db(location_id, data)
    except Exception as exc:
        logger.warning("DB write failed for %s, writing JSON fallback: %s", location_id, exc)
        _save_json(data, location_id)


# ---------------------------------------------------------------------------
# JSON file helpers (legacy / backup)
# ---------------------------------------------------------------------------

def _cache_path(location_id: str = "") -> str:
    """Return the JSON cache file path for a given location."""
    if location_id:
        return os.path.join(CACHE_DIR, f"forecast_{location_id}.json")
    return CACHE_FILE


def _load_json_fallback(location_id: str = "") -> Optional[Dict[str, Any]]:
    """Load from the legacy JSON file if it exists."""
    path = _cache_path(location_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_json(data: Dict[str, Any], location_id: str = "") -> None:
    """Write forecast to a JSON file (backup)."""
    path = _cache_path(location_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        logger.warning("Failed to write JSON backup %s: %s", path, exc)


def _migrate_json_to_db(location_id: str, data: Dict[str, Any]) -> None:
    """One-time migration: copy a JSON-cached forecast into the DB."""
    try:
        from storage.sqlite import save_forecast_to_db
        save_forecast_to_db(location_id, data)
        logger.info("Migrated JSON forecast to DB for %s", location_id)
    except Exception as exc:
        logger.warning("Failed to migrate forecast for %s: %s", location_id, exc)


# ---------------------------------------------------------------------------
# Age / display helpers (unchanged)
# ---------------------------------------------------------------------------

def _forecast_age_minutes(forecast: Dict[str, Any]) -> Optional[float]:
    """Return the age of a cached forecast in minutes, or None."""
    try:
        generated = datetime.fromisoformat(forecast["generated_at"])
        now = datetime.now(ZoneInfo("America/New_York"))
        return (now - generated).total_seconds() / 60
    except Exception:
        return None


def _human_age(minutes: Optional[float]) -> str:
    """Convert a duration in minutes to a human-friendly string."""
    if minutes is None:
        return ""
    if minutes < 1:
        return "just now"
    if minutes < 60:
        m = int(minutes)
        return f"{m} min ago"
    hours = minutes / 60
    if hours < 24:
        h = int(hours)
        return f"{h} hr ago" if h == 1 else f"{h} hrs ago"
    days = int(hours / 24)
    return f"{days} day ago" if days == 1 else f"{days} days ago"
