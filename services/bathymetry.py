"""Seafloor depth near a pier via NOAA NCEI's coastal bathymetric DEM mosaic.

NOAA NCEI hosts a continuously-updated mosaic of coastal digital elevation
models (LIDAR + bathymetric survey data) as a public, no-key ArcGIS
ImageServer. Its "identify" operation returns the elevation (in meters,
negative below sea level) at a single point -- exactly what's needed to
describe the drop-off/structure near a pier, which matters for where fish
stage.

No API key required; free NOAA public data.
"""

from __future__ import annotations

import concurrent.futures as _cf
import logging
import math
import time
from typing import Any, Optional
from urllib.parse import urlencode

from services.http_client import get as http_get

logger = logging.getLogger(__name__)

_DEM_IDENTIFY_URL = (
    "https://gis.ngdc.noaa.gov/arcgis/rest/services/DEM_mosaics/DEM_all/ImageServer/identify"
)
_TIMEOUT: tuple[float, float] = (5, 15)

_METERS_TO_FEET = 3.28084
_NM_TO_DEG_LAT = 1 / 60.0  # 1 nautical mile ~= 1 minute of latitude

# Seaward compass bearing (0=N, 90=E, 180=S, 270=W) by coastline orientation,
# matching domain.forecast._wind_orientation()'s categories.
_SEAWARD_BEARING: dict[str, Optional[float]] = {
    "east": 90.0,
    "west": 270.0,
    "gulf": 180.0,
    "hawaii": None,  # omnidirectional; no single seaward heading
}

_CACHE: dict[tuple, dict[str, Any]] = {}
_CACHE_TTL = 30 * 24 * 3600  # 30 days -- the seafloor doesn't move
_CACHE_TTL_FAIL = 600
_CACHE_MAX = 512


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


def cache_clear() -> None:
    """Clear the cached depth lookups. Useful in tests."""
    _CACHE.clear()


def _offset_point(
    lat: float, lng: float, bearing_deg: float, distance_nm: float
) -> tuple[float, float]:
    """Return a (lat, lng) point *distance_nm* along *bearing_deg* from (lat, lng).

    Uses a flat-earth approximation -- accurate enough at the few-nautical-
    mile scale used here.
    """
    bearing = math.radians(bearing_deg)
    dlat = distance_nm * _NM_TO_DEG_LAT * math.cos(bearing)
    dlng = (
        distance_nm
        * _NM_TO_DEG_LAT
        * math.sin(bearing)
        / max(math.cos(math.radians(lat)), 0.01)
    )
    return lat + dlat, lng + dlng


def fetch_depth_at_point(lat: float, lng: float) -> Optional[float]:
    """Return seafloor/land elevation in feet at a point.

    Negative values are underwater depth; positive values are land
    elevation. Returns ``None`` when the DEM mosaic has no coverage there
    or the service is unreachable.
    """
    key = (round(lat, 4), round(lng, 4))
    cached = _cache_get(key)
    if cached is not None:
        return cached

    geometry = f'{{"x":{lng},"y":{lat},"spatialReference":{{"wkid":4326}}}}'
    params = {
        "geometry": geometry,
        "geometryType": "esriGeometryPoint",
        "returnGeometry": "false",
        "f": "json",
    }
    url = f"{_DEM_IDENTIFY_URL}?{urlencode(params)}"

    try:
        resp = http_get(url, endpoint="ncei.dem_identify", timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("value")
        if raw is None or str(raw).strip().lower() in ("nodata", ""):
            _cache_set(key, None, failed=True)
            return None
        meters = float(raw)
    except Exception:
        logger.debug("NCEI DEM identify failed for (%s, %s)", lat, lng, exc_info=True)
        _cache_set(key, None, failed=True)
        return None

    feet = round(meters * _METERS_TO_FEET, 1)
    _cache_set(key, feet)
    return feet


def get_depth_profile(lat: float, lng: float, orientation: str = "east") -> dict[str, Any]:
    """Return depth at a pier point plus a rough seaward drop-off profile.

    *orientation* should be one of the values returned by
    ``domain.forecast._wind_orientation()`` ("east"/"west"/"gulf"/"hawaii").
    Samples depth at 0.5, 1, and 2 nautical miles along that coastline's
    seaward bearing so the profile shows how quickly the bottom drops off --
    useful context for where fish are likely to stage relative to the pier.

    Returns {available, point_depth_ft, profile, source, source_url}.
    """
    bearing = _SEAWARD_BEARING.get(orientation, 90.0)
    points = [(lat, lng)]
    distances = [0.0]
    if bearing is not None:
        for nm in (0.5, 1.0, 2.0):
            points.append(_offset_point(lat, lng, bearing, nm))
            distances.append(nm)

    with _cf.ThreadPoolExecutor(max_workers=len(points), thread_name_prefix="bathy") as pool:
        futures = [pool.submit(fetch_depth_at_point, plat, plng) for plat, plng in points]
        depths = []
        for fut in futures:
            try:
                depths.append(fut.result(timeout=18))
            except Exception:
                depths.append(None)

    point_depth = depths[0]
    profile = [
        {"distance_nm": nm, "depth_ft": depth}
        for nm, depth in zip(distances[1:], depths[1:])
        if depth is not None
    ]

    return {
        "available": point_depth is not None or bool(profile),
        "point_depth_ft": point_depth,
        "profile": profile,
        "source": "NOAA NCEI Coastal Digital Elevation Models",
        "source_url": "https://www.ncei.noaa.gov/products/coastal-relief-model",
    }
