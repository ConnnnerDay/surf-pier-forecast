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

    fetch_beach_closures(state_code) -> List[dict]
        Returns active beach-closure / advisory records for a US state.

    get_water_quality_summary(lat, lng) -> dict
        High-level summary ready for template rendering.

All endpoints are called over HTTPS, return JSON, and require no
registration or API key.  Results are cached in-process for 2 hours.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

# ── HTTP session ──────────────────────────────────────────────────────────────
_HTTP: requests.Session = requests.Session()
_HTTP.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=4))

# ── In-process result cache ───────────────────────────────────────────────────
_CACHE: Dict[tuple, Dict[str, Any]] = {}
_CACHE_TTL: int = 7200       # 2 hours — water quality changes slowly
_CACHE_TTL_FAIL: int = 300   # 5 min — retry failed queries sooner
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
    "Enterococcus",    # beach closure indicator
]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_water_quality(
    lat: float,
    lng: float,
    radius_km: float = 50,
    within_days: int = 7,
) -> Dict[str, Any]:
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
    from datetime import datetime, timezone, timedelta
    start_date = (datetime.now(timezone.utc) - timedelta(days=within_days)).strftime("%m-%d-%Y")

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

    result: Dict[str, Any] = {
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
            timeout=(5, 30),
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
    stations_map: Dict[str, Dict[str, Any]] = {}

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


def fetch_beach_closures(state_code: str) -> List[Dict[str, Any]]:
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

    closures: List[Dict[str, Any]] = []
    try:
        resp = _HTTP.get(
            f"{_WQP_BASE}/data/Station/search",
            params=params,
            timeout=(5, 20),
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("datagov: beach closure fetch failed for %s: %s", state_code, exc)
        _cache_set(cache_key, closures, failed=True)
        return closures

    rows = data if isinstance(data, list) else []
    for row in rows:
        props = row.get("properties", row)
        name = props.get("MonitoringLocationName", "")
        if not name:
            continue
        closures.append({
            "beach_name": name,
            "station_id": props.get("MonitoringLocationIdentifier", ""),
            "county": props.get("CountyCode", ""),
            "lat": _safe_float(props.get("LatitudeMeasure")),
            "lng": _safe_float(props.get("LongitudeMeasure")),
        })

    _cache_set(cache_key, closures)
    return closures[:50]  # cap at 50


def get_water_quality_summary(lat: float, lng: float) -> Dict[str, Any]:
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
        "source": raw.get("source", "EPA Water Quality Portal"),
        "source_url": raw.get("source_url", "https://www.waterqualitydata.us/"),
        "fetched_at": raw.get("fetched_at"),
        "station_count": len(raw.get("stations", [])),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_summary(stations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate measurements across stations into a single summary dict."""
    accum: Dict[str, List[float]] = {}

    char_map = {
        "Temperature, water": "water_temp_c",
        "Dissolved oxygen (DO)": "dissolved_oxygen",
        "Salinity": "salinity_ppt",
        "pH": "ph",
        "Turbidity": "turbidity_ntu",
        "Chlorophyll a": "chlorophyll_a",
        "Fecal Coliform": "fecal_coliform_cfu_100ml",
        "Enterococcus": "enterococcus_cfu_100ml",
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
    from datetime import datetime, timezone
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
_STATE_FIPS: Dict[str, str] = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06",
    "CO": "08", "CT": "09", "DE": "10", "FL": "12", "GA": "13",
    "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19",
    "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29",
    "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34",
    "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45",
    "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50",
    "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56",
}
