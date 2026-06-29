"""Nearest-station resolution for arbitrary US coastal coordinates.

Fetches and caches the public NOAA CO-OPS and NDBC station catalogs so a
forecast can be generated for *any* coastal point, not only the curated spots
in :mod:`locations`.  This is what lets the app work uniformly nationwide
instead of only near a hand-mapped location.

All network access degrades gracefully: when a catalog is unavailable the
lookup helpers return ``None``/``[]`` and the caller falls back to the nearest
curated location's station IDs — i.e. the app's pre-existing behaviour.  The
feature therefore can only *add* coverage; it never makes a working location
worse.

The catalogs change rarely, so each is fetched at most once per
``_CATALOG_TTL_S`` window and cached in-process (mirroring the lru-cached
``all_locations_sorted`` pattern in ``locations.py``).
"""

from __future__ import annotations

import logging
import math
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

from services.http_client import get as http_get

logger = logging.getLogger(__name__)

# Catalogs are static metadata; a day-long TTL keeps a long-running process
# fresh without re-downloading on every dynamic-location resolution.
_CATALOG_TTL_S = 24 * 3600
# A failed fetch is cached only briefly so that, during an upstream outage,
# every dynamic-location resolution doesn't re-hammer the (down) endpoint and
# block each request on the timeout — while still recovering within a minute or
# two once the API is healthy again.
_NEG_CATALOG_TTL_S = 120
_TIMEOUT: tuple[float, float] = (3.05, 20)

_COOPS_MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
_COOPS_TIDE_URL = _COOPS_MDAPI + "?type=tidepredictions"
_COOPS_TEMP_URL = _COOPS_MDAPI + "?type=watertemp"
_NDBC_URL = "https://www.ndbc.noaa.gov/activestations.xml"

_HEADERS = {
    "User-Agent": "(SurfPierForecast, github.com/ConnnnerDay/surf-pier-forecast)",
    "Accept": "application/json",
}

_lock = threading.Lock()
# Each cache entry is (fetched_at_epoch, list_of_station_dicts); a non-empty
# list uses the long TTL, an empty list (failed fetch) the short negative TTL.
_CACHES: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _load_catalog(key: str, fetch: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return a cached catalog, fetching on miss; caches successes and failures.

    Successful (non-empty) results live for ``_CATALOG_TTL_S``; empty results
    (a failed fetch) live only for ``_NEG_CATALOG_TTL_S`` so an outage doesn't
    turn every dynamic-location lookup into a fresh blocking network call.
    """
    with _lock:
        entry = _CACHES.get(key)
        if entry is not None:
            ts, data = entry
            ttl = _CATALOG_TTL_S if data else _NEG_CATALOG_TTL_S
            if time.time() - ts < ttl:
                return data
    data = fetch()
    with _lock:
        _CACHES[key] = (time.time(), data)
    return data


def _haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in miles (kept local to avoid an import cycle)."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _fetch_coops_stations(url: str, endpoint: str, label: str) -> list[dict[str, Any]]:
    """Download a CO-OPS station catalog (tide-prediction or water-temp).

    Returns a list of ``{"id", "name", "lat", "lng", "state"}`` dicts, or an
    empty list on any failure.
    """
    try:
        resp = http_get(url, endpoint=endpoint, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        logger.warning("CO-OPS %s catalog unavailable", label, exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    for s in data.get("stations", []):
        try:
            out.append(
                {
                    "id": str(s["id"]),
                    "name": s.get("name", ""),
                    "lat": float(s["lat"]),
                    "lng": float(s["lng"]),
                    "state": (s.get("state") or "").strip(),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    logger.info("Loaded %d CO-OPS %s stations", len(out), label)
    return out


def _fetch_ndbc_stations() -> list[dict[str, Any]]:
    """Download the NDBC active-station catalog (XML).

    Returns a list of ``{"id", "lat", "lng", "has_met"}`` dicts, or an empty
    list on any failure.  ``has_met`` marks stations reporting meteorological
    data (wind/waves), which are the only ones useful for marine conditions.
    """
    try:
        resp = http_get(
            _NDBC_URL,
            endpoint="stations.ndbc_catalog",
            headers={"User-Agent": _HEADERS["User-Agent"]},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
    except Exception:
        logger.warning("NDBC station catalog unavailable", exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    for st in root.iter("station"):
        sid = st.get("id")
        lat = st.get("lat")
        lng = st.get("lon")
        if not sid or lat is None or lng is None:
            continue
        try:
            out.append(
                {
                    "id": str(sid),
                    "lat": float(lat),
                    "lng": float(lng),
                    "has_met": (st.get("met", "n") or "n").lower() == "y",
                }
            )
        except (TypeError, ValueError):
            continue
    logger.info("Loaded %d NDBC stations", len(out))
    return out


def _load_coops() -> list[dict[str, Any]]:
    """Return the cached CO-OPS tide-prediction catalog."""
    return _load_catalog(
        "coops_tide",
        lambda: _fetch_coops_stations(
            _COOPS_TIDE_URL, "stations.coops_catalog", "tide"
        ),
    )


def _load_coops_temp() -> list[dict[str, Any]]:
    """Return the cached CO-OPS water-temperature catalog."""
    return _load_catalog(
        "coops_temp",
        lambda: _fetch_coops_stations(
            _COOPS_TEMP_URL, "stations.coops_temp_catalog", "water-temp"
        ),
    )


def _load_ndbc() -> list[dict[str, Any]]:
    """Return the cached NDBC catalog."""
    return _load_catalog("ndbc", _fetch_ndbc_stations)


def _nearest(
    stations: list[dict[str, Any]], lat: float, lng: float
) -> Optional[dict[str, Any]]:
    """Return the nearest station dict (with distance_miles) or None."""
    if not stations:
        return None
    best: Optional[dict[str, Any]] = None
    best_d = float("inf")
    for s in stations:
        d = _haversine_miles(lat, lng, s["lat"], s["lng"])
        if d < best_d:
            best_d = d
            best = s
    if best is None:
        return None
    return {
        "id": best["id"],
        "name": best["name"],
        "state": best["state"],
        "distance_miles": round(best_d, 1),
    }


def nearest_coops_station(lat: float, lng: float) -> Optional[dict[str, Any]]:
    """Return the nearest CO-OPS tide station to ``(lat, lng)``.

    The result dict carries ``id``, ``name``, ``state`` and ``distance_miles``.
    Returns ``None`` when the catalog is unavailable so the caller can fall
    back to a curated station.
    """
    return _nearest(_load_coops(), lat, lng)


def nearest_watertemp_station(lat: float, lng: float) -> Optional[dict[str, Any]]:
    """Return the nearest CO-OPS station that reports water temperature.

    Tide stations often lack a temperature sensor, so resolving this separately
    gives dynamic locations a live water-temp reading (the dominant species
    driver) instead of falling straight back to monthly climatology. Returns
    ``None`` when the catalog is unavailable.
    """
    return _nearest(_load_coops_temp(), lat, lng)


def nearest_ndbc_stations(lat: float, lng: float, n: int = 2) -> list[dict[str, Any]]:
    """Return up to ``n`` nearest met-reporting NDBC buoys to ``(lat, lng)``.

    Each result carries ``id`` and ``distance_miles``, sorted nearest-first.
    Returns ``[]`` when the catalog is unavailable.
    """
    stations = _load_ndbc()
    if not stations:
        return []
    scored: list[tuple[float, str]] = []
    for s in stations:
        if not s.get("has_met"):
            continue
        d = _haversine_miles(lat, lng, s["lat"], s["lng"])
        scored.append((d, s["id"]))
    scored.sort(key=lambda x: x[0])
    return [{"id": sid, "distance_miles": round(d, 1)} for d, sid in scored[:n]]
