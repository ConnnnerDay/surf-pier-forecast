"""Forecast cache -- SQLite primary, JSON file fallback for migration.

Public API (unchanged):
    load_cached_forecast(location_id, user_id=None) -> dict | None
    save_forecast(data, location_id, user_id=None) -> None
    _forecast_age_minutes(forecast) -> float | None
    _human_age(minutes) -> str
    CACHE_MAX_AGE_HOURS
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from storage.sqlite import (
    delete_forecast_cache,
    load_forecast,
    load_forecast_cache_for_user,
    prune_forecast_cache,
    save_forecast_cache,
)
from utils import norm_user_id as _norm_user_id

logger = logging.getLogger(__name__)

# Maximum age (in hours) before a cached forecast is considered stale
# and automatically refreshed on the next page load.
CACHE_MAX_AGE_HOURS = 4

# ---------------------------------------------------------------------------
# In-process parsed-forecast memory cache
# ---------------------------------------------------------------------------
# Avoids re-reading and re-parsing the large forecast JSON blob from SQLite
# on every dashboard render.  Key: (location_id, normalized_user_id).
# Value: parsed forecast dict.  Invalidated on save/delete and on stale check.
_MEM_CACHE: dict[tuple[str, int], dict[str, Any]] = {}
_MEM_CACHE_MAX = 32

# Legacy JSON cache directory (kept for migration / fallback reads)
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(CACHE_DIR, exist_ok=True)
# Restrict the data directory to the owning user so forecast cache files,
# the SQLite database, and the secret key file cannot be read by other users
# on a shared host.
try:
    os.chmod(CACHE_DIR, 0o700)
except OSError:
    pass
CACHE_FILE = os.path.join(CACHE_DIR, "forecast.json")


# ---------------------------------------------------------------------------
# Primary storage: SQLite via storage.db
# ---------------------------------------------------------------------------


def _is_stale(forecast: dict[str, Any]) -> bool:
    age = _forecast_age_minutes(forecast)
    if age is None:
        return False
    if age > CACHE_MAX_AGE_HOURS * 60:
        return True
    # Stale if generated before today's UTC midnight — yesterday's data is
    # always useless regardless of how many hours have elapsed.
    try:
        generated = datetime.fromisoformat(forecast["generated_at"])
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        return (
            generated.astimezone(timezone.utc).date()
            < datetime.now(timezone.utc).date()
        )
    except Exception:
        return False


def load_cached_forecast(
    location_id: str = "",
    user_id: Optional[int] = None,
    include_stale: bool = False,
) -> Optional[dict[str, Any]]:
    """Load the cached forecast, trying in-process memory then SQLite then JSON.

    By default stale entries are treated as cache misses and removed from the
    hot cache table. Set ``include_stale=True`` when callers want to render a
    stale forecast while an async refresh is in progress.
    """
    if not location_id:
        return _load_json_fallback(location_id)

    normalized_uid = _norm_user_id(user_id)
    mem_key = (location_id, normalized_uid)

    # Fast path: in-process memory cache (avoids SQLite + json.loads on every render)
    mem_hit = _MEM_CACHE.get(mem_key)
    if mem_hit is not None:
        if _is_stale(mem_hit):
            if include_stale:
                return mem_hit
            # Evict from memory; DB eviction happens below
            _MEM_CACHE.pop(mem_key, None)
        else:
            return mem_hit

    # Combined query: one connection handles user-specific + anonymous fallback.
    result = load_forecast_cache_for_user(normalized_uid, location_id)

    if result is not None:
        if _is_stale(result):
            if include_stale:
                _mem_cache_set(mem_key, result)
                return result
            delete_forecast_cache(normalized_uid, location_id)
            _MEM_CACHE.pop((location_id, normalized_uid), None)
            return None
        _mem_cache_set(mem_key, result)
        return result

    # Backward-compatible fallback to historical forecasts table
    result = load_forecast(location_id)
    if result is not None:
        if include_stale or not _is_stale(result):
            _mem_cache_set(mem_key, result)
            return result

    # Fallback: try legacy JSON file and migrate it to DB if found
    result = _load_json_fallback(location_id)
    if result is not None:
        if _is_stale(result) and not include_stale:
            return None
        _mem_cache_set(mem_key, result)
        _migrate_json_to_db(location_id, result, normalized_uid)
    return result


def prune_old_forecasts(max_age_days: int = 7) -> int:
    """Remove forecast_cache rows older than max_age_days. Returns rows deleted."""
    try:
        removed = prune_forecast_cache(max_age_days)
        if removed:
            logger.info("cache.pruned rows=%d max_age_days=%d", removed, max_age_days)
            _MEM_CACHE.clear()
        return removed
    except Exception as exc:
        logger.warning("cache.prune_failed: %s", exc)
        return 0


def _mem_cache_set(key: tuple[str, int], data: dict[str, Any]) -> None:
    """Insert or update a parsed forecast in the in-process memory cache."""
    if key not in _MEM_CACHE and len(_MEM_CACHE) >= _MEM_CACHE_MAX:
        _MEM_CACHE.pop(next(iter(_MEM_CACHE)))
    _MEM_CACHE[key] = data


def save_forecast(
    data: dict[str, Any], location_id: str = "", user_id: Optional[int] = None
) -> None:
    """Persist the forecast to SQLite; JSON is fallback-only for resilience."""
    if not location_id:
        _save_json(data, location_id)
        return

    normalized_uid = _norm_user_id(user_id)
    try:
        save_forecast_cache(normalized_uid, location_id, data)
    except Exception as exc:
        logger.warning(
            "DB write failed for %s, writing JSON fallback: %s", location_id, exc
        )
        _save_json(data, location_id)

    # Update the in-process memory cache so the next render sees fresh data
    # without a DB round-trip.
    _mem_cache_set((location_id, normalized_uid), data)


# ---------------------------------------------------------------------------
# JSON file helpers (legacy / backup)
# ---------------------------------------------------------------------------


def _cache_path(location_id: str = "") -> str:
    """Return the JSON cache file path for a given location.

    ``location_id`` is sanitised to a flat filename so that values like
    ``../../../etc/passwd`` cannot escape the cache directory.
    """
    if location_id:
        safe_id = os.path.basename(location_id).replace("/", "_").replace("\\", "_")
        return os.path.join(CACHE_DIR, f"forecast_{safe_id}.json")
    return CACHE_FILE


def _load_json_fallback(location_id: str = "") -> Optional[dict[str, Any]]:
    """Load from the legacy JSON file if it exists."""
    path = _cache_path(location_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_json(data: dict[str, Any], location_id: str = "") -> None:
    """Write forecast to a JSON file (backup)."""
    path = _cache_path(location_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        logger.warning("Failed to write JSON backup %s: %s", path, exc)


def _migrate_json_to_db(
    location_id: str, data: dict[str, Any], user_id: int = 0
) -> None:
    """One-time migration: copy a JSON-cached forecast into the DB."""
    try:
        save_forecast_cache(user_id, location_id, data)
        logger.info("Migrated JSON forecast to DB for %s", location_id)
    except Exception as exc:
        logger.warning("Failed to migrate forecast for %s: %s", location_id, exc)


# ---------------------------------------------------------------------------
# Age / display helpers (unchanged)
# ---------------------------------------------------------------------------


def _forecast_age_minutes(forecast: dict[str, Any]) -> Optional[float]:
    """Return the age of a cached forecast in minutes, or None.

    All comparisons are done in UTC so the result is correct regardless of the
    server's local timezone and immune to DST transitions.  Legacy naive
    ``generated_at`` values (stored before timezone-awareness was enforced) are
    treated as UTC to avoid erroneously marking them as ageless.
    """
    try:
        generated = datetime.fromisoformat(forecast["generated_at"])
        if generated.tzinfo is None:
            # Legacy naive timestamp — assume UTC (matches how datetime.utcnow()
            # was used before the switch to timezone-aware datetimes).
            generated = generated.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
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
