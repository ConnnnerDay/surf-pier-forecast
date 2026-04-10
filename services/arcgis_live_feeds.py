"""ArcGIS Living Atlas Live Feeds integration.

Data sources (all from services9.arcgis.com/RHVPKKiFTONKtxq3):
  NWS_Watches_Warnings_v1  → layer 6 = "Events Ordered by Size and Severity"
                             Active NWS watches, warnings, and advisories as
                             polygons with severity, expiration, and description.
  Active_Hurricanes_v1     → layers 0/2/4 = forecast position, track, cone
                             Live tropical cyclone data from NHC/JTWC.

Public API
----------
    fetch_marine_warnings(south, west, north, east) → list[dict]
    fetch_active_storms()                           → list[dict]
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_BASE = "https://services9.arcgis.com/RHVPKKiFTONKtxq3/arcgis/rest/services"

# NWS Watches/Warnings – layer 6 = "Events Ordered by Size and Severity"
_WARNINGS_URL = f"{_BASE}/NWS_Watches_Warnings_v1/FeatureServer/6/query"

# Active Hurricanes layers
_STORM_POS_URL   = f"{_BASE}/Active_Hurricanes_v1/FeatureServer/0/query"  # Forecast Position
_STORM_TRACK_URL = f"{_BASE}/Active_Hurricanes_v1/FeatureServer/2/query"  # Forecast Track
_STORM_CONE_URL  = f"{_BASE}/Active_Hurricanes_v1/FeatureServer/4/query"  # Forecast Error Cone

# Keywords that make a warning relevant to coastal/marine fishing
_MARINE_KEYWORDS = frozenset({
    "marine", "gale", "storm warning", "hurricane force", "small craft",
    "coastal flood", "beach hazard", "rip current", "high wind",
    "dense fog", "special marine", "tsunami", "surf",
})

# ── Caches ─────────────────────────────────────────────────────────────────────

_WARN_CACHE: Dict[tuple, Dict[str, Any]] = {}
_WARN_TTL = 600    # 10 minutes — warnings update frequently
_WARN_MAX = 64     # bbox combinations kept in memory

_STORM_CACHE: Optional[List[Dict[str, Any]]] = None
_STORM_TS    = 0.0
_STORM_TTL   = 600  # 10 minutes


def _warn_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 2), round(w, 2), round(n, 2), round(e, 2))


def _warn_evict() -> None:
    now = time.time()
    stale = [k for k, v in list(_WARN_CACHE.items()) if now - v["ts"] >= _WARN_TTL]
    for k in stale:
        _WARN_CACHE.pop(k, None)
    while len(_WARN_CACHE) >= _WARN_MAX:
        try:
            del _WARN_CACHE[next(iter(_WARN_CACHE))]
        except (KeyError, StopIteration):
            break


def _is_marine(event: str) -> bool:
    ev = event.lower()
    return any(kw in ev for kw in _MARINE_KEYWORDS)


def _warning_color(severity: str, event: str) -> str:
    """Map severity + event type to a hex fill color for map polygons."""
    ev  = event.lower()
    sev = severity.lower()
    if "extreme" in sev or "hurricane" in ev or "typhoon" in ev or "tornado" in ev:
        return "#ef4444"   # red
    if "severe" in sev or "gale" in ev or "storm warning" in ev or "hurricane force" in ev:
        return "#f97316"   # orange
    if "moderate" in sev or "small craft" in ev or "coastal flood warning" in ev:
        return "#eab308"   # yellow
    return "#60a5fa"       # blue — minor advisories


def _ms_to_iso(ms: Any) -> str:
    """Convert ArcGIS epoch-milliseconds timestamp to ISO-8601 string."""
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _ring_to_latlng(ring: list) -> list:
    """Convert ArcGIS [x=lng, y=lat] ring coordinates to Leaflet [[lat, lng]]."""
    return [[pt[1], pt[0]] for pt in ring if len(pt) >= 2]


# ── Marine warnings ────────────────────────────────────────────────────────────

def fetch_marine_warnings(
    south: float, west: float, north: float, east: float
) -> List[Dict[str, Any]]:
    """Return active NWS watches/warnings that intersect the bounding box.

    Each dict has:
        event       str   warning type (e.g. "Small Craft Advisory")
        severity    str   "Extreme" | "Severe" | "Moderate" | "Minor" | "Unknown"
        summary     str   one-line description
        description str   full text of the warning
        instruction str   recommended safety actions
        affected    str   areas covered by the warning
        expires     str   ISO-8601 expiration time (UTC) or ""
        color       str   suggested hex colour for the polygon fill
        marine      bool  True if event type is marine/coastal relevant
        rings       list  list of rings; each ring = [[lat, lng], ...]
    """
    key = _warn_key(south, west, north, east)
    _warn_evict()
    cached = _WARN_CACHE.get(key)
    if cached and time.time() - cached["ts"] < _WARN_TTL:
        return cached["data"]

    params = {
        "where":        "1=1",
        "geometry":     f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel":   "esriSpatialRelIntersects",
        "inSR":         "4326",
        "outSR":        "4326",
        "outFields":    (
            "Event,Severity,Summary,Description,Instruction,"
            "Affected,End_,Updated,Urgency"
        ),
        "returnGeometry":   "true",
        "resultRecordCount": 200,
        "f": "json",
    }

    try:
        resp = requests.get(_WARNINGS_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("ArcGIS marine-warnings fetch failed: %s", exc)
        return []

    results: List[Dict[str, Any]] = []
    for feat in data.get("features", []):
        attrs  = feat.get("attributes", {})
        event  = attrs.get("Event") or ""
        geom   = feat.get("geometry") or {}
        rings  = geom.get("rings") or []
        if not rings:
            continue

        results.append({
            "event":       event,
            "severity":    attrs.get("Severity") or "",
            "summary":     attrs.get("Summary") or "",
            "description": attrs.get("Description") or "",
            "instruction": attrs.get("Instruction") or "",
            "affected":    attrs.get("Affected") or "",
            "expires":     _ms_to_iso(attrs.get("End_")),
            "color":       _warning_color(attrs.get("Severity") or "", event),
            "marine":      _is_marine(event),
            "rings":       [_ring_to_latlng(r) for r in rings],
        })

    _WARN_CACHE[key] = {"ts": time.time(), "data": results}
    return results


# ── Active storm tracker ───────────────────────────────────────────────────────

def fetch_active_storms() -> List[Dict[str, Any]]:
    """Return currently active tropical cyclones with forecast track and cone.

    Each dict has:
        name        str    storm name (e.g. "IDA")
        category    str    human-readable intensity label
        lat         float  current / latest forecast position latitude
        lng         float  current / latest forecast position longitude
        wind_mph    int    max sustained winds in mph
        pressure_mb int    minimum central pressure in mb (0 if unknown)
        track       list   [[lat, lng], ...] forecast track polyline
        cone        list   list of rings [[lat, lng], ...] uncertainty cone
    """
    global _STORM_CACHE, _STORM_TS
    if _STORM_CACHE is not None and time.time() - _STORM_TS < _STORM_TTL:
        return _STORM_CACHE

    common = {
        "where":          "1=1",
        "outSR":          "4326",
        "returnGeometry": "true",
        "f":              "json",
        "resultRecordCount": 50,
    }

    storms: Dict[str, Dict[str, Any]] = {}  # keyed by UPPER storm name

    # ── Step 1: Forecast positions ─────────────────────────────────────────────
    try:
        p = {**common, "outFields": "STORMNAME,STORMTYPE,INTENSITY,MSLP,ADVISNUM"}
        resp = requests.get(_STORM_POS_URL, params=p, timeout=(3.05, 15))
        resp.raise_for_status()
        for feat in resp.json().get("features", []):
            attrs = feat.get("attributes", {})
            geom  = feat.get("geometry") or {}
            name  = (attrs.get("STORMNAME") or "Unknown").strip()
            key   = name.upper()
            kt    = int(attrs.get("INTENSITY") or 0)
            storms[key] = {
                "name":        name.title(),
                "category":    _category_label(kt),
                "lat":         geom.get("y", 0),
                "lng":         geom.get("x", 0),
                "wind_mph":    round(kt * 1.15078),
                "pressure_mb": int(attrs.get("MSLP") or 0),
                "track":       [],
                "cone":        [],
            }
    except Exception as exc:
        logger.warning("ArcGIS storm positions fetch failed: %s", exc)
        _STORM_CACHE = []
        _STORM_TS    = time.time()
        return []

    if not storms:
        _STORM_CACHE = []
        _STORM_TS    = time.time()
        return []

    # ── Step 2: Forecast track ─────────────────────────────────────────────────
    try:
        p = {**common, "outFields": "STORMNAME"}
        resp = requests.get(_STORM_TRACK_URL, params=p, timeout=(3.05, 15))
        resp.raise_for_status()
        for feat in resp.json().get("features", []):
            attrs = feat.get("attributes", {})
            name  = (attrs.get("STORMNAME") or "").strip().upper()
            geom  = feat.get("geometry") or {}
            paths = geom.get("paths") or []
            if name in storms and paths:
                storms[name]["track"] = [[pt[1], pt[0]] for pt in paths[0] if len(pt) >= 2]
    except Exception as exc:
        logger.warning("ArcGIS storm track fetch failed: %s", exc)

    # ── Step 3: Forecast uncertainty cone ─────────────────────────────────────
    try:
        p = {**common, "outFields": "STORMNAME"}
        resp = requests.get(_STORM_CONE_URL, params=p, timeout=(3.05, 15))
        resp.raise_for_status()
        for feat in resp.json().get("features", []):
            attrs = feat.get("attributes", {})
            name  = (attrs.get("STORMNAME") or "").strip().upper()
            geom  = feat.get("geometry") or {}
            rings = geom.get("rings") or []
            if name in storms and rings:
                storms[name]["cone"] = [_ring_to_latlng(r) for r in rings]
    except Exception as exc:
        logger.warning("ArcGIS storm cone fetch failed: %s", exc)

    result = list(storms.values())
    _STORM_CACHE = result
    _STORM_TS    = time.time()
    return result


def _category_label(kt: int) -> str:
    """Convert max sustained wind speed (knots) to a human-readable category."""
    if kt >= 137:
        return "Category 5 Hurricane"
    if kt >= 113:
        return "Category 4 Hurricane"
    if kt >= 96:
        return "Category 3 Hurricane"
    if kt >= 83:
        return "Category 2 Hurricane"
    if kt >= 64:
        return "Category 1 Hurricane"
    if kt >= 34:
        return "Tropical Storm"
    if kt > 0:
        return "Tropical Depression"
    return "Unknown"


def cache_clear() -> None:
    """Clear all cached results.  Useful in tests."""
    global _STORM_CACHE, _STORM_TS
    _WARN_CACHE.clear()
    _STORM_CACHE = None
    _STORM_TS    = 0.0
