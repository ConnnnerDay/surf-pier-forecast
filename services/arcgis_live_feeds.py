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
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

# Shared session with connection pooling so that TCP+TLS handshakes are reused
# across the many layer fetches that hit the same services9.arcgis.com host.
# All 27+ requests.get() calls in this module use _HTTP instead of bare
# requests.get(), saving ~50-200 ms of handshake overhead per call.
_HTTP: requests.Session = requests.Session()
_HTTP.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=0))
_HTTP.mount("http://", HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=0))

_BASE = "https://services9.arcgis.com/RHVPKKiFTONKtxq3/arcgis/rest/services"

# NWS Watches/Warnings – layer 6 = "Events Ordered by Size and Severity"
_WARNINGS_URL = f"{_BASE}/NWS_Watches_Warnings_v1/FeatureServer/6/query"

# Active Hurricanes layers
_STORM_POS_URL = (
    f"{_BASE}/Active_Hurricanes_v1/FeatureServer/0/query"  # Forecast Position
)
_STORM_TRACK_URL = (
    f"{_BASE}/Active_Hurricanes_v1/FeatureServer/2/query"  # Forecast Track
)
_STORM_CONE_URL = (
    f"{_BASE}/Active_Hurricanes_v1/FeatureServer/4/query"  # Forecast Error Cone
)

# Recent Hurricanes – layer 1 = Observed Track (polylines)
_RECENT_TRACK_URL = f"{_BASE}/Recent_Hurricanes_v1/FeatureServer/1/query"

# Air quality – layer 0 = OpenAQ PM2.5 monitoring stations
_AQI_URL = f"{_BASE}/Air_Quality_PM25_Latest_Results/FeatureServer/0/query"

# NDFD Wind Forecast – layer 6 = City Level (multipoint, 3-h intervals)
_NDFD_WIND_URL = f"{_BASE}/NDFD_WindForecast_v1/FeatureServer/6/query"

# Coral Reef / SST stations – layer 0 = station points with live SST
_SST_URL = f"{_BASE}/Coral_Reef_Stations/FeatureServer/0/query"

# Active wildfires – layer 0 = incident points
_FIRE_URL = f"{_BASE}/USA_Wildfires_v1/FeatureServer/0/query"

# Smoke forecast – layer 0 = hourly smoke-density polygons (CONUS, 48 h)
_SMOKE_URL = f"{_BASE}/NDGD_SmokeForecast_v1/FeatureServer/0/query"

# NDFD Precipitation – layer 0 = amount polygons per 6-h interval
_PRECIP_URL = f"{_BASE}/NDFD_Precipitation_v1/FeatureServer/0/query"

# Arctic sea ice extent – monthly polygon boundary
_SEA_ICE_N_URL = f"{_BASE}/seaice_extent_N_v1/FeatureServer/0/query"

# NDFD Daily Temperature – layer 0=Minimum, layer 1=Maximum (polygon, daily intervals)
_NDFD_TMIN_URL = f"{_BASE}/NDFD_DailyTemperature_v1/FeatureServer/0/query"
_NDFD_TMAX_URL = f"{_BASE}/NDFD_DailyTemperature_v1/FeatureServer/1/query"

# USGS Seismic Data – layer 0 = earthquake events (point, real-time)
_SEISMIC_URL = f"{_BASE}/USGS_Seismic_Data_v1/FeatureServer/0/query"

# US Drought Intensity – layer 3 = current CONUS drought (DM 0-4 polygons)
_DROUGHT_URL = f"{_BASE}/US_Drought_Intensity_v1/FeatureServer/3/query"

# NOAA METAR surface observations – layer 0 = current station readings
_METAR_URL = f"{_BASE}/NOAA_METAR_current_wind_speed_direction_v1/FeatureServer/0/query"

# Day/Night Terminator – layer 2 = night shadow polygon (updates every ~5 min)
_TERMINATOR_URL = f"{_BASE}/Day_Night_Terminator/FeatureServer/2/query"

# Live Stream Gauges – layer 0 = current water level / flood stage at gauges
_GAUGE_URL = f"{_BASE}/Live_Stream_Gauges_v1/FeatureServer/0/query"

# NOAA Storm Reports – layers 0=Hail, 1=Tornado, 2=Wind (past 24 hours)
_STORM_RPT_HAIL_URL = f"{_BASE}/NOAA_storm_reports_v1/FeatureServer/0/query"
_STORM_RPT_TORN_URL = f"{_BASE}/NOAA_storm_reports_v1/FeatureServer/1/query"
_STORM_RPT_WIND_URL = f"{_BASE}/NOAA_storm_reports_v1/FeatureServer/2/query"

# NDBC Weather Buoys – current ocean/coastal observations
_NDBC_URL = f"{_BASE}/NDBC_Observations_v1/FeatureServer/0/query"

# NOAA HF Radar surface currents – hourly velocity vectors
# Three regional services cover East Coast, Gulf of Mexico, West Coast.
# Layer 0 = hourly current vectors (speed cm/s, direction °, u/v components)
_HFRADAR_EAST_URL = f"{_BASE}/NOAA_HFRNet_US_East_Hourly_v1/FeatureServer/0/query"
_HFRADAR_GULF_URL = f"{_BASE}/NOAA_HFRNet_US_GoMex_Hourly_v1/FeatureServer/0/query"
_HFRADAR_WEST_URL = f"{_BASE}/NOAA_HFRNet_US_West_Hourly_v1/FeatureServer/0/query"

# NHC Tropical Weather Outlook – development-area polygons (layer 0)
_TROPICAL_OUTLOOK_URL = f"{_BASE}/NHC_Tropical_Weather_Outlook_v1/FeatureServer/0/query"

# Keywords that make a warning relevant to coastal/marine fishing
_MARINE_KEYWORDS = frozenset(
    {
        "marine",
        "gale",
        "storm warning",
        "hurricane force",
        "small craft",
        "coastal flood",
        "beach hazard",
        "rip current",
        "high wind",
        "dense fog",
        "special marine",
        "tsunami",
        "surf",
    }
)

# ── Caches ─────────────────────────────────────────────────────────────────────

_WARN_CACHE: Dict[tuple, Dict[str, Any]] = {}
_WARN_TTL = 600  # 10 minutes — warnings update frequently
_WARN_MAX = 64  # bbox combinations kept in memory

_STORM_CACHE: Optional[List[Dict[str, Any]]] = None
_STORM_TS = 0.0
_STORM_TTL = 600  # 10 minutes


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
    ev = event.lower()
    sev = severity.lower()
    if "extreme" in sev or "hurricane" in ev or "typhoon" in ev or "tornado" in ev:
        return "#ef4444"  # red
    if (
        "severe" in sev
        or "gale" in ev
        or "storm warning" in ev
        or "hurricane force" in ev
    ):
        return "#f97316"  # orange
    if "moderate" in sev or "small craft" in ev or "coastal flood warning" in ev:
        return "#eab308"  # yellow
    return "#60a5fa"  # blue — minor advisories


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
        "where": "1=1",
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": (
            "Event,Severity,Summary,Description,Instruction,"
            "Affected,End_,Updated,Urgency"
        ),
        "returnGeometry": "true",
        "resultRecordCount": 200,
        "f": "json",
    }

    try:
        resp = _HTTP.get(_WARNINGS_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("ArcGIS marine-warnings fetch failed: %s", exc)
        return []

    results: List[Dict[str, Any]] = []
    for feat in data.get("features", []):
        attrs = feat.get("attributes", {})
        event = attrs.get("Event") or ""
        geom = feat.get("geometry") or {}
        rings = geom.get("rings") or []
        if not rings:
            continue

        results.append(
            {
                "event": event,
                "severity": attrs.get("Severity") or "",
                "summary": attrs.get("Summary") or "",
                "description": attrs.get("Description") or "",
                "instruction": attrs.get("Instruction") or "",
                "affected": attrs.get("Affected") or "",
                "expires": _ms_to_iso(attrs.get("End_")),
                "color": _warning_color(attrs.get("Severity") or "", event),
                "marine": _is_marine(event),
                "rings": [_ring_to_latlng(r) for r in rings],
            }
        )

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
        "where": "1=1",
        "outSR": "4326",
        "returnGeometry": "true",
        "f": "json",
        "resultRecordCount": 50,
    }

    storms: Dict[str, Dict[str, Any]] = {}  # keyed by UPPER storm name

    # ── Step 1: Forecast positions ─────────────────────────────────────────────
    try:
        p = {**common, "outFields": "STORMNAME,STORMTYPE,INTENSITY,MSLP,ADVISNUM"}
        resp = _HTTP.get(_STORM_POS_URL, params=p, timeout=(3.05, 15))
        resp.raise_for_status()
        for feat in resp.json().get("features", []):
            attrs = feat.get("attributes", {})
            geom = feat.get("geometry") or {}
            name = (attrs.get("STORMNAME") or "Unknown").strip()
            key = name.upper()
            kt = int(attrs.get("INTENSITY") or 0)
            storms[key] = {
                "name": name.title(),
                "category": _category_label(kt),
                "lat": geom.get("y", 0),
                "lng": geom.get("x", 0),
                "wind_mph": round(kt * 1.15078),
                "pressure_mb": int(attrs.get("MSLP") or 0),
                "track": [],
                "cone": [],
            }
    except Exception as exc:
        logger.warning("ArcGIS storm positions fetch failed: %s", exc)
        _STORM_CACHE = []
        _STORM_TS = time.time()
        return []

    if not storms:
        _STORM_CACHE = []
        _STORM_TS = time.time()
        return []

    # ── Step 2: Forecast track ─────────────────────────────────────────────────
    try:
        p = {**common, "outFields": "STORMNAME"}
        resp = _HTTP.get(_STORM_TRACK_URL, params=p, timeout=(3.05, 15))
        resp.raise_for_status()
        for feat in resp.json().get("features", []):
            attrs = feat.get("attributes", {})
            name = (attrs.get("STORMNAME") or "").strip().upper()
            geom = feat.get("geometry") or {}
            paths = geom.get("paths") or []
            if name in storms and paths:
                storms[name]["track"] = [
                    [pt[1], pt[0]] for pt in paths[0] if len(pt) >= 2
                ]
    except Exception as exc:
        logger.warning("ArcGIS storm track fetch failed: %s", exc)

    # ── Step 3: Forecast uncertainty cone ─────────────────────────────────────
    try:
        p = {**common, "outFields": "STORMNAME"}
        resp = _HTTP.get(_STORM_CONE_URL, params=p, timeout=(3.05, 15))
        resp.raise_for_status()
        for feat in resp.json().get("features", []):
            attrs = feat.get("attributes", {})
            name = (attrs.get("STORMNAME") or "").strip().upper()
            geom = feat.get("geometry") or {}
            rings = geom.get("rings") or []
            if name in storms and rings:
                storms[name]["cone"] = [_ring_to_latlng(r) for r in rings]
    except Exception as exc:
        logger.warning("ArcGIS storm cone fetch failed: %s", exc)

    result = list(storms.values())
    _STORM_CACHE = result
    _STORM_TS = time.time()
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
_RECENT_TRACK_TS = 0.0
_RECENT_TRACK_TTL = 3600  # 1 hour — historical; updates a few times per day

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
    -1: "#94a3b8",  # grey — low/remnant
    0: "#94a3b8",  # grey — tropical depression
    1: "#3b82f6",  # blue — tropical storm
    2: "#22c55e",  # green — Cat 1
    3: "#f59e0b",  # amber — Cat 2
    4: "#f97316",  # orange — Cat 3
    5: "#ef4444",  # red — Cat 4
    6: "#7c3aed",  # violet — Cat 5
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
    if (
        _RECENT_TRACK_CACHE is not None
        and time.time() - _RECENT_TRACK_TS < _RECENT_TRACK_TTL
    ):
        cached = _RECENT_TRACK_CACHE
        if basin:
            return [s for s in cached if s.get("basin", "").upper() == basin.upper()]
        return cached

    where = f"BASIN='{basin.upper()}'" if basin else "1=1"
    params = {
        "where": where,
        "outFields": "STORMID,STORMNAME,BASIN,STORMTYPE,SS,STARTDTG,ENDDTG",
        "outSR": "4326",
        "returnGeometry": "true",
        "resultRecordCount": 500,
        "f": "json",
    }

    try:
        resp = _HTTP.get(_RECENT_TRACK_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("ArcGIS recent storm tracks fetch failed: %s", exc)
        return []

    results: List[Dict[str, Any]] = []
    for feat in data.get("features", []):
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry") or {}
        paths = geom.get("paths") or []
        if not paths:
            continue

        ss = int(attrs.get("SS") or 0)
        name = (attrs.get("STORMNAME") or "Unknown").strip().title()

        results.append(
            {
                "storm_id": attrs.get("STORMID") or "",
                "name": name,
                "basin": (attrs.get("BASIN") or "").strip(),
                "start_dtg": _ms_to_iso(attrs.get("STARTDTG")),
                "end_dtg": _ms_to_iso(attrs.get("ENDDTG")),
                "ss_max": ss,
                "category": _SS_LABELS.get(ss, "Unknown"),
                "color": _SS_COLORS.get(ss, "#94a3b8"),
                "path": [[pt[1], pt[0]] for pt in paths[0] if len(pt) >= 2],
            }
        )

    # Sort by most recent start date first
    results.sort(key=lambda s: s["start_dtg"], reverse=True)

    _RECENT_TRACK_CACHE = results
    _RECENT_TRACK_TS = time.time()

    if basin:
        return [s for s in results if s.get("basin", "").upper() == basin.upper()]
    return results


# ── Air Quality (PM2.5) ────────────────────────────────────────────────────────

# PM2.5 (µg/m³) breakpoints → AQI category
_PM25_BREAKPOINTS = [
    (0.0, 12.0, "Good", "#22c55e"),
    (12.1, 35.4, "Moderate", "#eab308"),
    (35.5, 55.4, "Unhealthy for Sensitive", "#f97316"),
    (55.5, 150.4, "Unhealthy", "#ef4444"),
    (150.5, 250.4, "Very Unhealthy", "#a855f7"),
    (250.5, 9999.0, "Hazardous", "#7c3aed"),
]

_AQI_CACHE: Dict[tuple, Dict[str, Any]] = {}
_AQI_CACHE_TTL = 1800  # 30 minutes
_AQI_CACHE_MAX = 32


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

    if len(_AQI_CACHE) >= _AQI_CACHE_MAX:
        oldest = min(_AQI_CACHE, key=lambda x: _AQI_CACHE[x]["ts"])
        _AQI_CACHE.pop(oldest, None)

    for pad in (0.5, 1.0, 2.0):
        geom = f"{lng - pad},{lat - pad},{lng + pad},{lat + pad}"
        params = {
            "where": "value > 0",  # exclude broken / zero readings
            "geometry": geom,
            "geometryType": "esriGeometryEnvelope",
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326",
            "outSR": "4326",
            "outFields": "location,city,value,unit,lastUpdated,country_name",
            "returnGeometry": "true",
            "resultRecordCount": 20,
            "f": "json",
        }
        try:
            resp = _HTTP.get(_AQI_URL, params=params, timeout=(3.05, 12))
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
            sx = geom_obj.get("x", 0)
            sy = geom_obj.get("y", 0)
            dist = ((sx - lng) ** 2 + (sy - lat) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best = feat

        if best is None:
            break

        attrs = best["attributes"]
        raw = float(attrs.get("value") or 0)
        cat, color = _pm25_category(raw)
        # 1 degree ≈ 111 km
        dist_km = round(best_dist * 111, 1)

        result: Dict[str, Any] = {
            "location": attrs.get("location") or attrs.get("city") or "Unknown",
            "city": attrs.get("city") or "",
            "value": round(raw, 1),
            "unit": attrs.get("unit") or "µg/m³",
            "updated": attrs.get("lastUpdated") or "",
            "category": cat,
            "color": color,
            "distance_km": dist_km,
        }
        _AQI_CACHE[key] = {"ts": time.time(), "data": result}
        return result

    _AQI_CACHE[key] = {"ts": time.time(), "data": None}
    return None


# ── NDFD Wind Forecast ─────────────────────────────────────────────────────────

_WIND_FC_CACHE: Dict[tuple, Dict[str, Any]] = {}
_WIND_FC_TTL = 3600  # 1 hour — NDFD updates every 1-3 hours
_WIND_FC_MAX = 32


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

    if len(_WIND_FC_CACHE) >= _WIND_FC_MAX:
        oldest = min(_WIND_FC_CACHE, key=lambda x: _WIND_FC_CACHE[x]["ts"])
        _WIND_FC_CACHE.pop(oldest, None)

    pad = 0.5  # ½ degree search radius (~55 km)
    geom = f"{lng - pad},{lat - pad},{lng + pad},{lat + pad}"
    params = {
        "where": "1=1",
        "geometry": geom,
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": "IntervalStart,WindDir,WindSpeed,WindGust",
        "returnGeometry": "false",
        "orderByFields": "IntervalStart ASC",
        "resultRecordCount": 200,
        "f": "json",
    }

    try:
        resp = _HTTP.get(_NDFD_WIND_URL, params=params, timeout=(3.05, 15))
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
        ts = attrs.get("IntervalStart")
        if ts is None:
            continue
        buckets[int(ts)].append(
            {
                "dir": attrs.get("WindDir"),
                "speed": attrs.get("WindSpeed"),
                "gust": attrs.get("WindGust"),
            }
        )

    results: List[Dict[str, Any]] = []
    for ts_ms in sorted(buckets.keys()):
        entries = buckets[ts_ms]
        dirs = [e["dir"] for e in entries if e["dir"] is not None]
        speeds = [e["speed"] for e in entries if e["speed"] is not None]
        gusts = [e["gust"] for e in entries if e["gust"] is not None]

        avg_dir = round(sum(dirs) / len(dirs)) if dirs else None
        avg_speed = round(sum(speeds) / len(speeds)) if speeds else None
        avg_gust = round(sum(gusts) / len(gusts)) if gusts else 0

        results.append(
            {
                "interval_start": _ms_to_iso(ts_ms),
                "wind_dir_deg": avg_dir,
                "wind_dir": _deg_to_compass(avg_dir),
                "wind_speed": avg_speed or 0,
                "wind_gust": avg_gust,
            }
        )
        if len(results) >= 8:  # 8 × 3-hour intervals = 24 hours
            break

    _WIND_FC_CACHE[key] = {"ts": time.time(), "data": results}
    return results


# ── SST / Coral Reef Stations ─────────────────────────────────────────────────

_SST_CACHE: Dict[tuple, Dict[str, Any]] = {}
_SST_CACHE_TTL = 1800  # 30 minutes
_SST_CACHE_MAX = 64

# Alert level → label + colour
_SST_ALERT = {
    0: ("No Stress", "#22c55e"),
    1: ("Bleaching Watch", "#eab308"),
    2: ("Bleaching Warning", "#f97316"),
    3: ("Bleaching Alert 1", "#ef4444"),
    4: ("Bleaching Alert 2", "#7c3aed"),
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

    if len(_SST_CACHE) >= _SST_CACHE_MAX:
        oldest = min(_SST_CACHE, key=lambda x: _SST_CACHE[x]["ts"])
        _SST_CACHE.pop(oldest, None)

    params = {
        "where": "1=1",
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": "name,date,sst,ssta,hs,dhw,alert",
        "returnGeometry": "true",
        "resultRecordCount": 200,
        "f": "json",
    }

    try:
        resp = _HTTP.get(_SST_URL, params=params, timeout=(3.05, 12))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS SST stations fetch failed: %s", exc)
        return []

    results: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry") or {}
        lat = geom.get("y")
        lng = geom.get("x")
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

        results.append(
            {
                "name": (attrs.get("name") or "").strip(),
                "lat": lat,
                "lng": lng,
                "sst_c": round(sst_c, 1) if sst_c is not None else None,
                "sst_f": sst_f,
                "ssta": round(ssta, 2),
                "dhw": round(dhw, 1),
                "alert": alert,
                "alert_label": label,
                "alert_color": color,
                "updated": _ms_to_iso(attrs.get("date")),
            }
        )

    _SST_CACHE[key] = {"ts": time.time(), "data": results}
    return results


# ── Active Wildfires ───────────────────────────────────────────────────────────

_FIRE_CACHE: Dict[tuple, Dict[str, Any]] = {}
_FIRE_CACHE_TTL = 900  # 15 minutes
_FIRE_CACHE_MAX = 32


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

    if len(_FIRE_CACHE) >= _FIRE_CACHE_MAX:
        oldest = min(_FIRE_CACHE, key=lambda x: _FIRE_CACHE[x]["ts"])
        _FIRE_CACHE.pop(oldest, None)

    params = {
        "where": "IncidentTypeCategory='WF'",  # wildfire only (exclude Rx burns)
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": (
            "IncidentName,POOState,POOCounty,DailyAcres,PercentContained,"
            "FireCauseGeneral,FireDiscoveryDateTime,FireDiscoveryAge"
        ),
        "returnGeometry": "true",
        "resultRecordCount": 300,
        "f": "json",
    }

    try:
        resp = _HTTP.get(_FIRE_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS wildfire fetch failed: %s", exc)
        return []

    results: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry") or {}
        lat = geom.get("y")
        lng = geom.get("x")
        if lat is None or lng is None:
            continue

        results.append(
            {
                "name": (attrs.get("IncidentName") or "Unknown Fire").strip().title(),
                "state": (attrs.get("POOState") or "").strip(),
                "county": (attrs.get("POOCounty") or "").strip(),
                "acres": float(attrs.get("DailyAcres") or 0),
                "contained_pct": float(attrs.get("PercentContained") or 0),
                "cause": (attrs.get("FireCauseGeneral") or "").strip(),
                "discovered": _ms_to_iso(attrs.get("FireDiscoveryDateTime")),
                "age_days": int(attrs.get("FireDiscoveryAge") or 0),
                "lat": lat,
                "lng": lng,
            }
        )

    # Sort by size descending so the biggest fires are most prominent
    results.sort(key=lambda f: f["acres"], reverse=True)
    _FIRE_CACHE[key] = {"ts": time.time(), "data": results}
    return results


# ── Smoke Forecast ─────────────────────────────────────────────────────────────

_SMOKE_CACHE: Dict[tuple, Dict[str, Any]] = {}
_SMOKE_CACHE_TTL = 3600  # 1 hour — hourly forecast product
_SMOKE_CACHE_MAX = 32

# Smoke class description → opacity and fill colour
_SMOKE_CLASSES = {
    "0-3": {"fill": "#fef9c3", "opacity": 0.25, "label": "Light (0–3 µg/m³)"},
    "3-25": {"fill": "#fde047", "opacity": 0.35, "label": "Moderate (3–25 µg/m³)"},
    "25-63": {"fill": "#f97316", "opacity": 0.45, "label": "Heavy (25–63 µg/m³)"},
    "63-158": {"fill": "#b45309", "opacity": 0.55, "label": "Dense (63–158 µg/m³)"},
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

    if len(_SMOKE_CACHE) >= _SMOKE_CACHE_MAX:
        oldest = min(_SMOKE_CACHE, key=lambda x: _SMOKE_CACHE[x]["ts"])
        _SMOKE_CACHE.pop(oldest, None)

    params = {
        "where": "1=1",
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": "smoke_classdesc,referencedate,todate",
        "returnGeometry": "true",
        "resultRecordCount": 500,
        "f": "json",
    }

    try:
        resp = _HTTP.get(_SMOKE_URL, params=params, timeout=(3.05, 18))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS smoke forecast fetch failed: %s", exc)
        return []

    if not feats:
        _SMOKE_CACHE[key] = {"ts": time.time(), "data": []}
        return []

    # Find the most recent reference date
    ref_dates = [
        f["attributes"].get("referencedate") for f in feats if f.get("attributes")
    ]
    ref_dates_valid = [d for d in ref_dates if d is not None]
    latest_ref = max(ref_dates_valid) if ref_dates_valid else None

    results: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        # Only include the current hour's polygons to avoid stacking
        if latest_ref is not None and attrs.get("referencedate") != latest_ref:
            continue
        geom = feat.get("geometry") or {}
        rings = geom.get("rings") or []
        if not rings:
            continue
        cld = (attrs.get("smoke_classdesc") or "").strip()
        style = _smoke_style(cld)
        results.append(
            {
                "class_desc": cld,
                "label": style["label"],
                "fill": style["fill"],
                "opacity": style["opacity"],
                "valid_from": _ms_to_iso(attrs.get("referencedate")),
                "valid_to": _ms_to_iso(attrs.get("todate")),
                "rings": [_ring_to_latlng(r) for r in rings],
            }
        )

    _SMOKE_CACHE[key] = {"ts": time.time(), "data": results}
    return results


# ── Precipitation Forecast ─────────────────────────────────────────────────────

_PRECIP_CACHE: Dict[tuple, Dict[str, Any]] = {}
_PRECIP_CACHE_TTL = 3600  # 1 hour
_PRECIP_CACHE_MAX = 32

# NDFD category integer (0–19) → approximate label when service label is missing
_PRECIP_CAT_LABEL = {
    0: '0.01–0.10"',
    1: '0.10–0.25"',
    2: '0.25–0.50"',
    3: '0.50–0.75"',
    4: '0.75–1.00"',
    5: '1.00–1.25"',
    6: '1.25–1.50"',
    7: '1.50–2.00"',
    8: '2.00–2.50"',
    9: '2.50–3.00"',
    10: '3.00–4.00"',
    11: '4.00–5.00"',
    12: '5.00–6.00"',
    13: '6.00–8.00"',
    14: '8.00–10.0"',
    15: '10.0–15.0"',
    16: '15.0–20.0"',
    17: '20.0–30.0"',
    18: '30.0–40.0"',
    19: '>40.0"',
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

    if len(_PRECIP_CACHE) >= _PRECIP_CACHE_MAX:
        oldest = min(_PRECIP_CACHE, key=lambda x: _PRECIP_CACHE[x]["ts"])
        _PRECIP_CACHE.pop(oldest, None)

    pad = 0.5
    params = {
        "where": "1=1",
        "geometry": f"{lng - pad},{lat - pad},{lng + pad},{lat + pad}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": "category,fromdate,todate,label",
        "returnGeometry": "false",
        "orderByFields": "fromdate ASC",
        "resultRecordCount": 200,
        "f": "json",
    }

    try:
        resp = _HTTP.get(_PRECIP_URL, params=params, timeout=(3.05, 15))
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
        fd = attrs.get("fromdate")
        if fd in seen:
            continue
        seen.add(fd)
        cat = int(attrs.get("category") or 0)
        lbl = (attrs.get("label") or "").strip() or _PRECIP_CAT_LABEL.get(cat, "")
        results.append(
            {
                "from_time": _ms_to_iso(fd),
                "to_time": _ms_to_iso(attrs.get("todate")),
                "category": cat,
                "label": lbl,
                "rain": cat > 0,
            }
        )
        if len(results) >= 4:
            break

    _PRECIP_CACHE[key] = {"ts": time.time(), "data": results}
    return results


# ── Arctic Sea Ice Extent ─────────────────────────────────────────────────────

_SEA_ICE_CACHE: Optional[Dict[str, Any]] = None
_SEA_ICE_TS = 0.0
_SEA_ICE_TTL = 86400  # 24 hours — monthly product


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
        "where": "1=1",
        "outFields": "Rec_Year,Rec_Month,Rec_Area,Rec_Extent,Rec_Date",
        "returnGeometry": "true",
        "orderByFields": "Rec_Date DESC",
        "resultRecordCount": 1,
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_SEA_ICE_N_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS sea ice fetch failed: %s", exc)
        _SEA_ICE_CACHE = None
        _SEA_ICE_TS = time.time()
        return None

    if not feats:
        _SEA_ICE_CACHE = None
        _SEA_ICE_TS = time.time()
        return None

    attrs = feats[0].get("attributes", {})
    geom = feats[0].get("geometry") or {}
    rings = geom.get("rings") or []

    result: Dict[str, Any] = {
        "year": int(attrs.get("Rec_Year") or 0),
        "month": int(attrs.get("Rec_Month") or 0),
        "area_mkm2": round(float(attrs.get("Rec_Area") or 0), 2),
        "extent_mkm2": round(float(attrs.get("Rec_Extent") or 0), 2),
        "rings": [_ring_to_latlng(r) for r in rings],
    }

    _SEA_ICE_CACHE = result
    _SEA_ICE_TS = time.time()
    return result


# ── NDFD Daily Temperature ─────────────────────────────────────────────────────

_TEMP_FC_CACHE: Dict[tuple, Dict[str, Any]] = {}
_TEMP_FC_TTL = 3600  # 1 hour
_TEMP_FC_MAX = 32


def _temp_key(lat: float, lng: float) -> tuple:
    return (round(lat, 2), round(lng, 2))


def fetch_temp_forecast(lat: float, lng: float) -> List[Dict]:
    """Return NDFD 5-7 day daily high/low temperature forecast for (lat, lng).

    Each item: { date (YYYY-MM-DD), min_f (int|None), max_f (int|None) }
    Layers 0 (Minimum) and 1 (Maximum) of NDFD_DailyTemperature_v1 are queried
    in parallel using a ±0.5° bounding box around the point.
    """
    k = _temp_key(lat, lng)
    now = time.time()
    if k in _TEMP_FC_CACHE and now - _TEMP_FC_CACHE[k]["ts"] < _TEMP_FC_TTL:
        return _TEMP_FC_CACHE[k]["data"]

    pad = 0.5
    geom = f"{lng - pad},{lat - pad},{lng + pad},{lat + pad}"
    base = {
        "geometry": geom,
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "where": "1=1",
        "outFields": "Temp,Period",
        "returnGeometry": "false",
        "outSR": "4326",
        "f": "json",
    }

    results: Dict[str, Dict[str, Any]] = {}

    for url, field in [(_NDFD_TMIN_URL, "min_f"), (_NDFD_TMAX_URL, "max_f")]:
        try:
            resp = _HTTP.get(url, params=base, timeout=(3.05, 20))
            resp.raise_for_status()
            feats = resp.json().get("features", [])
        except Exception as exc:
            logger.warning("ArcGIS NDFD temp fetch failed (%s): %s", url, exc)
            continue

        for feat in feats:
            attrs = feat.get("attributes", {})
            period = attrs.get("Period")
            temp = attrs.get("Temp")
            if period is None or temp is None:
                continue
            try:
                date_str = datetime.fromtimestamp(
                    int(period) / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d")
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
_SEISMIC_TTL = 900  # 15 minutes
_SEISMIC_MAX = 32

_ALERT_COLORS: Dict[str, str] = {
    "green": "#4CAF50",
    "yellow": "#FFC107",
    "orange": "#FF9800",
    "red": "#F44336",
}


def _seismic_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))


def fetch_seismic_events(
    south: float, west: float, north: float, east: float
) -> List[Dict]:
    """Return USGS earthquake events (M ≥ 2.5) intersecting the bounding box.

    Each item: { lat, lng, mag, depth_km, place, time (ISO), hours_old,
                 tsunami (bool), alert, alert_color, sig, event_type }
    """
    k = _seismic_key(south, west, north, east)
    now = time.time()
    if k in _SEISMIC_CACHE and now - _SEISMIC_CACHE[k]["ts"] < _SEISMIC_TTL:
        return _SEISMIC_CACHE[k]["data"]

    if len(_SEISMIC_CACHE) >= _SEISMIC_MAX:
        oldest = min(_SEISMIC_CACHE, key=lambda x: _SEISMIC_CACHE[x]["ts"])
        _SEISMIC_CACHE.pop(oldest, None)

    params = {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "where": "mag >= 2.5",
        "outFields": "mag,depth,eventTime,place,latitude,longitude,tsunami,alert,hoursOld,sig,eventType",
        "returnGeometry": "false",
        "orderByFields": "eventTime DESC",
        "resultRecordCount": 200,
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_SEISMIC_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS seismic fetch failed: %s", exc)
        _SEISMIC_CACHE[k] = {"ts": now, "data": []}
        return []

    data: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        lat = attrs.get("latitude")
        lng = attrs.get("longitude")
        if lat is None or lng is None:
            continue
        alert = str(attrs.get("alert") or "").lower()
        data.append(
            {
                "lat": float(lat),
                "lng": float(lng),
                "mag": round(float(attrs.get("mag") or 0), 1),
                "depth_km": round(float(attrs.get("depth") or 0), 1),
                "place": str(attrs.get("place") or ""),
                "time": _ms_to_iso(attrs["eventTime"])
                if attrs.get("eventTime") is not None
                else None,
                "hours_old": int(attrs.get("hoursOld") or 0),
                "tsunami": bool(attrs.get("tsunami")),
                "sig": int(attrs.get("sig") or 0),
                "alert": alert or None,
                "alert_color": _ALERT_COLORS.get(alert, "#9E9E9E"),
                "event_type": str(attrs.get("eventType") or "earthquake"),
            }
        )

    _SEISMIC_CACHE[k] = {"ts": now, "data": data}
    return data


# ── US Drought Intensity ───────────────────────────────────────────────────────

_DROUGHT_CACHE: Dict[tuple, Dict[str, Any]] = {}
_DROUGHT_TTL = 21600  # 6 hours — drought data updates weekly
_DROUGHT_MAX = 64

_DROUGHT_LABELS: Dict[int, tuple] = {
    0: ("D0", "Abnormally Dry"),
    1: ("D1", "Moderate Drought"),
    2: ("D2", "Severe Drought"),
    3: ("D3", "Extreme Drought"),
    4: ("D4", "Exceptional Drought"),
}
_DROUGHT_COLORS: Dict[int, str] = {
    0: "#FFFF00",  # yellow
    1: "#FCD37F",  # tan/buff
    2: "#FFAA00",  # orange
    3: "#E60000",  # red
    4: "#730000",  # dark maroon
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
    k = _drought_key(lat, lng)
    now = time.time()
    if k in _DROUGHT_CACHE and now - _DROUGHT_CACHE[k]["ts"] < _DROUGHT_TTL:
        return _DROUGHT_CACHE[k]["data"]

    if len(_DROUGHT_CACHE) >= _DROUGHT_MAX:
        oldest = min(_DROUGHT_CACHE, key=lambda x: _DROUGHT_CACHE[x]["ts"])
        _DROUGHT_CACHE.pop(oldest, None)

    params = {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "where": "1=1",
        "outFields": "dm,d0,d1,d2,d3,d4,ddate",
        "returnGeometry": "false",
        "resultRecordCount": 1,
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_DROUGHT_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        body = resp.json()
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
            "dm": -1,
            "code": "None",
            "label": "No Drought",
            "color": "#FFFFFF",
            "date": None,
            "d0": 0.0,
            "d1": 0.0,
            "d2": 0.0,
            "d3": 0.0,
            "d4": 0.0,
        }
        _DROUGHT_CACHE[k] = {"ts": now, "data": result}
        return result

    attrs = feats[0].get("attributes", {})
    dm = int(attrs["dm"]) if attrs.get("dm") is not None else -1
    code, label = _DROUGHT_LABELS.get(dm, ("D?", "Unknown"))
    color = _DROUGHT_COLORS.get(dm, "#9E9E9E")
    ddate = attrs.get("ddate")
    date_str = _ms_to_iso(ddate).split("T")[0] if ddate is not None else None

    result = {
        "dm": dm,
        "code": code,
        "label": label,
        "color": color,
        "date": date_str,
        "d0": round(float(attrs.get("d0") or 0), 1),
        "d1": round(float(attrs.get("d1") or 0), 1),
        "d2": round(float(attrs.get("d2") or 0), 1),
        "d3": round(float(attrs.get("d3") or 0), 1),
        "d4": round(float(attrs.get("d4") or 0), 1),
    }

    _DROUGHT_CACHE[k] = {"ts": now, "data": result}
    return result


# ── NOAA METAR Surface Observations ───────────────────────────────────────────

_METAR_CACHE: Dict[tuple, Dict[str, Any]] = {}
_METAR_TTL = 1800  # 30 minutes — METAR updates hourly
_METAR_MAX = 32

_FLT_CAT_COLORS: Dict[str, str] = {
    "VFR": "#22c55e",  # green  — clear flying conditions
    "MVFR": "#60a5fa",  # blue   — marginal VFR
    "IFR": "#f87171",  # red    — instrument conditions / low visibility
    "LIFR": "#c084fc",  # purple — low instrument conditions / fog
}


def _metar_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))


def fetch_metar_stations(
    south: float, west: float, north: float, east: float
) -> List[Dict]:
    """Return current NOAA METAR surface observations intersecting the bounding box.

    Each item: { icao, name, lat, lng, observed (ISO), temp_f, dew_f, humidity,
                 wind_deg, wind_kt, gust_kt, wind_dir, visibility_m, pressure_mb,
                 sky, weather, heat_index_f, wind_chill_f, flight_cat, cat_color }
    Wind speed is converted from km/h → knots (÷ 1.852).
    """
    k = _metar_key(south, west, north, east)
    now = time.time()
    if k in _METAR_CACHE and now - _METAR_CACHE[k]["ts"] < _METAR_TTL:
        return _METAR_CACHE[k]["data"]

    if len(_METAR_CACHE) >= _METAR_MAX:
        oldest = min(_METAR_CACHE, key=lambda x: _METAR_CACHE[x]["ts"])
        _METAR_CACHE.pop(oldest, None)

    params = {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "where": "1=1",
        "outFields": (
            "ICAO,STATION_NAME,OBS_DATETIME,TEMP,DEW_POINT,R_HUMIDITY,"
            "WIND_DIRECT,WIND_SPEED,WIND_GUST,WIND_CHILL,VISIBILITY,"
            "PRESSURE,SKY_CONDTN,WEATHER,HEAT_INDEX,LATITUDE,LONGITUDE,"
            "FLT_CATEGORY"
        ),
        "returnGeometry": "false",
        "resultRecordCount": 300,
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_METAR_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS METAR fetch failed: %s", exc)
        _METAR_CACHE[k] = {"ts": now, "data": []}
        return []

    data: List[Dict[str, Any]] = []
    for feat in feats:
        a = feat.get("attributes", {})
        lat = a.get("LATITUDE")
        lng = a.get("LONGITUDE")
        if lat is None or lng is None:
            continue
        # km/h → knots
        spd_kmh = a.get("WIND_SPEED")
        gust_kmh = a.get("WIND_GUST")
        spd_kt = round(float(spd_kmh) / 1.852, 1) if spd_kmh is not None else None
        gust_kt = round(float(gust_kmh) / 1.852, 1) if gust_kmh is not None else None
        wind_deg = a.get("WIND_DIRECT")
        cat = str(a.get("FLT_CATEGORY") or "").strip().upper()
        data.append(
            {
                "icao": str(a.get("ICAO") or ""),
                "name": str(a.get("STATION_NAME") or ""),
                "lat": float(lat),
                "lng": float(lng),
                "observed": _ms_to_iso(a["OBS_DATETIME"])
                if a.get("OBS_DATETIME") is not None
                else None,
                "temp_f": round(float(a["TEMP"]), 1)
                if a.get("TEMP") is not None
                else None,
                "dew_f": round(float(a["DEW_POINT"]), 1)
                if a.get("DEW_POINT") is not None
                else None,
                "humidity": int(a["R_HUMIDITY"])
                if a.get("R_HUMIDITY") is not None
                else None,
                "wind_deg": int(wind_deg) if wind_deg is not None else None,
                "wind_dir": _deg_to_compass(wind_deg) if wind_deg is not None else None,
                "wind_kt": spd_kt,
                "gust_kt": gust_kt,
                "wind_chill_f": round(float(a["WIND_CHILL"]), 1)
                if a.get("WIND_CHILL") is not None
                else None,
                "heat_index_f": round(float(a["HEAT_INDEX"]), 1)
                if a.get("HEAT_INDEX") is not None
                else None,
                "visibility_m": int(a["VISIBILITY"])
                if a.get("VISIBILITY") is not None
                else None,
                "pressure_mb": round(float(a["PRESSURE"]), 1)
                if a.get("PRESSURE") is not None
                else None,
                "sky": str(a.get("SKY_CONDTN") or ""),
                "weather": str(a.get("WEATHER") or ""),
                "flight_cat": cat or None,
                "cat_color": _FLT_CAT_COLORS.get(cat, "#9ca3af"),
            }
        )

    _METAR_CACHE[k] = {"ts": now, "data": data}
    return data


# ── Day/Night Terminator ───────────────────────────────────────────────────────

_TERM_CACHE: Optional[Dict[str, Any]] = None
_TERM_TS = 0.0
_TERM_TTL = 300  # 5 minutes — the subsolar point moves ~0.07°/min


def fetch_terminator() -> Optional[Dict]:
    """Return the current night-shadow polygon (Day/Night Terminator).

    Returns { rings ([[lat,lng]]), timestamp (ISO) } or None on error.
    The polygon covers the dark (night) half of the Earth.
    """
    global _TERM_CACHE, _TERM_TS
    now = time.time()
    if _TERM_CACHE is not None and now - _TERM_TS < _TERM_TTL:
        return _TERM_CACHE

    params = {
        "where": "1=1",
        "outFields": "timestamp",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_TERMINATOR_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS terminator fetch failed: %s", exc)
        _TERM_CACHE = None
        _TERM_TS = now
        return None

    if not feats:
        _TERM_CACHE = None
        _TERM_TS = now
        return None

    feat = feats[0]
    attrs = feat.get("attributes", {})
    geom = feat.get("geometry") or {}
    rings = geom.get("rings") or []

    ts_ms = attrs.get("timestamp")
    _TERM_CACHE = {
        "rings": [_ring_to_latlng(r) for r in rings],
        "timestamp": _ms_to_iso(ts_ms) if ts_ms is not None else None,
    }
    _TERM_TS = now
    return _TERM_CACHE


# ── Live Stream Gauges ─────────────────────────────────────────────────────────

_GAUGE_CACHE: Dict[tuple, Dict[str, Any]] = {}
_GAUGE_TTL = 900  # 15 minutes
_GAUGE_MAX = 32

# Map statusClass integer to a human label and colour
_GAUGE_STATUS: Dict[int, tuple] = {
    0: ("Normal", "#22c55e"),
    1: ("Action Stage", "#facc15"),
    2: ("Minor Flood", "#f97316"),
    3: ("Moderate Flood", "#ef4444"),
    4: ("Major Flood", "#9f1239"),
}


def _gauge_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))


def fetch_stream_gauges(
    south: float, west: float, north: float, east: float
) -> List[Dict]:
    """Return live USGS/NWS stream gauge readings intersecting the bounding box.

    Each item: { id, name, lat, lng, stage_ft, flow_cfs, status, status_class,
                 status_color, status_24h, status_48h, status_72h,
                 updated (ISO), station_url, graph_url }
    """
    k = _gauge_key(south, west, north, east)
    now = time.time()
    if k in _GAUGE_CACHE and now - _GAUGE_CACHE[k]["ts"] < _GAUGE_TTL:
        return _GAUGE_CACHE[k]["data"]

    if len(_GAUGE_CACHE) >= _GAUGE_MAX:
        oldest = min(_GAUGE_CACHE, key=lambda x: _GAUGE_CACHE[x]["ts"])
        _GAUGE_CACHE.pop(oldest, None)

    params = {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "where": "1=1",
        "outFields": (
            "stationid,name,stage_ft,flow_cfs,status,statusClass,"
            "status_full,status_24h,status_48h,status_72h,"
            "lastupdate,stationurl,graphurl,LATITUDE,LONGITUDE"
        ),
        "returnGeometry": "true",
        "resultRecordCount": 200,
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_GAUGE_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS stream gauge fetch failed: %s", exc)
        _GAUGE_CACHE[k] = {"ts": now, "data": []}
        return []

    data: List[Dict[str, Any]] = []
    for feat in feats:
        a = feat.get("attributes", {})
        geom = feat.get("geometry") or {}
        lat = a.get("LATITUDE") or geom.get("y")
        lng = a.get("LONGITUDE") or geom.get("x")
        if lat is None or lng is None:
            continue
        sc = int(a.get("statusClass") or 0)
        status_lbl, color = _GAUGE_STATUS.get(sc, ("Unknown", "#9ca3af"))
        data.append(
            {
                "id": str(a.get("stationid") or ""),
                "name": str(a.get("name") or ""),
                "lat": float(lat),
                "lng": float(lng),
                "stage_ft": round(float(a["stage_ft"]), 2)
                if a.get("stage_ft") is not None
                else None,
                "flow_cfs": round(float(a["flow_cfs"]), 1)
                if a.get("flow_cfs") is not None
                else None,
                "status": str(a.get("status") or status_lbl),
                "status_class": sc,
                "status_color": color,
                "status_full": str(a.get("status_full") or ""),
                "status_24h": str(a.get("status_24h") or ""),
                "status_48h": str(a.get("status_48h") or ""),
                "status_72h": str(a.get("status_72h") or ""),
                "updated": _ms_to_iso(a["lastupdate"])
                if a.get("lastupdate") is not None
                else None,
                "station_url": str(a.get("stationurl") or ""),
                "graph_url": str(a.get("graphurl") or ""),
            }
        )

    _GAUGE_CACHE[k] = {"ts": now, "data": data}
    return data


# ── NOAA Storm Reports (past 24 h) ────────────────────────────────────────────

_STORM_RPT_CACHE: Dict[tuple, Dict[str, Any]] = {}
_STORM_RPT_TTL = 1800  # 30 minutes
_STORM_RPT_MAX = 32

_STORM_RPT_COLORS: Dict[str, str] = {
    "hail": "#facc15",  # yellow
    "tornado": "#ef4444",  # red
    "wind": "#60a5fa",  # blue
}


def _storm_rpt_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))


def fetch_storm_reports(
    south: float, west: float, north: float, east: float
) -> List[Dict]:
    """Return NOAA severe weather reports (past 24 h) intersecting the bounding box.

    Queries hail, tornado, and wind-damage layers and combines them.
    Each item: { type, lat, lng, time (ISO), location, state, comments,
                 magnitude, color }
    """
    k = _storm_rpt_key(south, west, north, east)
    now = time.time()
    if k in _STORM_RPT_CACHE and now - _STORM_RPT_CACHE[k]["ts"] < _STORM_RPT_TTL:
        return _STORM_RPT_CACHE[k]["data"]

    if len(_STORM_RPT_CACHE) >= _STORM_RPT_MAX:
        oldest = min(_STORM_RPT_CACHE, key=lambda x: _STORM_RPT_CACHE[x]["ts"])
        _STORM_RPT_CACHE.pop(oldest, None)

    bbox = f"{west},{south},{east},{north}"
    base = {
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "where": "1=1",
        "returnGeometry": "false",
        "resultRecordCount": 200,
        "outSR": "4326",
        "f": "json",
    }

    combined: List[Dict[str, Any]] = []

    # Hail layer
    try:
        p = dict(
            base,
            geometry=bbox,
            outFields="UTC_DATETIME,HAIL_SIZE,LOCATION,STATE,LATITUDE,LONGITUDE,COMMENTS",
        )
        r = _HTTP.get(_STORM_RPT_HAIL_URL, params=p, timeout=(3.05, 15))
        r.raise_for_status()
        for feat in r.json().get("features", []):
            a = feat.get("attributes", {})
            if a.get("LATITUDE") is None:
                continue
            combined.append(
                {
                    "type": "hail",
                    "lat": float(a["LATITUDE"]),
                    "lng": float(a["LONGITUDE"]),
                    "time": _ms_to_iso(a["UTC_DATETIME"])
                    if a.get("UTC_DATETIME") is not None
                    else None,
                    "location": str(a.get("LOCATION") or ""),
                    "state": str(a.get("STATE") or ""),
                    "comments": str(a.get("COMMENTS") or ""),
                    "magnitude": str(a.get("HAIL_SIZE") or ""),
                    "color": _STORM_RPT_COLORS["hail"],
                }
            )
    except Exception as exc:
        logger.warning("Storm report hail fetch failed: %s", exc)

    # Tornado layer
    try:
        p = dict(
            base,
            geometry=bbox,
            outFields="UTC_DATETIME,F_SCALE,LOCATION,STATE,LATITUDE,LONGITUDE,COMMENTS",
        )
        r = _HTTP.get(_STORM_RPT_TORN_URL, params=p, timeout=(3.05, 15))
        r.raise_for_status()
        for feat in r.json().get("features", []):
            a = feat.get("attributes", {})
            if a.get("LATITUDE") is None:
                continue
            ef = a.get("F_SCALE")
            combined.append(
                {
                    "type": "tornado",
                    "lat": float(a["LATITUDE"]),
                    "lng": float(a["LONGITUDE"]),
                    "time": _ms_to_iso(a["UTC_DATETIME"])
                    if a.get("UTC_DATETIME") is not None
                    else None,
                    "location": str(a.get("LOCATION") or ""),
                    "state": str(a.get("STATE") or ""),
                    "comments": str(a.get("COMMENTS") or ""),
                    "magnitude": ("EF" + str(ef)) if ef is not None else "",
                    "color": _STORM_RPT_COLORS["tornado"],
                }
            )
    except Exception as exc:
        logger.warning("Storm report tornado fetch failed: %s", exc)

    # Wind damage layer
    try:
        p = dict(
            base,
            geometry=bbox,
            outFields="UTC_DATETIME,LOCATION,STATE,LATITUDE,LONGITUDE,COMMENTS",
        )
        r = _HTTP.get(_STORM_RPT_WIND_URL, params=p, timeout=(3.05, 15))
        r.raise_for_status()
        for feat in r.json().get("features", []):
            a = feat.get("attributes", {})
            if a.get("LATITUDE") is None:
                continue
            combined.append(
                {
                    "type": "wind",
                    "lat": float(a["LATITUDE"]),
                    "lng": float(a["LONGITUDE"]),
                    "time": _ms_to_iso(a["UTC_DATETIME"])
                    if a.get("UTC_DATETIME") is not None
                    else None,
                    "location": str(a.get("LOCATION") or ""),
                    "state": str(a.get("STATE") or ""),
                    "comments": str(a.get("COMMENTS") or ""),
                    "magnitude": "",
                    "color": _STORM_RPT_COLORS["wind"],
                }
            )
    except Exception as exc:
        logger.warning("Storm report wind fetch failed: %s", exc)

    # Sort chronologically (most recent first)
    combined.sort(key=lambda x: x.get("time") or "", reverse=True)

    _STORM_RPT_CACHE[k] = {"ts": now, "data": combined}
    return combined


# ── AQI Stations (bbox map overlay) ───────────────────────────────────────────

_AQI_MAP_CACHE: Dict[tuple, Dict[str, Any]] = {}
_AQI_MAP_TTL = 1800  # 30 minutes
_AQI_MAP_MAX = 32


def _bbox_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))


def fetch_aqi_map(
    south: float, west: float, north: float, east: float
) -> List[Dict[str, Any]]:
    """Return PM2.5 AQI monitoring stations within the bounding box.

    Each dict:
        lat         float  station latitude
        lng         float  station longitude
        name        str    station / location name
        pm25        float  PM2.5 concentration in µg/m³
        category    str    "Good" | "Moderate" | "Unhealthy" …
        color       str    hex colour matching the AQI category
        updated     str    last-updated timestamp string
    """
    k = _bbox_key(south, west, north, east)
    now = time.time()
    if k in _AQI_MAP_CACHE and now - _AQI_MAP_CACHE[k]["ts"] < _AQI_MAP_TTL:
        return _AQI_MAP_CACHE[k]["data"]

    if len(_AQI_MAP_CACHE) >= _AQI_MAP_MAX:
        oldest = min(_AQI_MAP_CACHE, key=lambda x: _AQI_MAP_CACHE[x]["ts"])
        _AQI_MAP_CACHE.pop(oldest, None)

    params = {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "where": "value > 0",
        "outFields": "location,city,value,unit,lastUpdated",
        "returnGeometry": "true",
        "resultRecordCount": 300,
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_AQI_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS AQI map fetch failed: %s", exc)
        _AQI_MAP_CACHE[k] = {"ts": now, "data": []}
        return []

    data: List[Dict[str, Any]] = []
    for feat in feats:
        geom = feat.get("geometry") or {}
        lng_ = geom.get("x")
        lat_ = geom.get("y")
        if lat_ is None or lng_ is None:
            continue
        attrs = feat.get("attributes", {})
        raw = float(attrs.get("value") or 0)
        cat, color = _pm25_category(raw)
        data.append(
            {
                "lat": float(lat_),
                "lng": float(lng_),
                "name": str(attrs.get("location") or attrs.get("city") or ""),
                "pm25": round(raw, 1),
                "category": cat,
                "color": color,
                "updated": str(attrs.get("lastUpdated") or ""),
            }
        )

    _AQI_MAP_CACHE[k] = {"ts": now, "data": data}
    return data


# ── Drought Polygons (bbox map overlay) ───────────────────────────────────────

_DROUGHT_MAP_CACHE: Dict[tuple, Dict[str, Any]] = {}
_DROUGHT_MAP_TTL = 21600  # 6 hours — drought updates weekly
_DROUGHT_MAP_MAX = 32


def fetch_drought_map(
    south: float, west: float, north: float, east: float
) -> List[Dict[str, Any]]:
    """Return US Drought Monitor intensity polygons intersecting the bounding box.

    Each dict:
        dm      int    drought category 0–4
        code    str    "D0" … "D4"
        label   str    human-readable description
        color   str    hex fill colour
        rings   list   polygon rings as [[lat, lng], …]
    """
    k = _bbox_key(south, west, north, east)
    now = time.time()
    if k in _DROUGHT_MAP_CACHE and now - _DROUGHT_MAP_CACHE[k]["ts"] < _DROUGHT_MAP_TTL:
        return _DROUGHT_MAP_CACHE[k]["data"]

    if len(_DROUGHT_MAP_CACHE) >= _DROUGHT_MAP_MAX:
        oldest = min(_DROUGHT_MAP_CACHE, key=lambda x: _DROUGHT_MAP_CACHE[x]["ts"])
        _DROUGHT_MAP_CACHE.pop(oldest, None)

    params = {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "where": "dm >= 0",
        "outFields": "dm,d0,d1,d2,d3,d4,ddate",
        "returnGeometry": "true",
        "resultRecordCount": 200,
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_DROUGHT_URL, params=params, timeout=(3.05, 20))
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise ValueError(body["error"].get("message", "service error"))
        feats = body.get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS drought map fetch failed: %s", exc)
        _DROUGHT_MAP_CACHE[k] = {"ts": now, "data": []}
        return []

    data: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry") or {}
        rings = geom.get("rings") or []
        if not rings:
            continue
        dm = int(attrs.get("dm") or -1)
        if dm < 0:
            continue
        code, label = _DROUGHT_LABELS.get(dm, ("D?", "Unknown"))
        color = _DROUGHT_COLORS.get(dm, "#9E9E9E")
        # Thin very large rings for map performance
        thinned = [r[:: max(1, len(r) // 300)] for r in rings]
        data.append(
            {
                "dm": dm,
                "code": code,
                "label": label,
                "color": color,
                "rings": [_ring_to_latlng(r) for r in thinned],
            }
        )

    _DROUGHT_MAP_CACHE[k] = {"ts": now, "data": data}
    return data


# ── Precipitation Polygons (bbox map overlay) ─────────────────────────────────

_PRECIP_MAP_CACHE: Dict[tuple, Dict[str, Any]] = {}
_PRECIP_MAP_TTL = 3600  # 1 hour
_PRECIP_MAP_MAX = 32

_PRECIP_POLY_COLORS = [
    "#bfdbfe",
    "#93c5fd",
    "#60a5fa",
    "#3b82f6",
    "#2563eb",
    "#1d4ed8",
    "#1e40af",
    "#1e3a8a",
    "#172554",
]


def _precip_color(cat: int) -> str:
    idx = min(cat * 8 // 20, len(_PRECIP_POLY_COLORS) - 1) if cat > 0 else 0
    return _PRECIP_POLY_COLORS[max(0, idx)]


def fetch_precipitation_map(
    south: float, west: float, north: float, east: float
) -> List[Dict[str, Any]]:
    """Return NDFD precipitation forecast polygons intersecting the bounding box.

    Each dict:
        from_time   str   ISO-8601 period start
        to_time     str   ISO-8601 period end
        category    int   NDFD rainfall category (0–19)
        label       str   rainfall amount range (e.g. '0.25–0.50"')
        color       str   hex fill colour (lighter→darker with intensity)
        rings       list  polygon rings as [[lat, lng], …]
    """
    k = _bbox_key(south, west, north, east)
    now = time.time()
    if k in _PRECIP_MAP_CACHE and now - _PRECIP_MAP_CACHE[k]["ts"] < _PRECIP_MAP_TTL:
        return _PRECIP_MAP_CACHE[k]["data"]

    if len(_PRECIP_MAP_CACHE) >= _PRECIP_MAP_MAX:
        oldest = min(_PRECIP_MAP_CACHE, key=lambda x: _PRECIP_MAP_CACHE[x]["ts"])
        _PRECIP_MAP_CACHE.pop(oldest, None)

    params = {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "where": "category > 0",
        "outFields": "category,fromdate,todate,label",
        "returnGeometry": "true",
        "orderByFields": "fromdate ASC",
        "resultRecordCount": 200,
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_PRECIP_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS precip map fetch failed: %s", exc)
        _PRECIP_MAP_CACHE[k] = {"ts": now, "data": []}
        return []

    data: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry") or {}
        rings = geom.get("rings") or []
        if not rings:
            continue
        cat = int(attrs.get("category") or 0)
        if cat == 0:
            continue
        lbl = (attrs.get("label") or "").strip() or _PRECIP_CAT_LABEL.get(cat, "")
        data.append(
            {
                "from_time": _ms_to_iso(attrs.get("fromdate")),
                "to_time": _ms_to_iso(attrs.get("todate")),
                "category": cat,
                "label": lbl,
                "color": _precip_color(cat),
                "rings": [_ring_to_latlng(r) for r in rings],
            }
        )

    _PRECIP_MAP_CACHE[k] = {"ts": now, "data": data}
    return data


# ── NDBC Weather Buoys (bbox map overlay) ─────────────────────────────────────

_NDBC_CACHE: Dict[tuple, Dict[str, Any]] = {}
_NDBC_TTL = 1800  # 30 minutes — NDBC updates hourly
_NDBC_MAX = 32


def fetch_ndbc_buoys(
    south: float, west: float, north: float, east: float
) -> List[Dict[str, Any]]:
    """Return NDBC weather buoy observations within the bounding box.

    Each dict:
        lat           float   latitude
        lng           float   longitude
        id            str     NDBC station ID (e.g. "44025")
        name          str     station description
        water_temp_f  float|None  sea surface temperature °F
        wave_ht_ft    float|None  significant wave height in feet
        wind_kt       float|None  wind speed in knots
        wind_dir      int|None    wind direction in degrees
        period_s      float|None  dominant wave period in seconds
        pressure_mb   float|None  sea-level pressure in mb
        updated       str         ISO-8601 observation time
    """
    k = _bbox_key(south, west, north, east)
    now = time.time()
    if k in _NDBC_CACHE and now - _NDBC_CACHE[k]["ts"] < _NDBC_TTL:
        return _NDBC_CACHE[k]["data"]

    if len(_NDBC_CACHE) >= _NDBC_MAX:
        oldest = min(_NDBC_CACHE, key=lambda x: _NDBC_CACHE[x]["ts"])
        _NDBC_CACHE.pop(oldest, None)

    params = {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "where": "1=1",
        "outFields": (
            "STATION_ID,STATION_NAME,WATER_TEMP_F,WAVE_HT_FT,"
            "WIND_SPEED_KT,WIND_DIR,DOMINANT_PERIOD_S,PRESSURE_MB,"
            "LAT,LON,LAST_UPDATE"
        ),
        "returnGeometry": "false",
        "resultRecordCount": 200,
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_NDBC_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise ValueError(body["error"].get("message", "service error"))
        feats = body.get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS NDBC buoy fetch failed: %s", exc)
        _NDBC_CACHE[k] = {"ts": now, "data": []}
        return []

    def _f(v: Any) -> Optional[float]:
        try:
            return round(float(v), 1) if v is not None else None
        except (TypeError, ValueError):
            return None

    data: List[Dict[str, Any]] = []
    for feat in feats:
        a = feat.get("attributes", {})
        lat_ = a.get("LAT")
        lng_ = a.get("LON")
        if lat_ is None or lng_ is None:
            continue
        data.append(
            {
                "lat": float(lat_),
                "lng": float(lng_),
                "id": str(a.get("STATION_ID") or ""),
                "name": str(a.get("STATION_NAME") or ""),
                "water_temp_f": _f(a.get("WATER_TEMP_F")),
                "wave_ht_ft": _f(a.get("WAVE_HT_FT")),
                "wind_kt": _f(a.get("WIND_SPEED_KT")),
                "wind_dir": a.get("WIND_DIR"),
                "period_s": _f(a.get("DOMINANT_PERIOD_S")),
                "pressure_mb": _f(a.get("PRESSURE_MB")),
                "updated": _ms_to_iso(a["LAST_UPDATE"])
                if a.get("LAST_UPDATE") is not None
                else "",
            }
        )

    _NDBC_CACHE[k] = {"ts": now, "data": data}
    return data


# ── NDFD Daily Temperature polygons (bbox map overlay) ───────────────────────

_NDFD_TEMP_MAP_CACHE: Dict[tuple, Dict[str, Any]] = {}
_NDFD_TEMP_MAP_TTL = 3600  # 1 hour — NDFD updates infrequently
_NDFD_TEMP_MAP_MAX = 32


def _temp_color(temp_f: float, layer: str) -> str:
    """Temperature-to-colour for the map layer.

    Cool blues for cold, warm reds for hot.  The palette is intentionally
    muted so it doesn't dominate other layers.
    """
    if layer == "min":
        if temp_f < 0:
            return "#1e3a8a"  # deep blue  < 0°F
        if temp_f < 20:
            return "#1d4ed8"  # blue       0-20°F
        if temp_f < 32:
            return "#3b82f6"  # light blue 20-32°F
        if temp_f < 45:
            return "#06b6d4"  # cyan       32-45°F
        if temp_f < 60:
            return "#34d399"  # teal-green 45-60°F
        if temp_f < 75:
            return "#fbbf24"  # amber      60-75°F
        return "#f97316"  # orange     >75°F (warm night)
    else:  # max
        if temp_f < 32:
            return "#3b82f6"  # blue       <32°F (freeze)
        if temp_f < 50:
            return "#06b6d4"  # cyan       32-50°F
        if temp_f < 65:
            return "#34d399"  # green      50-65°F
        if temp_f < 80:
            return "#fbbf24"  # amber      65-80°F
        if temp_f < 95:
            return "#f97316"  # orange     80-95°F
        return "#ef4444"  # red        >95°F


def fetch_ndfd_temperature_map(
    south: float, west: float, north: float, east: float
) -> Dict[str, List[Dict[str, Any]]]:
    """Return NDFD daily high/low temperature polygons for the bounding box.

    Returns a dict with two keys:
        "min": list of min-temp polygons
        "max": list of max-temp polygons

    Each polygon dict:
        temp_f   float   temperature in °F
        period   str     ISO-8601 date (YYYY-MM-DD)
        color    str     hex fill colour
        rings    list    [[lat, lng], …]
    """
    k = _bbox_key(south, west, north, east)
    now = time.time()
    if (
        k in _NDFD_TEMP_MAP_CACHE
        and now - _NDFD_TEMP_MAP_CACHE[k]["ts"] < _NDFD_TEMP_MAP_TTL
    ):
        return _NDFD_TEMP_MAP_CACHE[k]["data"]

    if len(_NDFD_TEMP_MAP_CACHE) >= _NDFD_TEMP_MAP_MAX:
        oldest = min(_NDFD_TEMP_MAP_CACHE, key=lambda x: _NDFD_TEMP_MAP_CACHE[x]["ts"])
        _NDFD_TEMP_MAP_CACHE.pop(oldest, None)

    params_base = {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "where": "1=1",
        "outFields": "Temp,Period",
        "returnGeometry": "true",
        "resultRecordCount": 150,
        "outSR": "4326",
        "f": "json",
    }

    result: Dict[str, List[Dict[str, Any]]] = {"min": [], "max": []}

    for url, layer_key in [(_NDFD_TMIN_URL, "min"), (_NDFD_TMAX_URL, "max")]:
        try:
            resp = _HTTP.get(url, params=params_base, timeout=(3.05, 15))
            resp.raise_for_status()
            feats = resp.json().get("features", [])
        except Exception as exc:
            logger.warning("ArcGIS NDFD temp map fetch failed (%s): %s", url, exc)
            continue

        for feat in feats:
            attrs = feat.get("attributes", {})
            geom = feat.get("geometry") or {}
            raw = attrs.get("Temp")
            period = attrs.get("Period")
            if raw is None or period is None:
                continue
            try:
                temp_f = float(raw)
                period_str = datetime.fromtimestamp(
                    int(period) / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d")
            except Exception:
                continue

            rings_raw = geom.get("rings") or []
            rings = [_ring_to_latlng(r) for r in rings_raw if r]
            rings = [r for r in rings if len(r) >= 3]
            # Thin rings to ≤300 pts
            rings = [r[:: max(1, len(r) // 300)] for r in rings]
            if not rings:
                continue

            result[layer_key].append(
                {
                    "temp_f": round(temp_f),
                    "period": period_str,
                    "color": _temp_color(temp_f, layer_key),
                    "rings": rings,
                }
            )

    _NDFD_TEMP_MAP_CACHE[k] = {"ts": now, "data": result}
    return result


# ── NOAA HF Radar Surface Currents (bbox map overlay) ─────────────────────────

_HFRADAR_CACHE: Dict[tuple, Dict[str, Any]] = {}
_HFRADAR_TTL = 3600  # 1 hour — HF Radar updates hourly
_HFRADAR_MAX = 32


# Beaufort-scale-like colour ramp for current speed (cm/s)
def _current_color(speed_cms: float) -> str:
    if speed_cms < 10:
        return "#60a5fa"  # light blue  < 0.1 m/s
    if speed_cms < 25:
        return "#22c55e"  # green      0.1–0.25 m/s
    if speed_cms < 50:
        return "#eab308"  # yellow     0.25–0.5 m/s
    if speed_cms < 100:
        return "#f97316"  # orange     0.5–1 m/s
    return "#ef4444"  # red        > 1 m/s


def fetch_hfradar_currents(
    south: float, west: float, north: float, east: float
) -> List[Dict[str, Any]]:
    """Return NOAA HF Radar surface current vectors intersecting the bounding box.

    Queries East Coast, Gulf of Mexico, and West Coast regional services in
    parallel (thread-pool) and merges results into a single list.

    Each dict:
        lat         float   vector latitude
        lng         float   vector longitude
        speed_cms   float   current speed in cm/s
        speed_kts   float   current speed in knots
        dir_deg     int     current direction (degrees from, oceanographic convention)
        u           float   eastward component (cm/s)
        v           float   northward component (cm/s)
        color       str     hex colour for the speed level
        updated     str     ISO-8601 observation time
    """
    k = _bbox_key(south, west, north, east)
    now = time.time()
    if k in _HFRADAR_CACHE and now - _HFRADAR_CACHE[k]["ts"] < _HFRADAR_TTL:
        return _HFRADAR_CACHE[k]["data"]

    if len(_HFRADAR_CACHE) >= _HFRADAR_MAX:
        oldest = min(_HFRADAR_CACHE, key=lambda x: _HFRADAR_CACHE[x]["ts"])
        _HFRADAR_CACHE.pop(oldest, None)

    bbox = f"{west},{south},{east},{north}"
    base_params = {
        "geometry": bbox,
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "where": "1=1",
        "outFields": "u,v,speed,direction,lat,lon,datetime",
        "returnGeometry": "false",
        "resultRecordCount": 500,
        "outSR": "4326",
        "f": "json",
    }

    combined: List[Dict[str, Any]] = []

    for url in (_HFRADAR_EAST_URL, _HFRADAR_GULF_URL, _HFRADAR_WEST_URL):
        try:
            resp = _HTTP.get(url, params=base_params, timeout=(3.05, 12))
            resp.raise_for_status()
            body = resp.json()
            if body.get("error"):
                continue  # region not applicable; skip silently
            feats = body.get("features", [])
        except Exception as exc:
            logger.debug("HF Radar fetch skipped for %s: %s", url, exc)
            continue

        for feat in feats:
            a = feat.get("attributes", {})
            lat_ = a.get("lat")
            lng_ = a.get("lon")
            if lat_ is None or lng_ is None:
                continue
            spd = float(a.get("speed") or 0)
            dir_ = a.get("direction")
            combined.append(
                {
                    "lat": float(lat_),
                    "lng": float(lng_),
                    "speed_cms": round(spd, 1),
                    "speed_kts": round(spd * 0.0194384, 2),
                    "dir_deg": int(dir_) if dir_ is not None else None,
                    "u": round(float(a.get("u") or 0), 2),
                    "v": round(float(a.get("v") or 0), 2),
                    "color": _current_color(spd),
                    "updated": _ms_to_iso(a["datetime"])
                    if a.get("datetime") is not None
                    else "",
                }
            )

    _HFRADAR_CACHE[k] = {"ts": now, "data": combined}
    return combined


# ── NHC Tropical Weather Outlook (map overlay) ────────────────────────────────

_TROP_OUTLOOK_CACHE: Optional[List[Dict[str, Any]]] = None
_TROP_OUTLOOK_TS = 0.0
_TROP_OUTLOOK_TTL = 3600  # 1 hour — outlook updates every 6 hours

_TROP_PROB_COLORS: Dict[str, str] = {
    "high": "#ef4444",  # ≥60 % — red
    "medium": "#f97316",  # 40–59 % — orange
    "low": "#eab308",  # < 40 % — yellow
}


def fetch_tropical_outlook() -> List[Dict[str, Any]]:
    """Return NHC tropical weather outlook development-area polygons.

    Updated every 6 hours by the National Hurricane Center.  Returns an empty
    list outside of the Atlantic / Eastern Pacific tropical season or when no
    areas of interest exist.

    Each dict:
        probability   str    "low" | "medium" | "high"
        prob_label    str    human-readable probability string (e.g. "40%")
        color         str    hex fill colour
        basin         str    "ATL" | "EPAC" | "CPAC"
        rings         list   polygon rings [[lat, lng], …]
        discussion    str    brief text description
    """
    global _TROP_OUTLOOK_CACHE, _TROP_OUTLOOK_TS
    now = time.time()
    if _TROP_OUTLOOK_CACHE is not None and now - _TROP_OUTLOOK_TS < _TROP_OUTLOOK_TTL:
        return _TROP_OUTLOOK_CACHE

    params = {
        "where": "1=1",
        "outFields": "probability,basin,discussion,FormationChance2day,FormationChance5day",
        "returnGeometry": "true",
        "resultRecordCount": 50,
        "outSR": "4326",
        "f": "json",
    }

    try:
        resp = _HTTP.get(_TROPICAL_OUTLOOK_URL, params=params, timeout=(3.05, 15))
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise ValueError(body["error"].get("message", "service error"))
        feats = body.get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS tropical outlook fetch failed: %s", exc)
        _TROP_OUTLOOK_CACHE = []
        _TROP_OUTLOOK_TS = now
        return []

    results: List[Dict[str, Any]] = []
    for feat in feats:
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry") or {}
        rings = geom.get("rings") or []
        if not rings:
            continue

        raw_prob = str(
            attrs.get("probability") or attrs.get("FormationChance2day") or ""
        ).lower()
        # Normalise to low/medium/high
        if "high" in raw_prob:
            prob = "high"
        elif "medium" in raw_prob or "mod" in raw_prob:
            prob = "medium"
        else:
            prob = "low"

        prob_label = str(
            attrs.get("FormationChance2day") or attrs.get("probability") or ""
        ).strip()
        basin = str(attrs.get("basin") or "ATL").upper()
        discussion = str(attrs.get("discussion") or "").strip()[:300]

        results.append(
            {
                "probability": prob,
                "prob_label": prob_label or prob.capitalize(),
                "color": _TROP_PROB_COLORS.get(prob, "#eab308"),
                "basin": basin,
                "rings": [_ring_to_latlng(r) for r in rings],
                "discussion": discussion,
            }
        )

    _TROP_OUTLOOK_CACHE = results
    _TROP_OUTLOOK_TS = now
    return results


def cache_clear() -> None:
    """Clear all cached results.  Useful in tests."""
    global _STORM_CACHE, _STORM_TS, _RECENT_TRACK_CACHE, _RECENT_TRACK_TS
    global _SEA_ICE_CACHE, _SEA_ICE_TS, _TERM_CACHE, _TERM_TS
    _WARN_CACHE.clear()
    _STORM_CACHE = None
    _STORM_TS = 0.0
    _RECENT_TRACK_CACHE = None
    _RECENT_TRACK_TS = 0.0
    _AQI_CACHE.clear()
    _WIND_FC_CACHE.clear()
    _SST_CACHE.clear()
    _FIRE_CACHE.clear()
    _SMOKE_CACHE.clear()
    _PRECIP_CACHE.clear()
    _SEA_ICE_CACHE = None
    _SEA_ICE_TS = 0.0
    _TEMP_FC_CACHE.clear()
    _SEISMIC_CACHE.clear()
    _DROUGHT_CACHE.clear()
    _METAR_CACHE.clear()
    _TERM_CACHE = None
    _TERM_TS = 0.0
    _GAUGE_CACHE.clear()
    _STORM_RPT_CACHE.clear()
    _AQI_MAP_CACHE.clear()
    _DROUGHT_MAP_CACHE.clear()
    _PRECIP_MAP_CACHE.clear()
    _NDBC_CACHE.clear()
    _NDFD_TEMP_MAP_CACHE.clear()
    _HFRADAR_CACHE.clear()
    global _TROP_OUTLOOK_CACHE, _TROP_OUTLOOK_TS
    _TROP_OUTLOOK_CACHE = None
    _TROP_OUTLOOK_TS = 0.0
