"""Forecast cache implementation (JSON file storage)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Path to the cached forecast JSON
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(CACHE_DIR, "forecast.json")

# Maximum age (in hours) before a cached forecast is considered stale
# and automatically refreshed on the next page load.
CACHE_MAX_AGE_HOURS = 4


def _cache_path(location_id: str = "") -> str:
    """Return the cache file path for a given location."""
    if location_id:
        return os.path.join(CACHE_DIR, f"forecast_{location_id}.json")
    return CACHE_FILE


def load_cached_forecast(location_id: str = "") -> Optional[Dict[str, Any]]:
    """Load the cached forecast from disk if present."""
    path = _cache_path(location_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_forecast(data: Dict[str, Any], location_id: str = "") -> None:
    """Persist the forecast to disk."""
    path = _cache_path(location_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


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
