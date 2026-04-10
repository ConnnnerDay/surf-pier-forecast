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
_NDFD_WIND_URL   = f"{_BASE}/NDFD_WindForecast_v1/FeatureServer/6/query"

# Coral Reef / SST stations – layer 0 = station points with live SST
_SST_URL         = f"{_BASE}/Coral_Reef_Stations/FeatureServer/0/query"

# Active wildfires – layer 0 = incident points
_FIRE_URL        = f"{_BASE}/USA_Wildfires_v1/FeatureServer/0/query"

# Smoke forecast – layer 0 = hourly smoke-density polygons (CONUS, 48 h)
_SMOKE_URL       = f"{_BASE}/NDGD_SmokeForecast_v1/FeatureServer/0/query"

# NDFD Precipitation – layer 0 = amount polygons per 6-h interval
_PRECIP_URL      = f"{_BASE}/NDFD_Precipitation_v1/FeatureServer/0/query"

# Arctic sea ice extent – monthly polygon boundary
_SEA_ICE_N_URL   = f"{_BASE}/seaice_extent_N_v1/FeatureServer/0/query"

# NDFD Daily Temperature – layer 0=Minimum, layer 1=Maximum (polygon, daily intervals)
_NDFD_TMIN_URL   = f"{_BASE}/NDFD_DailyTemperature_v1/FeatureServer/0/query"
_NDFD_TMAX_URL   = f"{_BASE}/NDFD_DailyTemperature_v1/FeatureServer/1/query"

# USGS Seismic Data – layer 0 = earthquake events (point, real-time)
_SEISMIC_URL     = f"{_BASE}/USGS_Seismic_Data_v1/FeatureServer/0/query"

# US Drought Intensity – layer 3 = current CONUS drought (DM 0-4 polygons)
_DROUGHT_URL     = f"{_BASE}/US_Drought_Intensity_v1/FeatureServer/3/query"

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


# ── SST / Coral Reef Stations ─────────────────────────────────────────────────

_SST_CACHE: Dict[tuple, Dict[str, Any]] = {}
_SST_CACHE_TTL = 1800   # 30 minutes

# Alert level → label + colour
_SST_ALERT = {
    0: ("No Stress",          "#22c55e"),
    1: ("Bleaching Watch",    "#eab308"),
    2: ("Bleaching Warning",  "#f97316"),
    3: ("Bleaching Alert 1",  "#ef4444"),
    4: ("Bleaching Alert 2",  "#7c3aed"),
}


def _sst_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 2), round(w, 2), round(n, 2), round(e, 2))


def fetch_sst_stations(
    south: float, west: float, north: float, east: float
) -> List[Dict[str, Any]]:
    """Return NOAA coral reef / SST monitoring stations in the bounding box.

    Each returned dict has:
        name        str    station name
        lat         float
        lng         float
        sst_c       float  sea-surface temperature in °C (None if unavailable)
        sst_f       float  sea-surface temperature in °F (None if unavailable)
        ssta        float  temperature anomaly in °C (+warming / −cooling)
        dhw         float  degree heating weeks (thermal stress accumulation)
        alert       int    bleaching alert level (0–4)
        alert_label str    human-readable alert description
        alert_color str    hex colour for the alert badge
        updated     str    ISO-8601 last-updated timestamp
    """
    key = _sst_key(south, west, north, east)
    cached = _SST_CACHE.get(key)
    if cached and time.time() - cached["ts"] < _SST_CACHE_TTL:
        return cached["data"]

    params = {
        "where":          "1=1",
        "geometry":       f"{west},{south},{east},{north}",
        "geometryType":   "esriGeometryEnvelope",
        "spatialRel":     "esriSpatialRelIntersects",
        "inSR":           "4326",
        "outSR":          "4326",
        "outFields":      "name,date,sst,ssta,hs,dhw,alert",
        "returnGeometry": "true",
        "resultRecordCount": 200,
        "f":              "json",
    }

    try:
        resp = requests.get(_SST_URL, params=params, timeout=(3.05, 12))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS SST stations fetch failed: %s", exc)
        return []

    results: List[Dict[str, Any]] = []
    for feat in feats:
        attrs  = feat.get("attributes", {})
        geom   = feat.get("geometry") or {}
        lat    = geom.get("y")
        lng    = geom.get("x")
        if lat is None or lng is None:
            continue

        try:
            sst_c = float(attrs.get("sst") or 0) or None
        except (ValueError, TypeError):
            sst_c = None
        sst_f = round(sst_c * 9 / 5 + 32, 1) if sst_c is not None else None

        try:
            ssta = float(attrs.get("ssta") or 0)
        except (ValueError, TypeError):
            ssta = 0.0

        try:
            dhw = float(attrs.get("dhw") or 0)
        except (ValueError, TypeError):
            dhw = 0.0

        alert = int(attrs.get("alert") or 0)
        label, color = _SST_ALERT.get(alert, ("Unknown", "#94a3b8"))

        results.append({
            "name":        (attrs.get("name") or "").strip(),
            "lat":         lat,
            "lng":         lng,
            "sst_c":       round(sst_c, 1) if sst_c is not None else None,
            "sst_f":       sst_f,
            "ssta":        round(ssta, 2),
            "dhw":         round(dhw, 1),
            "alert":       alert,
            "alert_label": label,
            "alert_color": color,
            "updated":     _ms_to_iso(attrs.get("date")),
        })

    _SST_CACHE[key] = {"ts": time.time(), "data": results}
    return results


# ── Active Wildfires ───────────────────────────────────────────────────────────

_FIRE_CACHE: Dict[tuple, Dict[str, Any]] = {}
_FIRE_CACHE_TTL = 900   # 15 minutes


def _fire_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))


def fetch_wildfire_incidents(
    south: float, west: float, north: float, east: float
) -> List[Dict[str, Any]]:
    """Return active wildfire incidents intersecting the bounding box.

    Each dict has:
        name         str    fire name
        state        str    state abbreviation
        county       str    county name
        acres        float  current acreage
        contained_pct float  percent contained (0–100)
        cause        str    fire cause (if known)
        discovered   str    ISO-8601 discovery date/time
        lat          float
        lng          float
        age_days     int    days since discovery
    """
    key = _fire_key(south, west, north, east)
    cached = _FIRE_CACHE.get(key)
    if cached and time.time() - cached["ts"] < _FIRE_CACHE_TTL:
        return cached["data"]

    params = {
        "where":          "IncidentTypeCategory='WF'",  # wildfire only (exclude Rx burns)
        "geometry":       f"{west},{south},{east},{north}",
        "geometryType":   "esriGeometryEnvelope",
        "spatialRel":     "esriSpatialRelIntersects",
        "inSR":           "4326",
        "outSR":          "4326",
        "outFields": (
            "IncidentName,POOState,POOCounty,DailyAcres,PercentContained,"
            "FireCauseGeneral,FireDiscoveryDateTime,FireDiscoveryAge"
        ),
        "returnGeometry": "true",
        "resultRecordCount": 300,
        "f":              "json",
    }

    try:
        resp = requests.get(_FIRE_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS wildfire fetch failed: %s", exc)
        return []

    results: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        geom  = feat.get("geometry") or {}
        lat   = geom.get("y")
        lng   = geom.get("x")
        if lat is None or lng is None:
            continue

        results.append({
            "name":          (attrs.get("IncidentName") or "Unknown Fire").strip().title(),
            "state":         (attrs.get("POOState") or "").strip(),
            "county":        (attrs.get("POOCounty") or "").strip(),
            "acres":         float(attrs.get("DailyAcres") or 0),
            "contained_pct": float(attrs.get("PercentContained") or 0),
            "cause":         (attrs.get("FireCauseGeneral") or "").strip(),
            "discovered":    _ms_to_iso(attrs.get("FireDiscoveryDateTime")),
            "age_days":      int(attrs.get("FireDiscoveryAge") or 0),
            "lat":           lat,
            "lng":           lng,
        })

    # Sort by size descending so the biggest fires are most prominent
    results.sort(key=lambda f: f["acres"], reverse=True)
    _FIRE_CACHE[key] = {"ts": time.time(), "data": results}
    return results


# ── Smoke Forecast ─────────────────────────────────────────────────────────────

_SMOKE_CACHE: Dict[tuple, Dict[str, Any]] = {}
_SMOKE_CACHE_TTL = 3600   # 1 hour — hourly forecast product

# Smoke class description → opacity and fill colour
_SMOKE_CLASSES = {
    "0-3":      {"fill": "#fef9c3", "opacity": 0.25, "label": "Light (0–3 µg/m³)"},
    "3-25":     {"fill": "#fde047", "opacity": 0.35, "label": "Moderate (3–25 µg/m³)"},
    "25-63":    {"fill": "#f97316", "opacity": 0.45, "label": "Heavy (25–63 µg/m³)"},
    "63-158":   {"fill": "#b45309", "opacity": 0.55, "label": "Dense (63–158 µg/m³)"},
    "158-1000": {"fill": "#7f1d1d", "opacity": 0.65, "label": "Extreme (>158 µg/m³)"},
}


def _smoke_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))


def _smoke_style(class_desc: str) -> Dict[str, Any]:
    """Map NDGD smoke class description to fill/opacity/label."""
    for key_frag, style in _SMOKE_CLASSES.items():
        if key_frag in (class_desc or ""):
            return style
    return {"fill": "#fef9c3", "opacity": 0.20, "label": class_desc or "Unknown"}


def fetch_smoke_forecast(
    south: float, west: float, north: float, east: float
) -> List[Dict[str, Any]]:
    """Return the current smoke forecast polygons intersecting the bounding box.

    Returns only the most recent hour's forecast polygons (lowest
    ``referencedate`` value in the result set).

    Each dict has:
        class_desc  str    smoke concentration class (e.g. "3-25")
        label       str    human-readable description
        fill        str    suggested polygon fill colour (hex)
        opacity     float  suggested polygon fill opacity (0–1)
        valid_from  str    ISO-8601 forecast reference time
        valid_to    str    ISO-8601 forecast end time
        rings       list   [[lat, lng], ...] polygon ring(s)
    """
    key = _smoke_key(south, west, north, east)
    cached = _SMOKE_CACHE.get(key)
    if cached and time.time() - cached["ts"] < _SMOKE_CACHE_TTL:
        return cached["data"]

    params = {
        "where":          "1=1",
        "geometry":       f"{west},{south},{east},{north}",
        "geometryType":   "esriGeometryEnvelope",
        "spatialRel":     "esriSpatialRelIntersects",
        "inSR":           "4326",
        "outSR":          "4326",
        "outFields":      "smoke_classdesc,referencedate,todate",
        "returnGeometry": "true",
        "resultRecordCount": 500,
        "f":              "json",
    }

    try:
        resp = requests.get(_SMOKE_URL, params=params, timeout=(3.05, 18))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS smoke forecast fetch failed: %s", exc)
        return []

    if not feats:
        _SMOKE_CACHE[key] = {"ts": time.time(), "data": []}
        return []

    # Find the most recent reference date
    ref_dates = [f["attributes"].get("referencedate") for f in feats if f.get("attributes")]
    ref_dates_valid = [d for d in ref_dates if d is not None]
    latest_ref = max(ref_dates_valid) if ref_dates_valid else None

    results: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        # Only include the current hour's polygons to avoid stacking
        if latest_ref is not None and attrs.get("referencedate") != latest_ref:
            continue
        geom  = feat.get("geometry") or {}
        rings = geom.get("rings") or []
        if not rings:
            continue
        cld   = (attrs.get("smoke_classdesc") or "").strip()
        style = _smoke_style(cld)
        results.append({
            "class_desc": cld,
            "label":      style["label"],
            "fill":       style["fill"],
            "opacity":    style["opacity"],
            "valid_from": _ms_to_iso(attrs.get("referencedate")),
            "valid_to":   _ms_to_iso(attrs.get("todate")),
            "rings":      [_ring_to_latlng(r) for r in rings],
        })

    _SMOKE_CACHE[key] = {"ts": time.time(), "data": results}
    return results


# ── Precipitation Forecast ─────────────────────────────────────────────────────

_PRECIP_CACHE: Dict[tuple, Dict[str, Any]] = {}
_PRECIP_CACHE_TTL = 3600   # 1 hour

# NDFD category integer (0–19) → approximate label when service label is missing
_PRECIP_CAT_LABEL = {
    0: "0.01–0.10\"", 1: "0.10–0.25\"", 2: "0.25–0.50\"", 3: "0.50–0.75\"",
    4: "0.75–1.00\"", 5: "1.00–1.25\"", 6: "1.25–1.50\"", 7: "1.50–2.00\"",
    8: "2.00–2.50\"", 9: "2.50–3.00\"", 10:"3.00–4.00\"", 11:"4.00–5.00\"",
    12:"5.00–6.00\"", 13:"6.00–8.00\"", 14:"8.00–10.0\"", 15:"10.0–15.0\"",
    16:"15.0–20.0\"", 17:"20.0–30.0\"", 18:"30.0–40.0\"", 19:">40.0\"",
}


def _precip_key(lat: float, lng: float) -> tuple:
    return (round(lat, 1), round(lng, 1))


def fetch_precip_forecast(lat: float, lng: float) -> List[Dict[str, Any]]:
    """Return NDFD precipitation forecast for the nearest area around lat/lng.

    Returns up to 4 periods (≈ 24 hours at 6-h intervals).

    Each dict has:
        from_time   str   ISO-8601 period start
        to_time     str   ISO-8601 period end
        category    int   NDFD rainfall category integer (0–19)
        label       str   rainfall amount range (e.g. "0.25–0.50\"")
        rain        bool  True if any precipitation expected this period
    """
    key = _precip_key(lat, lng)
    cached = _PRECIP_CACHE.get(key)
    if cached and time.time() - cached["ts"] < _PRECIP_CACHE_TTL:
        return cached["data"]

    pad = 0.5
    params = {
        "where":          "1=1",
        "geometry":       f"{lng - pad},{lat - pad},{lng + pad},{lat + pad}",
        "geometryType":   "esriGeometryEnvelope",
        "spatialRel":     "esriSpatialRelIntersects",
        "inSR":           "4326",
        "outSR":          "4326",
        "outFields":      "category,fromdate,todate,label",
        "returnGeometry": "false",
        "orderByFields":  "fromdate ASC",
        "resultRecordCount": 200,
        "f":              "json",
    }

    try:
        resp = requests.get(_PRECIP_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS precip forecast fetch failed: %s", exc)
        _PRECIP_CACHE[key] = {"ts": time.time(), "data": []}
        return []

    # Deduplicate by fromdate (multiple polygons may cover the area; take first hit)
    seen: set = set()
    results: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        fd    = attrs.get("fromdate")
        if fd in seen:
            continue
        seen.add(fd)
        cat   = int(attrs.get("category") or 0)
        lbl   = (attrs.get("label") or "").strip() or _PRECIP_CAT_LABEL.get(cat, "")
        results.append({
            "from_time": _ms_to_iso(fd),
            "to_time":   _ms_to_iso(attrs.get("todate")),
            "category":  cat,
            "label":     lbl,
            "rain":      cat > 0,
        })
        if len(results) >= 4:
            break

    _PRECIP_CACHE[key] = {"ts": time.time(), "data": results}
    return results


# ── Arctic Sea Ice Extent ─────────────────────────────────────────────────────

_SEA_ICE_CACHE: Optional[Dict[str, Any]] = None
_SEA_ICE_TS    = 0.0
_SEA_ICE_TTL   = 86400  # 24 hours — monthly product


def fetch_sea_ice_extent() -> Optional[Dict[str, Any]]:
    """Return the most recent Arctic sea ice extent polygon and statistics.

    Returns a dict or None:
        year        int    record year
        month       int    record month (1–12)
        area_mkm2   float  sea ice area in millions of km²
        extent_mkm2 float  sea ice extent in millions of km²
        rings       list   list of rings [[lat, lng], ...] for the boundary
    """
    global _SEA_ICE_CACHE, _SEA_ICE_TS
    if _SEA_ICE_CACHE is not None and time.time() - _SEA_ICE_TS < _SEA_ICE_TTL:
        return _SEA_ICE_CACHE

    params = {
        "where":          "1=1",
        "outFields":      "Rec_Year,Rec_Month,Rec_Area,Rec_Extent,Rec_Date",
        "returnGeometry": "true",
        "orderByFields":  "Rec_Date DESC",
        "resultRecordCount": 1,
        "outSR":          "4326",
        "f":              "json",
    }

    try:
        resp = requests.get(_SEA_ICE_N_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS sea ice fetch failed: %s", exc)
        _SEA_ICE_CACHE = None
        _SEA_ICE_TS    = time.time()
        return None

    if not feats:
        _SEA_ICE_CACHE = None
        _SEA_ICE_TS    = time.time()
        return None

    attrs = feats[0].get("attributes", {})
    geom  = feats[0].get("geometry") or {}
    rings = geom.get("rings") or []

    result: Dict[str, Any] = {
        "year":        int(attrs.get("Rec_Year") or 0),
        "month":       int(attrs.get("Rec_Month") or 0),
        "area_mkm2":   round(float(attrs.get("Rec_Area") or 0), 2),
        "extent_mkm2": round(float(attrs.get("Rec_Extent") or 0), 2),
        "rings":       [_ring_to_latlng(r) for r in rings],
    }

    _SEA_ICE_CACHE = result
    _SEA_ICE_TS    = time.time()
    return result


# ── NDFD Daily Temperature ─────────────────────────────────────────────────────

_TEMP_FC_CACHE: Dict[tuple, Dict[str, Any]] = {}
_TEMP_FC_TTL   = 3600   # 1 hour
_TEMP_FC_MAX   = 32


def _temp_key(lat: float, lng: float) -> tuple:
    return (round(lat, 2), round(lng, 2))


def fetch_temp_forecast(lat: float, lng: float) -> List[Dict]:
    """Return NDFD 5-7 day daily high/low temperature forecast for (lat, lng).

    Each item: { date (YYYY-MM-DD), min_f (int|None), max_f (int|None) }
    Layers 0 (Minimum) and 1 (Maximum) of NDFD_DailyTemperature_v1 are queried
    in parallel using a ±0.5° bounding box around the point.
    """
    k   = _temp_key(lat, lng)
    now = time.time()
    if k in _TEMP_FC_CACHE and now - _TEMP_FC_CACHE[k]["ts"] < _TEMP_FC_TTL:
        return _TEMP_FC_CACHE[k]["data"]

    pad   = 0.5
    geom  = f"{lng - pad},{lat - pad},{lng + pad},{lat + pad}"
    base  = {
        "geometry":           geom,
        "geometryType":       "esriGeometryEnvelope",
        "spatialRel":         "esriSpatialRelIntersects",
        "where":              "1=1",
        "outFields":          "Temp,Period",
        "returnGeometry":     "false",
        "outSR":              "4326",
        "f":                  "json",
    }

    results: Dict[str, Dict[str, Any]] = {}

    for url, field in [(_NDFD_TMIN_URL, "min_f"), (_NDFD_TMAX_URL, "max_f")]:
        try:
            resp = requests.get(url, params=base, timeout=(3.05, 20))
            resp.raise_for_status()
            feats = resp.json().get("features", [])
        except Exception as exc:
            logger.warning("ArcGIS NDFD temp fetch failed (%s): %s", url, exc)
            continue

        for feat in feats:
            attrs  = feat.get("attributes", {})
            period = attrs.get("Period")
            temp   = attrs.get("Temp")
            if period is None or temp is None:
                continue
            try:
                date_str = datetime.fromtimestamp(int(period) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                continue
            val = int(temp)
            if date_str not in results:
                results[date_str] = {"min_f": None, "max_f": None}
            cur = results[date_str][field]
            # Aggregate: keep coldest min, warmest max across overlapping polygons
            if cur is None:
                results[date_str][field] = val
            elif field == "min_f":
                results[date_str][field] = min(cur, val)
            else:
                results[date_str][field] = max(cur, val)

    data = [
        {"date": d, "min_f": v["min_f"], "max_f": v["max_f"]}
        for d, v in sorted(results.items())[:7]
    ]

    if len(_TEMP_FC_CACHE) >= _TEMP_FC_MAX:
        oldest = min(_TEMP_FC_CACHE, key=lambda x: _TEMP_FC_CACHE[x]["ts"])
        _TEMP_FC_CACHE.pop(oldest, None)
    _TEMP_FC_CACHE[k] = {"ts": now, "data": data}
    return data


# ── USGS Seismic Events ────────────────────────────────────────────────────────

_SEISMIC_CACHE: Dict[tuple, Dict[str, Any]] = {}
_SEISMIC_TTL   = 900   # 15 minutes
_SEISMIC_MAX   = 32

_ALERT_COLORS: Dict[str, str] = {
    "green":  "#4CAF50",
    "yellow": "#FFC107",
    "orange": "#FF9800",
    "red":    "#F44336",
}


def _seismic_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))


def fetch_seismic_events(south: float, west: float, north: float, east: float) -> List[Dict]:
    """Return USGS earthquake events (M ≥ 2.5) intersecting the bounding box.

    Each item: { lat, lng, mag, depth_km, place, time (ISO), hours_old,
                 tsunami (bool), alert, alert_color, sig, event_type }
    """
    k   = _seismic_key(south, west, north, east)
    now = time.time()
    if k in _SEISMIC_CACHE and now - _SEISMIC_CACHE[k]["ts"] < _SEISMIC_TTL:
        return _SEISMIC_CACHE[k]["data"]

    if len(_SEISMIC_CACHE) >= _SEISMIC_MAX:
        oldest = min(_SEISMIC_CACHE, key=lambda x: _SEISMIC_CACHE[x]["ts"])
        _SEISMIC_CACHE.pop(oldest, None)

    params = {
        "geometry":           f"{west},{south},{east},{north}",
        "geometryType":       "esriGeometryEnvelope",
        "spatialRel":         "esriSpatialRelIntersects",
        "where":              "mag >= 2.5",
        "outFields":          "mag,depth,eventTime,place,latitude,longitude,tsunami,alert,hoursOld,sig,eventType",
        "returnGeometry":     "false",
        "orderByFields":      "eventTime DESC",
        "resultRecordCount":  200,
        "outSR":              "4326",
        "f":                  "json",
    }

    try:
        resp  = requests.get(_SEISMIC_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS seismic fetch failed: %s", exc)
        _SEISMIC_CACHE[k] = {"ts": now, "data": []}
        return []

    data: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        lat   = attrs.get("latitude")
        lng   = attrs.get("longitude")
        if lat is None or lng is None:
            continue
        alert = str(attrs.get("alert") or "").lower()
        data.append({
            "lat":         float(lat),
            "lng":         float(lng),
            "mag":         round(float(attrs.get("mag") or 0), 1),
            "depth_km":    round(float(attrs.get("depth") or 0), 1),
            "place":       str(attrs.get("place") or ""),
            "time":        _ms_to_iso(attrs["eventTime"]) if attrs.get("eventTime") is not None else None,
            "hours_old":   int(attrs.get("hoursOld") or 0),
            "tsunami":     bool(attrs.get("tsunami")),
            "sig":         int(attrs.get("sig") or 0),
            "alert":       alert or None,
            "alert_color": _ALERT_COLORS.get(alert, "#9E9E9E"),
            "event_type":  str(attrs.get("eventType") or "earthquake"),
        })

    _SEISMIC_CACHE[k] = {"ts": now, "data": data}
    return data


# ── US Drought Intensity ───────────────────────────────────────────────────────

_DROUGHT_CACHE: Dict[tuple, Dict[str, Any]] = {}
_DROUGHT_TTL   = 21600  # 6 hours — drought data updates weekly
_DROUGHT_MAX   = 64

_DROUGHT_LABELS: Dict[int, tuple] = {
    0: ("D0", "Abnormally Dry"),
    1: ("D1", "Moderate Drought"),
    2: ("D2", "Severe Drought"),
    3: ("D3", "Extreme Drought"),
    4: ("D4", "Exceptional Drought"),
}
_DROUGHT_COLORS: Dict[int, str] = {
    0: "#FFFF00",   # yellow
    1: "#FCD37F",   # tan/buff
    2: "#FFAA00",   # orange
    3: "#E60000",   # red
    4: "#730000",   # dark maroon
}


def _drought_key(lat: float, lng: float) -> tuple:
    return (round(lat, 1), round(lng, 1))


def fetch_drought(lat: float, lng: float) -> Optional[Dict]:
    """Return current US Drought Monitor intensity at (lat, lng), or None outside CONUS.

    Returns { dm (-1=none, 0-4), code, label, color, date (YYYY-MM-DD),
              d0, d1, d2, d3, d4 (% area in each category) }
    When no drought polygon covers the point, dm=-1 and label='No Drought'.
    Outside the CONUS coverage area returns None.
    """
    k   = _drought_key(lat, lng)
    now = time.time()
    if k in _DROUGHT_CACHE and now - _DROUGHT_CACHE[k]["ts"] < _DROUGHT_TTL:
        return _DROUGHT_CACHE[k]["data"]

    if len(_DROUGHT_CACHE) >= _DROUGHT_MAX:
        oldest = min(_DROUGHT_CACHE, key=lambda x: _DROUGHT_CACHE[x]["ts"])
        _DROUGHT_CACHE.pop(oldest, None)

    params = {
        "geometry":           f"{lng},{lat}",
        "geometryType":       "esriGeometryPoint",
        "spatialRel":         "esriSpatialRelIntersects",
        "inSR":               "4326",
        "where":              "1=1",
        "outFields":          "dm,d0,d1,d2,d3,d4,ddate",
        "returnGeometry":     "false",
        "resultRecordCount":  1,
        "outSR":              "4326",
        "f":                  "json",
    }

    try:
        resp  = requests.get(_DROUGHT_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        body  = resp.json()
        feats = body.get("features", [])
        # Detect service error (e.g. layer not found)
        if body.get("error"):
            raise ValueError(body["error"].get("message", "service error"))
    except Exception as exc:
        logger.warning("ArcGIS drought fetch failed: %s", exc)
        _DROUGHT_CACHE[k] = {"ts": now, "data": None}
        return None

    if not feats:
        # Point is covered by CONUS extent but no active drought polygon
        result: Optional[Dict[str, Any]] = {
            "dm": -1, "code": "None", "label": "No Drought", "color": "#FFFFFF",
            "date": None, "d0": 0.0, "d1": 0.0, "d2": 0.0, "d3": 0.0, "d4": 0.0,
        }
        _DROUGHT_CACHE[k] = {"ts": now, "data": result}
        return result

    attrs = feats[0].get("attributes", {})
    dm    = int(attrs["dm"]) if attrs.get("dm") is not None else -1
    code, label = _DROUGHT_LABELS.get(dm, ("D?", "Unknown"))
    color        = _DROUGHT_COLORS.get(dm, "#9E9E9E")
    ddate        = attrs.get("ddate")
    date_str     = _ms_to_iso(ddate).split("T")[0] if ddate is not None else None

    result = {
        "dm":    dm,
        "code":  code,
        "label": label,
        "color": color,
        "date":  date_str,
        "d0":    round(float(attrs.get("d0") or 0), 1),
        "d1":    round(float(attrs.get("d1") or 0), 1),
        "d2":    round(float(attrs.get("d2") or 0), 1),
        "d3":    round(float(attrs.get("d3") or 0), 1),
        "d4":    round(float(attrs.get("d4") or 0), 1),
    }

    _DROUGHT_CACHE[k] = {"ts": now, "data": result}
    return result


def cache_clear() -> None:
    """Clear all cached results.  Useful in tests."""
    global _STORM_CACHE, _STORM_TS, _RECENT_TRACK_CACHE, _RECENT_TRACK_TS
    global _SEA_ICE_CACHE, _SEA_ICE_TS
    _WARN_CACHE.clear()
    _STORM_CACHE = None
    _STORM_TS    = 0.0
    _RECENT_TRACK_CACHE = None
    _RECENT_TRACK_TS    = 0.0
    _AQI_CACHE.clear()
    _WIND_FC_CACHE.clear()
    _SST_CACHE.clear()
    _FIRE_CACHE.clear()
    _SMOKE_CACHE.clear()
    _PRECIP_CACHE.clear()
    _SEA_ICE_CACHE = None
    _SEA_ICE_TS    = 0.0
    _TEMP_FC_CACHE.clear()
    _SEISMIC_CACHE.clear()
    _DROUGHT_CACHE.clear()
