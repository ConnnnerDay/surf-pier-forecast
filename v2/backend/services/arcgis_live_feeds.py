"""ArcGIS Living Atlas Live Feeds integration.

Data sources (all from services9.arcgis.com/RHVPKKiFTONKtxq3):
  Air_Quality_PM25_Latest_Results → layer 0 = OpenAQ PM2.5 monitoring stations
                                   Latest particulate-matter readings from global
                                   OpenAQ network; converted to AQI-like categories.
  NDFD_WindForecast_v1           → layer 6 = "Wind at City Level"
                                   NOAA National Digital Forecast Database wind
                                   speed/direction/gust at 3-hour intervals (~7 days).

Public API
----------
    fetch_air_quality(lat, lng)                      → dict | None
    fetch_wind_forecast(lat, lng)                    → list[dict]
"""

from __future__ import annotations

import logging
import math
import threading as _threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Shared session with connection pooling so that TCP+TLS handshakes are reused
# across the many layer fetches that hit the same services9.arcgis.com host.
# All requests.get() calls in this module use _HTTP instead of bare
# requests.get(), saving ~50-200 ms of handshake overhead per call.

_HTTP: requests.Session = requests.Session()
# Retry once on transient gateway errors (502/503/504); 429 is NOT retried to
# avoid amplifying rate-limit pressure.  backoff_factor=0.5 adds a 0.5 s pause
# before the single retry.
_ARCGIS_RETRY = Retry(total=1, backoff_factor=0.5, status_forcelist=[502, 503, 504], raise_on_status=False)
_HTTP.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=_ARCGIS_RETRY))
_HTTP.mount("http://", HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=_ARCGIS_RETRY))
_HTTP.headers.update({"User-Agent": "surf-pier-forecast/1.0 (+https://github.com/connnnerday/surf-pier-forecast)"})

_BASE = "https://services9.arcgis.com/RHVPKKiFTONKtxq3/arcgis/rest/services"

_CMS_TO_KT = 0.0194384  # centimetres/sec → knots (= 1.94384 / 100)

# Request timeouts: (connect_s, read_s)
# Connect timeout slightly above 3 s avoids blocking on slow DNS/TLS.
# Read timeouts are grouped by expected payload size.
_T_STD:    tuple[float, float] = (3.05, 15)  # standard polygons/points
_T_SHORT:  tuple[float, float] = (3.05, 12)  # small station readings
_T_LONG:   tuple[float, float] = (3.05, 18)  # smoke/large rasters
_T_XLONG:  tuple[float, float] = (3.05, 20)  # sea ice, seismic, drought, METAR

# NWS Watches/Warnings – layer 6 = "Events Ordered by Size and Severity"

# Active Hurricanes layers

# Recent Hurricanes – layer 1 = Observed Track (polylines)

# Air quality – layer 0 = OpenAQ PM2.5 monitoring stations
_AQI_URL = f"{_BASE}/Air_Quality_PM25_Latest_Results/FeatureServer/0/query"

# NDFD Wind Forecast – layer 6 = City Level (multipoint, 3-h intervals)
_NDFD_WIND_URL = f"{_BASE}/NDFD_WindForecast_v1/FeatureServer/6/query"

# Coral Reef / SST stations – layer 0 = station points with live SST

# Active wildfires – layer 0 = incident points
_FIRE_URL = f"{_BASE}/USA_Wildfires_v1/FeatureServer/0/query"

# Smoke forecast – layer 0 = hourly smoke-density polygons (CONUS, 48 h)

# NDFD Precipitation – layer 0 = amount polygons per 6-h interval
_PRECIP_URL = f"{_BASE}/NDFD_Precipitation_v1/FeatureServer/0/query"

# Arctic sea ice extent – monthly polygon boundary

# NDFD Daily Temperature – layer 0=Minimum, layer 1=Maximum (polygon, daily intervals)
_NDFD_TMIN_URL = f"{_BASE}/NDFD_DailyTemperature_v1/FeatureServer/0/query"
_NDFD_TMAX_URL = f"{_BASE}/NDFD_DailyTemperature_v1/FeatureServer/1/query"

# USGS Seismic Data – layer 0 = earthquake events (point, real-time)

# US Drought Intensity – layer 3 = current CONUS drought (DM 0-4 polygons)
_DROUGHT_URL = f"{_BASE}/US_Drought_Intensity_v1/FeatureServer/3/query"

# NOAA METAR surface observations – layer 0 = current station readings
_METAR_URL = f"{_BASE}/NOAA_METAR_current_wind_speed_direction_v1/FeatureServer/0/query"

# Day/Night Terminator – layer 2 = night shadow polygon (updates every ~5 min)

# Live Stream Gauges – layer 0 = current water level / flood stage at gauges
_GAUGE_URL = f"{_BASE}/Live_Stream_Gauges_v1/FeatureServer/0/query"

# NOAA Storm Reports – layers 0=Hail, 1=Tornado, 2=Wind (past 24 hours)

# NDBC Weather Buoys – current ocean/coastal observations
_NDBC_URL = f"{_BASE}/NDBC_Observations_v1/FeatureServer/0/query"

# NOAA HF Radar surface currents – hourly velocity vectors
# Three regional services cover East Coast, Gulf of Mexico, West Coast.
# Layer 0 = hourly current vectors (speed cm/s, direction °, u/v components)

# NHC Tropical Weather Outlook – development-area polygons (layer 0)
_TROPICAL_OUTLOOK_URL = f"{_BASE}/NHC_Tropical_Weather_Outlook_v1/FeatureServer/0/query"

# Keywords that make a warning relevant to coastal/marine fishing

# ── Caches ─────────────────────────────────────────────────────────────────────


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


def _evict_oldest(cache: dict, max_size: int) -> None:
    """Drop the oldest entry when the cache is at capacity."""
    if len(cache) >= max_size:
        oldest = min(cache, key=lambda k: cache[k]["ts"])
        cache.pop(oldest, None)


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

_AQI_CACHE: dict[tuple, dict[str, Any]] = {}
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

def fetch_air_quality(lat: float, lng: float) -> Optional[dict[str, Any]]:
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

    _evict_oldest(_AQI_CACHE, _AQI_CACHE_MAX)

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
            resp = _HTTP.get(_AQI_URL, params=params, timeout=_T_SHORT)
            resp.raise_for_status()
            feats = resp.json().get("features", [])
        except Exception as exc:
            logger.warning("ArcGIS AQI fetch failed (pad=%.1f): %s", pad, exc)
            break

        if not feats:
            continue  # try wider search

        # Find the closest station by Euclidean distance (good enough at this scale)
        best: Optional[dict[str, Any]] = None
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

        result: dict[str, Any] = {
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

_WIND_FC_CACHE: dict[tuple, dict[str, Any]] = {}
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

def fetch_wind_forecast(lat: float, lng: float) -> list[dict[str, Any]]:
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

    _evict_oldest(_WIND_FC_CACHE, _WIND_FC_MAX)

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
        resp = _HTTP.get(_NDFD_WIND_URL, params=params, timeout=_T_STD)
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS NDFD wind forecast fetch failed: %s", exc)
        _WIND_FC_CACHE[key] = {"ts": time.time(), "data": []}
        return []

    # Group by IntervalStart: average across all city points returned in the bbox

    buckets: dict[int, list] = defaultdict(list)
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

    results: list[dict[str, Any]] = []
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


# ── Active Wildfires ───────────────────────────────────────────────────────────

_FIRE_CACHE: dict[tuple, dict[str, Any]] = {}
_FIRE_CACHE_TTL = 900  # 15 minutes
_FIRE_CACHE_MAX = 32

def _fire_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))

def fetch_wildfire_incidents(
    south: float, west: float, north: float, east: float
) -> list[dict[str, Any]]:
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

    _evict_oldest(_FIRE_CACHE, _FIRE_CACHE_MAX)

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
        resp = _HTTP.get(_FIRE_URL, params=params, timeout=_T_STD)
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS wildfire fetch failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
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


# ── Precipitation Forecast ─────────────────────────────────────────────────────

_PRECIP_CACHE: dict[tuple, dict[str, Any]] = {}
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

def fetch_precip_forecast(lat: float, lng: float) -> list[dict[str, Any]]:
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

    _evict_oldest(_PRECIP_CACHE, _PRECIP_CACHE_MAX)

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
        resp = _HTTP.get(_PRECIP_URL, params=params, timeout=_T_STD)
        resp.raise_for_status()
        feats = resp.json().get("features", [])
    except Exception as exc:
        logger.warning("ArcGIS precip forecast fetch failed: %s", exc)
        _PRECIP_CACHE[key] = {"ts": time.time(), "data": []}
        return []

    # Deduplicate by fromdate (multiple polygons may cover the area; take first hit)
    seen: set[Any] = set()
    results: list[dict[str, Any]] = []
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


# ── NDFD Daily Temperature ─────────────────────────────────────────────────────

_TEMP_FC_CACHE: dict[tuple, dict[str, Any]] = {}
_TEMP_FC_TTL = 3600  # 1 hour
_TEMP_FC_MAX = 32

def _temp_key(lat: float, lng: float) -> tuple:
    return (round(lat, 2), round(lng, 2))

def fetch_temp_forecast(lat: float, lng: float) -> list[Dict]:
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

    results: dict[str, dict[str, Any]] = {}

    for url, field in [(_NDFD_TMIN_URL, "min_f"), (_NDFD_TMAX_URL, "max_f")]:
        try:
            resp = _HTTP.get(url, params=base, timeout=_T_XLONG)
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

    _evict_oldest(_TEMP_FC_CACHE, _TEMP_FC_MAX)
    _TEMP_FC_CACHE[k] = {"ts": now, "data": data}
    return data


# ── US Drought Intensity ───────────────────────────────────────────────────────

_DROUGHT_CACHE: dict[tuple, dict[str, Any]] = {}
_DROUGHT_TTL = 21600  # 6 hours — drought data updates weekly
_DROUGHT_MAX = 64

_DROUGHT_LABELS: dict[int, tuple] = {
    0: ("D0", "Abnormally Dry"),
    1: ("D1", "Moderate Drought"),
    2: ("D2", "Severe Drought"),
    3: ("D3", "Extreme Drought"),
    4: ("D4", "Exceptional Drought"),
}
_DROUGHT_COLORS: dict[int, str] = {
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
    Uses the NDMC REST API directly (droughtmonitor.unl.edu) to avoid ESRI auth.
    """
    k = _drought_key(lat, lng)
    now = time.time()
    if k in _DROUGHT_CACHE and now - _DROUGHT_CACHE[k]["ts"] < _DROUGHT_TTL:
        return _DROUGHT_CACHE[k]["data"]

    _evict_oldest(_DROUGHT_CACHE, _DROUGHT_MAX)

    try:
        resp = _HTTP.get(
            "https://droughtmonitor.unl.edu/api/webservice/current/GetDMDataForPoint.ashx",
            params={"longitude": round(lng, 4), "latitude": round(lat, 4), "statistic": 0},
            timeout=_T_XLONG,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        logger.warning("NDMC drought fetch failed: %s", exc)
        _DROUGHT_CACHE[k] = {"ts": now, "data": None}
        return None

    if not raw:
        _DROUGHT_CACHE[k] = {"ts": now, "data": None}
        return None

    entry = raw[0] if isinstance(raw, list) else raw

    # D0–D4 are cumulative area percentages (D4 ⊆ D3 ⊆ D2 ⊆ D1 ⊆ D0).
    # Use highest non-zero category as the drought level at this point.
    d4 = float(entry.get("D4") or 0)
    d3 = float(entry.get("D3") or 0)
    d2 = float(entry.get("D2") or 0)
    d1 = float(entry.get("D1") or 0)
    d0 = float(entry.get("D0") or 0)

    if d4 > 0:
        dm = 4
    elif d3 > 0:
        dm = 3
    elif d2 > 0:
        dm = 2
    elif d1 > 0:
        dm = 1
    elif d0 > 0:
        dm = 0
    else:
        dm = -1

    map_date = str(entry.get("MapDate", ""))
    date_str = (
        f"{map_date[:4]}-{map_date[4:6]}-{map_date[6:]}"
        if len(map_date) == 8
        else None
    )

    code, label = _DROUGHT_LABELS.get(dm, ("None", "No Drought"))
    color = _DROUGHT_COLORS.get(dm, "#FFFFFF")

    result: Optional[dict[str, Any]] = {
        "dm": dm,
        "code": code,
        "label": label,
        "color": color,
        "date": date_str,
        "d0": round(d0, 1),
        "d1": round(d1, 1),
        "d2": round(d2, 1),
        "d3": round(d3, 1),
        "d4": round(d4, 1),
    }

    _DROUGHT_CACHE[k] = {"ts": now, "data": result}
    return result

# ── NOAA METAR Surface Observations ───────────────────────────────────────────

_METAR_CACHE: dict[tuple, dict[str, Any]] = {}
_METAR_TTL = 1800  # 30 minutes — METAR updates hourly
_METAR_MAX = 32

_FLT_CAT_COLORS: dict[str, str] = {
    "VFR": "#22c55e",  # green  — clear flying conditions
    "MVFR": "#60a5fa",  # blue   — marginal VFR
    "IFR": "#f87171",  # red    — instrument conditions / low visibility
    "LIFR": "#c084fc",  # purple — low instrument conditions / fog
}

def _metar_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))

def fetch_metar_stations(
    south: float, west: float, north: float, east: float
) -> list[Dict]:
    """Return current NOAA METAR surface observations intersecting the bounding box.

    Each item: { icao, name, lat, lng, observed (ISO), temp_f, dew_f, humidity,
                 wind_deg, wind_kt, gust_kt, wind_dir, visibility_m, pressure_mb,
                 sky, weather, heat_index_f, wind_chill_f, flight_cat, cat_color }
    Uses NOAA Aviation Weather Center JSON API (aviationweather.gov).
    Wind speed is already in knots from AWC; visibility in statute miles → metres.
    """
    k = _metar_key(south, west, north, east)
    now = time.time()
    if k in _METAR_CACHE and now - _METAR_CACHE[k]["ts"] < _METAR_TTL:
        return _METAR_CACHE[k]["data"]

    _evict_oldest(_METAR_CACHE, _METAR_MAX)

    try:
        resp = _HTTP.get(
            "https://aviationweather.gov/api/data/metar",
            params={
                "bbox": f"{south},{west},{north},{east}",
                "format": "json",
                "hours": 1,
            },
            timeout=_T_XLONG,
        )
        resp.raise_for_status()
        raw = resp.json()
        if not isinstance(raw, list):
            raw = raw.get("data", [])
    except Exception as exc:
        logger.warning("AWC METAR fetch failed: %s", exc)
        _METAR_CACHE[k] = {"ts": now, "data": []}
        return []

    data: list[dict[str, Any]] = []
    for st in raw:
        lat_ = st.get("lat")
        lng_ = st.get("lon")
        if lat_ is None or lng_ is None:
            continue

        temp_c = st.get("temp")
        dewp_c = st.get("dewp")
        temp_f = round(float(temp_c) * 9 / 5 + 32, 1) if temp_c is not None else None
        dew_f = round(float(dewp_c) * 9 / 5 + 32, 1) if dewp_c is not None else None

        vis_mi = st.get("visib")
        try:
            vis_m = round(float(vis_mi) * 1609.34) if vis_mi is not None else None
        except (TypeError, ValueError):
            vis_m = None

        wspd = st.get("wspd")  # knots
        wgst = st.get("wgst")  # knots
        wdir = st.get("wdir")

        slp = st.get("slp")
        altim = st.get("altim")  # hPa
        pressure_mb = (
            round(float(slp), 1)
            if slp is not None
            else (round(float(altim), 1) if altim is not None else None)
        )

        sky_cond = st.get("sky_condition") or []
        sky_str = sky_cond[0].get("cover", "") if sky_cond else str(st.get("sky") or "")
        cat = str(st.get("fltcat") or "").strip().upper()

        data.append(
            {
                "icao": str(st.get("icaoId") or ""),
                "name": str(st.get("name") or ""),
                "lat": float(lat_),
                "lng": float(lng_),
                "observed": str(st.get("obsTime") or ""),
                "temp_f": temp_f,
                "dew_f": dew_f,
                "humidity": st.get("relh"),
                "wind_deg": int(wdir) if wdir is not None else None,
                "wind_dir": _deg_to_compass(wdir) if wdir is not None else None,
                "wind_kt": float(wspd) if wspd is not None else None,
                "gust_kt": float(wgst) if wgst is not None else None,
                "wind_chill_f": None,
                "heat_index_f": None,
                "visibility_m": vis_m,
                "pressure_mb": pressure_mb,
                "sky": sky_str,
                "weather": str(st.get("wx_string") or ""),
                "flight_cat": cat or None,
                "cat_color": _FLT_CAT_COLORS.get(cat, "#9ca3af"),
            }
        )

    _METAR_CACHE[k] = {"ts": now, "data": data}
    return data


# ── Live Stream Gauges ─────────────────────────────────────────────────────────

_GAUGE_CACHE: dict[tuple, dict[str, Any]] = {}
_GAUGE_TTL = 900  # 15 minutes
_GAUGE_MAX = 32

# Map statusClass integer to a human label and colour
_GAUGE_STATUS: dict[int, tuple] = {
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
) -> list[Dict]:
    """Return live USGS stream gauge readings intersecting the bounding box.

    Each item: { id, name, lat, lng, stage_ft, flow_cfs, status, status_class,
                 status_color, status_24h, status_48h, status_72h,
                 updated (ISO), station_url, graph_url }
    Uses USGS Water Services IV API directly (waterservices.usgs.gov).
    """
    k = _gauge_key(south, west, north, east)
    now = time.time()
    if k in _GAUGE_CACHE and now - _GAUGE_CACHE[k]["ts"] < _GAUGE_TTL:
        return _GAUGE_CACHE[k]["data"]

    _evict_oldest(_GAUGE_CACHE, _GAUGE_MAX)

    try:
        resp = _HTTP.get(
            "https://waterservices.usgs.gov/nwis/iv/",
            params={
                "format": "json",
                "bBox": f"{west},{south},{east},{north}",
                "parameterCd": "00065,00060",  # gage height (ft), discharge (cfs)
                "siteStatus": "active",
                "siteType": "ST",
            },
            timeout=_T_XLONG,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        logger.warning("USGS stream gauge fetch failed: %s", exc)
        _GAUGE_CACHE[k] = {"ts": now, "data": []}
        return []

    time_series = body.get("value", {}).get("timeSeries", [])
    sites: dict[str, dict[str, Any]] = {}
    for ts in time_series:
        src = ts.get("sourceInfo", {})
        codes = src.get("siteCode") or []
        site_no = codes[0].get("value", "") if codes else ""
        if not site_no:
            continue

        geo = src.get("geoLocation", {}).get("geogLocation", {})
        lat_ = geo.get("latitude")
        lng_ = geo.get("longitude")
        if lat_ is None or lng_ is None:
            continue

        var_code = ((ts.get("variable", {}).get("variableCode")) or [{}])[0].get("value", "")
        values = ((ts.get("values") or [{}])[0]).get("value", [])
        latest = values[-1] if values else {}
        val_str = latest.get("value")
        val_dt = latest.get("dateTime")

        try:
            val = float(val_str) if val_str not in (None, "", "-999999") else None
        except (TypeError, ValueError):
            val = None

        if site_no not in sites:
            sites[site_no] = {
                "id": site_no,
                "name": str(src.get("siteName", "")),
                "lat": float(lat_),
                "lng": float(lng_),
                "stage_ft": None,
                "flow_cfs": None,
                "status": "Normal",
                "status_class": 0,
                "status_color": _GAUGE_STATUS[0][1],
                "status_full": "",
                "status_24h": "",
                "status_48h": "",
                "status_72h": "",
                "updated": val_dt,
                "station_url": f"https://waterdata.usgs.gov/nwis/uv?site_no={site_no}",
                "graph_url": f"https://waterdata.usgs.gov/nwis/uv?site_no={site_no}",
            }

        if var_code == "00065" and val is not None:
            sites[site_no]["stage_ft"] = round(val, 2)
        elif var_code == "00060" and val is not None:
            sites[site_no]["flow_cfs"] = round(val, 1)

    data = list(sites.values())
    _GAUGE_CACHE[k] = {"ts": now, "data": data}
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

def get_nearest_river_discharge(
    lat: float, lng: float, radius_deg: float = 0.35
) -> dict[str, Any]:
    """Return the nearest USGS streamgauge readings to a coordinate.

    Wraps :func:`fetch_stream_gauges` (which takes a bounding box) with
    distance sorting so a single pier/inlet coordinate can surface nearby
    river discharge and stage.  Freshwater outflow after rain changes
    salinity and turbidity near inlets and estuary piers, which affects
    where and how fish feed.  Widens the search once if nothing is found
    nearby, since rivers are sparse along open-coast piers.

    Returns {available, gauges (nearest 5, each with distance_mi), nearest,
    source, source_url}.
    """
    def _search(radius: float) -> list[dict[str, Any]]:
        raw = fetch_stream_gauges(lat - radius, lng - radius, lat + radius, lng + radius)
        gauges = []
        for g in raw:
            if g.get("flow_cfs") is None and g.get("stage_ft") is None:
                continue
            # Copy rather than mutate — fetch_stream_gauges returns cached
            # dicts shared across every caller in the same bbox bucket.
            entry = dict(g)
            entry["distance_mi"] = round(
                _haversine_miles(lat, lng, g["lat"], g["lng"]), 1
            )
            gauges.append(entry)
        gauges.sort(key=lambda g: g["distance_mi"])
        return gauges

    gauges = _search(radius_deg)
    if not gauges:
        gauges = _search(radius_deg * 2)

    nearest = gauges[0] if gauges else None
    return {
        "available": nearest is not None,
        "gauges": gauges[:5],
        "nearest": nearest,
        "source": "USGS NWIS streamgauges",
        "source_url": "https://waterdata.usgs.gov/nwis/rt",
    }


# ── Shared bbox cache key (used by remaining map-overlay fetchers) ───────────


def _bbox_key(s: float, w: float, n: float, e: float) -> tuple:
    return (round(s, 1), round(w, 1), round(n, 1), round(e, 1))


# ── NDBC Weather Buoys (bbox map overlay) ─────────────────────────────────────

_NDBC_CACHE: dict[tuple, dict[str, Any]] = {}
_NDBC_TTL = 1800  # 30 minutes — NDBC updates hourly
_NDBC_MAX = 32

def fetch_ndbc_buoys(
    south: float, west: float, north: float, east: float
) -> list[dict[str, Any]]:
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
    Uses NOAA CoastWatch ERDDAP (cwwcNDBCMet dataset) instead of ArcGIS.
    ERDDAP returns m/s for wind, m for wave height, °C for temperature —
    all converted to imperial/knots here.
    """
    from datetime import timedelta

    k = _bbox_key(south, west, north, east)
    now = time.time()
    if k in _NDBC_CACHE and now - _NDBC_CACHE[k]["ts"] < _NDBC_TTL:
        return _NDBC_CACHE[k]["data"]

    _evict_oldest(_NDBC_CACHE, _NDBC_MAX)

    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(hours=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    query = (
        "station,latitude,longitude,time,wd,wspd,gst,wvht,dpd,wtmp,atmp,bar"
        f"&latitude>={south}&latitude<={north}"
        f"&longitude>={west}&longitude<={east}"
        f"&time>={cutoff}"
        '&orderByMax("station,time")'
    )

    try:
        resp = _HTTP.get(
            f"https://coastwatch.pfeg.noaa.gov/erddap/tabledap/cwwcNDBCMet.json?{query}",
            timeout=_T_XLONG,
        )
        if resp.status_code == 404:
            # ERDDAP returns 404 when no data matches the constraints
            _NDBC_CACHE[k] = {"ts": now, "data": []}
            return []
        resp.raise_for_status()
        table = resp.json().get("table", {})
        col_names: list[str] = table.get("columnNames", [])
        rows: list[list] = table.get("rows", [])
    except Exception as exc:
        logger.warning("ERDDAP NDBC fetch failed: %s", exc)
        _NDBC_CACHE[k] = {"ts": now, "data": []}
        return []

    def _col(row: list, name: str) -> Any:
        try:
            return row[col_names.index(name)]
        except (ValueError, IndexError):
            return None

    def _f(v: Any) -> Optional[float]:
        try:
            return round(float(v), 1) if v is not None else None
        except (TypeError, ValueError):
            return None

    data: list[dict[str, Any]] = []
    for row in rows:
        lat_ = _col(row, "latitude")
        lng_ = _col(row, "longitude")
        if lat_ is None or lng_ is None:
            continue

        # ERDDAP units: wind/gust m/s → knots; wave height m → ft; temp °C → °F
        wspd_ms = _col(row, "wspd")
        gst_ms = _col(row, "gst")
        wvht_m = _col(row, "wvht")
        wtmp_c = _col(row, "wtmp")

        wind_kt = _f(float(wspd_ms) * 1.94384) if wspd_ms is not None else None
        gust_kt = _f(float(gst_ms) * 1.94384) if gst_ms is not None else None
        wave_ft = _f(float(wvht_m) * 3.28084) if wvht_m is not None else None
        water_f = _f(float(wtmp_c) * 9 / 5 + 32) if wtmp_c is not None else None

        station = str(_col(row, "station") or "")
        data.append(
            {
                "lat": float(lat_),
                "lng": float(lng_),
                "id": station,
                "name": station,
                "water_temp_f": water_f,
                "wave_ht_ft": wave_ft,
                "wind_kt": wind_kt,
                "wind_dir": _col(row, "wd"),
                "period_s": _f(_col(row, "dpd")),
                "pressure_mb": _f(_col(row, "bar")),
                "updated": str(_col(row, "time") or ""),
            }
        )

    _NDBC_CACHE[k] = {"ts": now, "data": data}
    return data


# ── NHC Tropical Weather Outlook (map overlay) ────────────────────────────────

_TROP_OUTLOOK_CACHE: Optional[list[dict[str, Any]]] = None
_TROP_OUTLOOK_TS = 0.0
_TROP_OUTLOOK_TTL = 3600  # 1 hour — outlook updates every 6 hours
_TROP_OUTLOOK_LOCK = _threading.Lock()

_TROP_PROB_COLORS: dict[str, str] = {
    "high": "#ef4444",  # ≥60 % — red
    "medium": "#f97316",  # 40–59 % — orange
    "low": "#eab308",  # < 40 % — yellow
}

def fetch_tropical_outlook() -> list[dict[str, Any]]:
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
    # Fast path — no lock needed
    if _TROP_OUTLOOK_CACHE is not None and now - _TROP_OUTLOOK_TS < _TROP_OUTLOOK_TTL:
        return _TROP_OUTLOOK_CACHE

    with _TROP_OUTLOOK_LOCK:
        now = time.time()
        if _TROP_OUTLOOK_CACHE is not None and now - _TROP_OUTLOOK_TS < _TROP_OUTLOOK_TTL:
            return _TROP_OUTLOOK_CACHE

        params = {
            "where": "1=1",
            "outFields": "probability,basin,discussion,FormationChance2day,FormationChance5day,FormationChance7day",
            "returnGeometry": "true",
            "resultRecordCount": 50,
            "outSR": "4326",
            "f": "json",
        }

        try:
            resp = _HTTP.get(_TROPICAL_OUTLOOK_URL, params=params, timeout=_T_STD)
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

        results: list[dict[str, Any]] = []
        for feat in feats:
            attrs = feat.get("attributes", {})
            geom = feat.get("geometry") or {}
            rings = geom.get("rings") or []
            if not rings:
                continue

            # NHC changed from 2-day/5-day to 7-day outlook in 2024; try all variants.
            raw_prob = str(
                attrs.get("probability")
                or attrs.get("FormationChance7day")
                or attrs.get("FormationChance2day")
                or ""
            ).lower()
            # Normalise to low/medium/high
            if "high" in raw_prob:
                prob = "high"
            elif "medium" in raw_prob or "mod" in raw_prob:
                prob = "medium"
            else:
                prob = "low"

            prob_label = str(
                attrs.get("FormationChance7day")
                or attrs.get("FormationChance2day")
                or attrs.get("probability")
                or ""
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
    _AQI_CACHE.clear()
    _WIND_FC_CACHE.clear()
    _FIRE_CACHE.clear()
    _PRECIP_CACHE.clear()
    _TEMP_FC_CACHE.clear()
    _DROUGHT_CACHE.clear()
    _METAR_CACHE.clear()
    _GAUGE_CACHE.clear()
    _NDBC_CACHE.clear()
    global _TROP_OUTLOOK_CACHE, _TROP_OUTLOOK_TS
    _TROP_OUTLOOK_CACHE = None
    _TROP_OUTLOOK_TS = 0.0
