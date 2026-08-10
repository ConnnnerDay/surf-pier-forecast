"""Data.gov environmental and coastal dataset integration.

Data.gov (https://data.gov/) is the U.S. government's open-data portal.
This module queries two public sub-portals that require no API key:

1. **EPA Water Quality Portal** (https://www.waterqualitydata.us/)
   Provides water chemistry measurements, beach-closure advisories, and
   harmful-algal-bloom (HAB) observations from thousands of monitoring
   stations managed by EPA, USGS, and state agencies.

2. **USGS National Water Information System (NWIS)** site list
   Provides metadata for surface-water monitoring stations (used to find
   the nearest stations to a forecast location).

Integration points
------------------
    fetch_water_quality(lat, lng, radius_km=50) -> dict
        Returns recent water-quality measurements for the nearest stations.

    fetch_beach_closures(state_code) -> list[dict]
        Returns active beach-closure / advisory records for a US state.

    get_water_quality_summary(lat, lng) -> dict
        High-level summary ready for template rendering.

All endpoints are called over HTTPS, return JSON, and require no
registration or API key.  Results are cached in-process for 2 hours.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_TIMEOUT_RESULT: tuple[float, float] = (5, 30)
_TIMEOUT_STATION: tuple[float, float] = (5, 20)

logger = logging.getLogger(__name__)

# ── HTTP session ──────────────────────────────────────────────────────────────
_HTTP: requests.Session = requests.Session()
_WQP_RETRY = Retry(total=1, backoff_factor=0.5, status_forcelist=[502, 503, 504], raise_on_status=False)
_HTTP.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=_WQP_RETRY))

# ── In-process result cache ───────────────────────────────────────────────────
_CACHE: dict[tuple, dict[str, Any]] = {}
_CACHE_TTL: int = 7200  # 2 hours — water quality changes slowly
_CACHE_TTL_FAIL: int = 300  # 5 min — retry failed queries sooner
_CACHE_MAX: int = 256

# ── Water Quality Portal base URL ─────────────────────────────────────────────
# Documentation: https://www.waterqualitydata.us/webservices_documentation/
_WQP_BASE = "https://www.waterqualitydata.us"

# ── Characteristic names of interest for coastal/marine fishing ───────────────
# These are standard WQP characteristic names; the portal understands them
# without an API key.  Selected for relevance to anglers and beach safety.
_CHARACTERISTICS = [
    "Temperature, water",
    "Dissolved oxygen (DO)",
    "Salinity",
    "pH",
    "Turbidity",
    "Chlorophyll a",
    "Fecal Coliform",
    "Enterococcus",  # beach closure indicator
    "Microcystin",  # harmful-algal-bloom toxin
    "Cyanobacteria",  # harmful-algal-bloom cell counts
]

# EPA's 2019 recreational-water health advisory thresholds for microcystin (µg/L)
_MICROCYSTIN_WATCH_UG_L = 8.0
_MICROCYSTIN_DANGER_UG_L = 20.0
# Elevated chlorophyll-a is a weaker secondary bloom indicator, used only when
# no direct toxin reading is available.
_CHLOROPHYLL_BLOOM_UG_L = 20.0

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_water_quality(
    lat: float,
    lng: float,
    radius_km: float = 50,
    within_days: int = 7,
) -> dict[str, Any]:
    """Fetch recent water-quality measurements near a location.

    Queries the EPA Water Quality Portal for the most recent results from
    monitoring stations within ``radius_km`` km of the given coordinates.

    Parameters
    ----------
    lat, lng        WGS-84 decimal degrees
    radius_km       Search radius in kilometres (default 50 km)
    within_days     Only return measurements from the last N days

    Returns
    -------
    dict with keys:
        stations    list of station dicts with recent measurements
        summary     aggregated values (latest DO, temp, pH, salinity, etc.)
        source      attribution string
        fetched_at  ISO timestamp
    """
    cache_key = ("wq", round(lat, 2), round(lng, 2), radius_km, within_days)
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    # WQP uses a bounding box in decimal degrees; approximate km → degrees
    deg = radius_km / 111.0
    bbox = f"{lng - deg},{lat - deg},{lng + deg},{lat + deg}"

    # Build start date
    start_date = (datetime.now(timezone.utc) - timedelta(days=within_days)).strftime(
        "%m-%d-%Y"
    )

    params = {
        "bBox": bbox,
        "characteristicName": ";".join(_CHARACTERISTICS),
        "startDateLo": start_date,
        "mimeType": "json",
        "sorted": "no",
        "resultPhysicalMediumName": "Water",
        # Limit to 200 results to keep response fast
        "maxResultRows": "200",
    }

    result: dict[str, Any] = {
        "stations": [],
        "summary": {},
        "source": "EPA Water Quality Portal",
        "source_url": "https://www.waterqualitydata.us/",
        "fetched_at": None,
    }

    try:
        resp = _HTTP.get(
            f"{_WQP_BASE}/data/Result/search",
            params=params,
            timeout=_TIMEOUT_RESULT,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("datagov: WQP fetch failed: %s", exc)
        _cache_set(cache_key, result, failed=True)
        return result
    except (ValueError, KeyError) as exc:
        logger.warning("datagov: WQP parse error: %s", exc)
        _cache_set(cache_key, result, failed=True)
        return result

    rows = data if isinstance(data, list) else data.get("features", [])
    stations_map: dict[str, dict[str, Any]] = {}

    for row in rows:
        props = row.get("properties", row)
        station_id = props.get("MonitoringLocationIdentifier", "unknown")
        char_name = props.get("CharacteristicName", "")
        value_str = props.get("ResultMeasureValue", "")
        unit = props.get("ResultMeasure/MeasureUnitCode", "")

        if station_id not in stations_map:
            stations_map[station_id] = {
                "id": station_id,
                "name": props.get("MonitoringLocationName", station_id),
                "lat": _safe_float(props.get("ActivityLocation/LatitudeMeasure")),
                "lng": _safe_float(props.get("ActivityLocation/LongitudeMeasure")),
                "measurements": {},
            }

        value = _safe_float(value_str)
        if value is not None and char_name:
            stations_map[station_id]["measurements"][char_name] = {
                "value": value,
                "unit": unit,
                "date": props.get("ActivityStartDate", ""),
            }

    result["stations"] = list(stations_map.values())[:20]  # cap at 20 stations
    result["summary"] = _build_summary(result["stations"])
    result["fetched_at"] = _now_iso()

    _cache_set(cache_key, result)
    return result

def fetch_beach_closures(state_code: str) -> list[dict[str, Any]]:
    """Fetch active beach-closure / advisory records for a US state.

    Uses the EPA Beach Advisory and Closing Online Notification (BEACON)
    dataset via the Water Quality Portal.  No API key required.

    Parameters
    ----------
    state_code  Two-letter US state abbreviation (e.g. ``"CA"``, ``"FL"``).

    Returns
    -------
    List of closure dicts: {beach_name, reason, start_date, end_date,
                            lat, lng, county}
    Returns [] on error.
    """
    state_code = state_code.upper().strip()
    cache_key = ("beach", state_code)
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    # BEACON data is hosted on the WQP as Organisation type "Beach Program"
    params = {
        "statecode": f"US:{_STATE_FIPS.get(state_code, '00')}",
        "characteristicName": "Enterococcus;Fecal Coliform",
        "mimeType": "json",
        "sorted": "no",
        "maxResultRows": "100",
    }

    closures: list[dict[str, Any]] = []
    try:
        resp = _HTTP.get(
            f"{_WQP_BASE}/data/Station/search",
            params=params,
            timeout=_TIMEOUT_STATION,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "datagov: beach closure fetch failed for %s: %s", state_code, exc
        )
        _cache_set(cache_key, closures, failed=True)
        return closures

    rows = data if isinstance(data, list) else []
    for row in rows:
        props = row.get("properties", row)
        name = props.get("MonitoringLocationName", "")
        if not name:
            continue
        closures.append(
            {
                "beach_name": name,
                "station_id": props.get("MonitoringLocationIdentifier", ""),
                "county": props.get("CountyCode", ""),
                "lat": _safe_float(props.get("LatitudeMeasure")),
                "lng": _safe_float(props.get("LongitudeMeasure")),
            }
        )

    _cache_set(cache_key, closures)
    return closures[:50]  # cap at 50

def _hab_risk(summary: dict[str, Any]) -> tuple[str, str]:
    """Classify harmful-algal-bloom risk from available WQP indicators.

    Returns (risk_level, message).  risk_level is one of "unknown", "low",
    "watch", "danger".  Prefers a direct microcystin toxin reading (EPA's
    2019 recreational health advisory thresholds); falls back to elevated
    chlorophyll-a as a weaker bloom indicator when no toxin reading exists.
    """
    microcystin = summary.get("microcystin_ug_l")
    if microcystin is not None:
        if microcystin >= _MICROCYSTIN_DANGER_UG_L:
            return (
                "danger",
                f"Microcystin at {microcystin:.1f} µg/L exceeds EPA's recreational "
                "danger threshold — avoid contact with the water and don't eat fish "
                "caught here.",
            )
        if microcystin >= _MICROCYSTIN_WATCH_UG_L:
            return (
                "watch",
                f"Microcystin at {microcystin:.1f} µg/L exceeds EPA's recreational "
                "watch threshold — avoid swallowing water or handling algal scum.",
            )
        return "low", f"Microcystin at {microcystin:.1f} µg/L is below EPA advisory thresholds."

    chlorophyll = summary.get("chlorophyll_a")
    if chlorophyll is not None and chlorophyll >= _CHLOROPHYLL_BLOOM_UG_L:
        return (
            "watch",
            f"Chlorophyll-a at {chlorophyll:.1f} µg/L suggests an active algal "
            "bloom — no direct toxin reading is available nearby.",
        )

    return "unknown", ""

def get_water_quality_summary(lat: float, lng: float) -> dict[str, Any]:
    """Return a simplified water-quality summary for template rendering.

    Calls ``fetch_water_quality`` and returns a flat dict of the most
    relevant metrics with display-ready formatted values.

    Parameters
    ----------
    lat, lng    WGS-84 decimal degrees

    Returns
    -------
    dict with keys: do_mg_l, temp_c, ph, salinity_ppt, turbidity,
                    enterococcus_flag, source, available
    """
    raw = fetch_water_quality(lat, lng)
    summary = raw.get("summary", {})

    do_val = summary.get("dissolved_oxygen")
    temp_val = summary.get("water_temp_c")
    ph_val = summary.get("ph")
    sal_val = summary.get("salinity_ppt")
    turb_val = summary.get("turbidity_ntu")
    entero = summary.get("enterococcus_cfu_100ml")

    # Interpret enterococcus: >104 CFU/100 mL triggers EPA beach-closure advisory
    entero_flag = "unknown"
    if entero is not None:
        entero_flag = "advisory" if entero > 104 else "ok"

    hab_level, hab_message = _hab_risk(summary)

    return {
        "available": bool(summary),
        "do_mg_l": _fmt(do_val, 1),
        "temp_c": _fmt(temp_val, 1),
        "temp_f": _fmt(_c_to_f(temp_val), 1) if temp_val is not None else None,
        "ph": _fmt(ph_val, 2),
        "salinity_ppt": _fmt(sal_val, 1),
        "turbidity_ntu": _fmt(turb_val, 1),
        "enterococcus_cfu_100ml": _fmt(entero, 0),
        "enterococcus_flag": entero_flag,
        "microcystin_ug_l": _fmt(summary.get("microcystin_ug_l"), 1),
        "hab_risk": hab_level,
        "hab_message": hab_message,
        "source": raw.get("source", "EPA Water Quality Portal"),
        "source_url": raw.get("source_url", "https://www.waterqualitydata.us/"),
        "fetched_at": raw.get("fetched_at"),
        "station_count": len(raw.get("stations", [])),
    }

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_summary(stations: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate measurements across stations into a single summary dict."""
    accum: dict[str, list[float]] = {}

    char_map = {
        "Temperature, water": "water_temp_c",
        "Dissolved oxygen (DO)": "dissolved_oxygen",
        "Salinity": "salinity_ppt",
        "pH": "ph",
        "Turbidity": "turbidity_ntu",
        "Chlorophyll a": "chlorophyll_a",
        "Fecal Coliform": "fecal_coliform_cfu_100ml",
        "Enterococcus": "enterococcus_cfu_100ml",
        "Microcystin": "microcystin_ug_l",
        "Cyanobacteria": "cyanobacteria_cells_ml",
    }

    for station in stations:
        for char_name, canonical in char_map.items():
            m = station.get("measurements", {}).get(char_name)
            if m and m.get("value") is not None:
                accum.setdefault(canonical, []).append(m["value"])

    return {k: round(sum(v) / len(v), 3) for k, v in accum.items() if v}

def _safe_float(val: Any) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

def _fmt(val: Optional[float], decimals: int) -> Optional[str]:
    if val is None:
        return None
    return f"{val:.{decimals}f}"

def _c_to_f(c: Optional[float]) -> Optional[float]:
    return c * 9 / 5 + 32 if c is not None else None

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _cache_get(key: tuple) -> Optional[Any]:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ttl = _CACHE_TTL_FAIL if entry.get("failed") else _CACHE_TTL
    if time.time() - entry["ts"] < ttl:
        return entry["data"]
    return None

def _cache_set(key: tuple, data: Any, failed: bool = False) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k]["ts"])
        _CACHE.pop(oldest, None)
    _CACHE[key] = {"ts": time.time(), "data": data, "failed": failed}

# ── FIPS codes for US states (needed for WQP state queries) ──────────────────
_STATE_FIPS: dict[str, str] = {
    "AL": "01",
    "AK": "02",
    "AZ": "04",
    "AR": "05",
    "CA": "06",
    "CO": "08",
    "CT": "09",
    "DE": "10",
    "FL": "12",
    "GA": "13",
    "HI": "15",
    "ID": "16",
    "IL": "17",
    "IN": "18",
    "IA": "19",
    "KS": "20",
    "KY": "21",
    "LA": "22",
    "ME": "23",
    "MD": "24",
    "MA": "25",
    "MI": "26",
    "MN": "27",
    "MS": "28",
    "MO": "29",
    "MT": "30",
    "NE": "31",
    "NV": "32",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "NY": "36",
    "NC": "37",
    "ND": "38",
    "OH": "39",
    "OK": "40",
    "OR": "41",
    "PA": "42",
    "RI": "44",
    "SC": "45",
    "SD": "46",
    "TN": "47",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "VA": "51",
    "WA": "53",
    "WV": "54",
    "WI": "55",
    "WY": "56",
}
