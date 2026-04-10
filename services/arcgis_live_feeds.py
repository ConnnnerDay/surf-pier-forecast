"""ArcGIS Living Atlas Live Feeds integration.

Data sources (all from services9.arcgis.com/RHVPKKiFTONKtxq3):
  NWS_Watches_Warnings_v1        → layer 6 = "Events Ordered by Size and Severity"
                                   Active NWS watches, warnings, and advisories as
                                   polygons with severity, expiration, and description.
  Active_Hurricanes_v1           → layers 0/2/4 = forecast position, track, cone
                                   Live tropical cyclone data from NHC/JTWC.
  Recent_Hurricanes_v1           → layer 1 = "Observed Track"
                                   Full-season storm tracks for recent Atlantic/Pacific
                                   hurricane seasons, colour-coded by Saffir-Simpson.
  Air_Quality_PM25_Latest_Results → layer 0 = OpenAQ PM2.5 monitoring stations
                                   Latest particulate-matter readings from global
                                   OpenAQ network; converted to AQI-like categories.
  NDFD_WindForecast_v1           → layer 6 = "Wind at City Level"
                                   NOAA National Digital Forecast Database wind
                                   speed/direction/gust at 3-hour intervals (~7 days).

Public API
----------
    fetch_marine_warnings(south, west, north, east)  → list[dict]
    fetch_active_storms()                            → list[dict]
    fetch_recent_storm_tracks(basin)                 → list[dict]
    fetch_air_quality(lat, lng)                      → dict | None
    fetch_wind_forecast(lat, lng)                    → list[dict]
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

# Recent Hurricanes – layer 1 = Observed Track (polylines)
_RECENT_TRACK_URL = f"{_BASE}/Recent_Hurricanes_v1/FeatureServer/1/query"

# Air quality – layer 0 = OpenAQ PM2.5 monitoring stations
_AQI_URL = f"{_BASE}/Air_Quality_PM25_Latest_Results/FeatureServer/0/query"

# NDFD Wind Forecast – layer 6 = City Level (multipoint, 3-h intervals)
_NDFD_WIND_URL = f"{_BASE}/NDFD_WindForecast_v1/FeatureServer/6/query"

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


# ── Recent Hurricane Tracks ────────────────────────────────────────────────────

_RECENT_TRACK_CACHE: Optional[List[Dict[str, Any]]] = None
_RECENT_TRACK_TS    = 0.0
_RECENT_TRACK_TTL   = 3600  # 1 hour — historical; updates a few times per day

# Saffir-Simpson integer → human label
_SS_LABELS = {
    -1: "Low / Remnant",
     0: "Tropical Depression",
     1: "Tropical Storm",
     2: "Category 1 Hurricane",
     3: "Category 2 Hurricane",
     4: "Category 3 Hurricane",
     5: "Category 4 Hurricane",
     6: "Category 5 Hurricane",
}

# Saffir-Simpson → stroke colour (matches common NHC colour scheme)
_SS_COLORS = {
    -1: "#94a3b8",   # grey — low/remnant
     0: "#94a3b8",   # grey — tropical depression
     1: "#3b82f6",   # blue — tropical storm
     2: "#22c55e",   # green — Cat 1
     3: "#f59e0b",   # amber — Cat 2
     4: "#f97316",   # orange — Cat 3
     5: "#ef4444",   # red — Cat 4
     6: "#7c3aed",   # violet — Cat 5
}


def fetch_recent_storm_tracks(
    basin: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return observed storm tracks for the current and recent hurricane seasons.

    Queries the Recent_Hurricanes_v1 live feed (NHC / JTWC source).

    Parameters
    ----------
    basin : optional str
        Filter to a specific basin — "AL" (Atlantic), "EP" (East Pacific),
        "CP" (Central Pacific), "WP" (West Pacific), etc.
        ``None`` returns all basins.

    Each returned dict has:
        storm_id   str    unique storm identifier
        name       str    storm name
        basin      str    basin code
        start_dtg  str    ISO-8601 start date/time or ""
        end_dtg    str    ISO-8601 end date/time or ""
        ss_max     int    peak Saffir-Simpson category (–1 to 6)
        category   str    human-readable peak intensity label
        color      str    NHC colour for the track line
        path       list   [[lat, lng], ...] observed track polyline
    """
    global _RECENT_TRACK_CACHE, _RECENT_TRACK_TS
    if _RECENT_TRACK_CACHE is not None and time.time() - _RECENT_TRACK_TS < _RECENT_TRACK_TTL:
        cached = _RECENT_TRACK_CACHE
        if basin:
            return [s for s in cached if s.get("basin", "").upper() == basin.upper()]
        return cached

    where = f"BASIN='{basin.upper()}'" if basin else "1=1"
    params = {
        "where":          where,
        "outFields":      "STORMID,STORMNAME,BASIN,STORMTYPE,SS,STARTDTG,ENDDTG",
        "outSR":          "4326",
        "returnGeometry": "true",
        "resultRecordCount": 500,
        "f":              "json",
    }

    try:
        resp = requests.get(_RECENT_TRACK_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("ArcGIS recent storm tracks fetch failed: %s", exc)
        return []

    results: List[Dict[str, Any]] = []
    for feat in data.get("features", []):
        attrs = feat.get("attributes", {})
        geom  = feat.get("geometry") or {}
        paths = geom.get("paths") or []
        if not paths:
            continue

        ss   = int(attrs.get("SS") or 0)
        name = (attrs.get("STORMNAME") or "Unknown").strip().title()

        results.append({
            "storm_id":  attrs.get("STORMID") or "",
            "name":      name,
            "basin":     (attrs.get("BASIN") or "").strip(),
            "start_dtg": _ms_to_iso(attrs.get("STARTDTG")),
            "end_dtg":   _ms_to_iso(attrs.get("ENDDTG")),
            "ss_max":    ss,
            "category":  _SS_LABELS.get(ss, "Unknown"),
            "color":     _SS_COLORS.get(ss, "#94a3b8"),
            "path":      [[pt[1], pt[0]] for pt in paths[0] if len(pt) >= 2],
        })

    # Sort by most recent start date first
    results.sort(key=lambda s: s["start_dtg"], reverse=True)

    _RECENT_TRACK_CACHE = results
    _RECENT_TRACK_TS    = time.time()

    if basin:
        return [s for s in results if s.get("basin", "").upper() == basin.upper()]
    return results


# ── Air Quality (PM2.5) ────────────────────────────────────────────────────────

# PM2.5 (µg/m³) breakpoints → AQI category
_PM25_BREAKPOINTS = [
    (0.0,   12.0,   "Good",                        "#22c55e"),
    (12.1,  35.4,   "Moderate",                    "#eab308"),
    (35.5,  55.4,   "Unhealthy for Sensitive",      "#f97316"),
    (55.5,  150.4,  "Unhealthy",                    "#ef4444"),
    (150.5, 250.4,  "Very Unhealthy",               "#a855f7"),
    (250.5, 9999.0, "Hazardous",                    "#7c3aed"),
]

_AQI_CACHE: Dict[tuple, Dict[str, Any]] = {}
_AQI_CACHE_TTL = 1800   # 30 minutes


def _aqi_key(lat: float, lng: float) -> tuple:
    return (round(lat, 1), round(lng, 1))


def _pm25_category(value: float) -> tuple:
    """Return (category_label, color_hex) for a PM2.5 reading in µg/m³."""
    for lo, hi, label, color in _PM25_BREAKPOINTS:
        if lo <= value <= hi:
            return label, color
    return "Unknown", "#94a3b8"


def fetch_air_quality(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """Return the nearest OpenAQ PM2.5 reading to the given coordinates.

    Searches within a ~0.5-degree (~55 km) bounding box; expands to ~1.0 degree
    if nothing is found in the first pass.

    Returns a dict or None if no station is within range:
        location    str    station name
        city        str    city/locality
        value       float  PM2.5 concentration in µg/m³
        unit        str    unit string from source
        updated     str    lastUpdated string from source
        category    str    e.g. "Good", "Moderate", "Unhealthy"
        color       str    hex colour for the category badge
        distance_km float  approximate distance to station
    """
    key = _aqi_key(lat, lng)
    cached = _AQI_CACHE.get(key)
    if cached and time.time() - cached["ts"] < _AQI_CACHE_TTL:
        return cached["data"]

    for pad in (0.5, 1.0, 2.0):
        geom = f"{lng - pad},{lat - pad},{lng + pad},{lat + pad}"
        params = {
            "where":          "value > 0",   # exclude broken / zero readings
            "geometry":       geom,
            "geometryType":   "esriGeometryEnvelope",
            "spatialRel":     "esriSpatialRelIntersects",
            "inSR":           "4326",
            "outSR":          "4326",
            "outFields":      "location,city,value,unit,lastUpdated,country_name",
            "returnGeometry": "true",
            "resultRecordCount": 20,
            "f":              "json",
        }
        try:
            resp = requests.get(_AQI_URL, params=params, timeout=(3.05, 12))
            resp.raise_for_status()
            feats = resp.json().get("features", [])
        except Exception as exc:
            logger.warning("ArcGIS AQI fetch failed (pad=%.1f): %s", pad, exc)
            break

        if not feats:
            continue  # try wider search

        # Find the closest station by Euclidean distance (good enough at this scale)
        best: Optional[Dict[str, Any]] = None
        best_dist = float("inf")
        for feat in feats:
            geom_obj = feat.get("geometry") or {}
            sx   = geom_obj.get("x", 0)
            sy   = geom_obj.get("y", 0)
            dist = ((sx - lng) ** 2 + (sy - lat) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best      = feat

        if best is None:
            break

        attrs = best["attributes"]
        raw   = float(attrs.get("value") or 0)
        cat, color = _pm25_category(raw)
        # 1 degree ≈ 111 km
        dist_km = round(best_dist * 111, 1)

        result: Dict[str, Any] = {
            "location":    attrs.get("location") or attrs.get("city") or "Unknown",
            "city":        attrs.get("city") or "",
            "value":       round(raw, 1),
            "unit":        attrs.get("unit") or "µg/m³",
            "updated":     attrs.get("lastUpdated") or "",
            "category":    cat,
            "color":       color,
            "distance_km": dist_km,
        }
        _AQI_CACHE[key] = {"ts": time.time(), "data": result}
        return result

    _AQI_CACHE[key] = {"ts": time.time(), "data": None}
    return None


# ── NDFD Wind Forecast ─────────────────────────────────────────────────────────

_WIND_FC_CACHE: Dict[tuple, Dict[str, Any]] = {}
_WIND_FC_TTL = 3600   # 1 hour — NDFD updates every 1-3 hours


def _wind_key(lat: float, lng: float) -> tuple:
    return (round(lat, 1), round(lng, 1))


def _deg_to_compass(deg: Optional[int]) -> str:
    """Convert wind direction in degrees to an 8-point compass abbreviation."""
    if deg is None:
        return ""
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round(int(deg) / 45) % 8
    return directions[idx]


def fetch_wind_forecast(lat: float, lng: float) -> List[Dict[str, Any]]:
    """Return NDFD wind forecast for the nearest city-level point.

    Queries NDFD_WindForecast_v1 FeatureServer layer 6 (City Level) in a
    small bounding box around the given coordinates, groups results by
    forecast interval, and returns up to 8 periods (≈ 24 hours).

    Each returned dict has:
        interval_start  str   ISO-8601 UTC forecast period start
        wind_dir_deg    int   wind direction in degrees (0–359)
        wind_dir        str   compass abbreviation ("N", "NE", etc.)
        wind_speed      int   sustained wind speed (knots)
        wind_gust       int   wind gust speed (knots; 0 if not available)
    """
    key = _wind_key(lat, lng)
    cached = _WIND_FC_CACHE.get(key)
    if cached and time.time() - cached["ts"] < _WIND_FC_TTL:
        return cached["data"]

    pad = 0.5   # ½ degree search radius (~55 km)
    geom = f"{lng - pad},{lat - pad},{lng + pad},{lat + pad}"
    params = {
        "where":          "1=1",
        "geometry":       geom,
        "geometryType":   "esriGeometryEnvelope",
        "spatialRel":     "esriSpatialRelIntersects",
        "inSR":           "4326",
        "outSR":          "4326",
        "outFields":      "IntervalStart,WindDir,WindSpeed,WindGust",
        "returnGeometry": "false",
        "orderByFields":  "IntervalStart ASC",
        "resultRecordCount": 200,
        "f":              "json",
    }

    try:
        resp = requests.get(_NDFD_WIND_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS NDFD wind forecast fetch failed: %s", exc)
        _WIND_FC_CACHE[key] = {"ts": time.time(), "data": []}
        return []

    # Group by IntervalStart: average across all city points returned in the bbox
    from collections import defaultdict
    buckets: Dict[int, list] = defaultdict(list)
    for feat in feats:
        attrs = feat.get("attributes", {})
        ts    = attrs.get("IntervalStart")
        if ts is None:
            continue
        buckets[int(ts)].append({
            "dir":   attrs.get("WindDir"),
            "speed": attrs.get("WindSpeed"),
            "gust":  attrs.get("WindGust"),
        })

    results: List[Dict[str, Any]] = []
    for ts_ms in sorted(buckets.keys()):
        entries = buckets[ts_ms]
        dirs    = [e["dir"]   for e in entries if e["dir"]   is not None]
        speeds  = [e["speed"] for e in entries if e["speed"] is not None]
        gusts   = [e["gust"]  for e in entries if e["gust"]  is not None]

        avg_dir   = round(sum(dirs)   / len(dirs))   if dirs   else None
        avg_speed = round(sum(speeds) / len(speeds)) if speeds else None
        avg_gust  = round(sum(gusts)  / len(gusts))  if gusts  else 0

        results.append({
            "interval_start": _ms_to_iso(ts_ms),
            "wind_dir_deg":   avg_dir,
            "wind_dir":       _deg_to_compass(avg_dir),
            "wind_speed":     avg_speed or 0,
            "wind_gust":      avg_gust,
        })
        if len(results) >= 8:   # 8 × 3-hour intervals = 24 hours
            break

    _WIND_FC_CACHE[key] = {"ts": time.time(), "data": results}
    return results


def cache_clear() -> None:
    """Clear all cached results.  Useful in tests."""
    global _STORM_CACHE, _STORM_TS, _RECENT_TRACK_CACHE, _RECENT_TRACK_TS
    _WARN_CACHE.clear()
    _STORM_CACHE = None
    _STORM_TS    = 0.0
    _RECENT_TRACK_CACHE = None
    _RECENT_TRACK_TS    = 0.0
    _AQI_CACHE.clear()
    _WIND_FC_CACHE.clear()
