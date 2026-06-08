"""Live water temperature fetching from NOAA CO-OPS with in-process TTL cache.

This module provides a thin cached wrapper around the CO-OPS Tides & Currents
API.  It is intentionally separate from services/noaa.py so it can be imported
independently and its cache TTL controlled without touching the broader NOAA
service layer.

Fallback behaviour:
  - On any network or parsing error the function returns None.
  - The caller (domain/forecast.py / services/noaa.py get_water_temp) is
    responsible for falling back to the monthly location table when None is
    returned.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from services.http_client import get as http_get

logger = logging.getLogger(__name__)

_COOPS_WATER_TEMP_URL = (
    "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    "?date=latest&station={station}"
    "&product=water_temperature&units=english"
    "&time_zone=lst_ldt&format=json"
)
_COOPS_HEADERS = {
    "User-Agent": "(SurfPierForecast, github.com/ConnnnerDay/surf-pier-forecast)",
    "Accept": "application/json",
}

# In-process TTL cache: maps station_id -> (timestamp, value_or_None)
_CACHE: dict[str, tuple[float, Optional[float]]] = {}
_CACHE_TTL_SECONDS: int = 4 * 60 * 60  # 4 hours


def get_water_temp_coops(station_id: str, *, timeout: int = 8) -> Optional[float]:
    """Fetch current water temperature from NOAA CO-OPS API.

    Returns temperature in °F or None on failure.
    Uses an in-process LRU cache with 4-hour TTL.
    Falls back gracefully to None (caller uses monthly fallback).
    """
    now = time.monotonic()
    cached = _CACHE.get(station_id)
    if cached is not None:
        ts, value = cached
        if now - ts < _CACHE_TTL_SECONDS:
            return value

    result: Optional[float] = None
    try:
        url = _COOPS_WATER_TEMP_URL.format(station=station_id)
        resp = http_get(
            url,
            endpoint="coops.water_temperature",
            headers=_COOPS_HEADERS,
            timeout=(5, timeout),
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data") or []
        reading = rows[0].get("v") if rows else None
        if reading is not None:
            result = float(reading)
    except Exception:
        logger.debug("get_water_temp_coops failed for station %s", station_id, exc_info=True)

    _CACHE[station_id] = (now, result)
    return result
