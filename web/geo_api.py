"""Geospatial data API blueprint.

Exposes REST endpoints that serve GIS layer configurations, environmental
data, and satellite imagery metadata to the front-end map controls.

All endpoints are GET-only, unauthenticated (public read), rate-limited,
and return JSON.  They follow the same conventions as the existing
``web/api.py`` blueprint (envelope format, CORS-safe, gzip-compressed by
the app factory middleware).

Routes
------
GET /api/v1/geo/layers
    All available map layer configurations (OSM, NASA GIBS, aerial, NE).

GET /api/v1/geo/environmental
    Water quality and environmental metrics for a lat/lng.

GET /api/v1/geo/coastlines
    Natural Earth coastline GeoJSON clipped to a bbox.

GET /api/v1/geo/osm/amenities
    OpenStreetMap marine amenities (marinas, boat ramps) near a point.

GET /api/v1/geo/esri/piers
    Esri Open Data pier / marina features for a bbox.

GET /api/v1/geo/esri/beaches
    Esri Open Data EPA beach locations for a bbox.

GET /api/v1/geo/esri/parks
    Esri Open Data NPS coastal park boundaries for a bbox.

GET /api/v1/geo/hdx-fao
    FAO fishing-zone identification and HDX dataset links for a lat/lng.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from flask import Blueprint, jsonify, request

# ── New geospatial services ───────────────────────────────────────────────────
from services.osm_tiles import get_tile_config, fetch_osm_amenities
from services.natural_earth import get_coastlines_geojson, get_state_boundaries_geojson
from services.datagov import get_water_quality_summary, fetch_beach_closures
from services.esri_open_data import (
    fetch_pier_locations,
    fetch_coastal_parks,
    fetch_epa_beaches,
    fetch_esri_layers_config,
)
from services.nasa_worldview import get_gibs_layers, get_sst_tile_config
from services.aerial_imagery import get_aerial_tile_config, search_oam_imagery
from services.hdx_fao import get_hdx_fao_enrichment

logger = logging.getLogger(__name__)

bp = Blueprint("geo_api", __name__)

# ── Per-IP rate limiting (shared with existing pattern in web/api.py) ─────────
_GEO_RATE_LIMIT_MAX = 60  # 60 requests per window
_GEO_RATE_LIMIT_WINDOW_S = 60  # 1-minute window
_geo_rate_store: dict[str, tuple[float, int]] = {}
_geo_rate_lock = threading.Lock()

_TRUST_PROXY: bool = False  # set from os.environ in app.py if needed

# ── bbox validation constants ─────────────────────────────────────────────────
_BBOX_MAX_DEGREES = 10.0  # reject unreasonably large bboxes

# ─────────────────────────────────────────────────────────────────────────────
# Rate limiting helper
# ─────────────────────────────────────────────────────────────────────────────

def _client_ip() -> str:
    if _TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"

def _is_rate_limited() -> bool:
    ip = _client_ip()
    now = time.time()
    with _geo_rate_lock:
        # Prune stale entries periodically (every ~100 requests)
        if len(_geo_rate_store) > 500:
            cutoff = now - _GEO_RATE_LIMIT_WINDOW_S
            stale = [k for k, (t, _) in _geo_rate_store.items() if t < cutoff]
            for k in stale:
                _geo_rate_store.pop(k, None)

        window_start, count = _geo_rate_store.get(ip, (now, 0))
        if now - window_start > _GEO_RATE_LIMIT_WINDOW_S:
            # New window
            _geo_rate_store[ip] = (now, 1)
            return False
        if count >= _GEO_RATE_LIMIT_MAX:
            return True
        _geo_rate_store[ip] = (window_start, count + 1)
        return False

def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status

def _ok(data: Any):
    return jsonify({"ok": True, "data": data})

# ─────────────────────────────────────────────────────────────────────────────
# Parameter helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_latlon() -> Optional[tuple[float, float]]:
    """Parse ?lat=&lng= from query string.  Returns None on bad input."""
    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, ValueError, TypeError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng

def _parse_bbox() -> Optional[tuple[float, float, float, float]]:
    """Parse ?south=&west=&north=&east= query parameters.

    Returns (south, west, north, east) or None if any value is invalid.
    """
    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError, TypeError):
        return None

    if not (-90 <= south <= north <= 90):
        return None
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        return None
    if (north - south) > _BBOX_MAX_DEGREES or abs(east - west) > _BBOX_MAX_DEGREES:
        return None
    return south, west, north, east

# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/api/v1/geo/layers")
def geo_layers():
    """Return all available map layer configurations.

    No parameters required.  Returns a combined dict with OSM base layers,
    NASA GIBS imagery layers, Esri aerial/satellite layers, and Natural
    Earth vector layer info.

    Optional query parameters:
        date    ISO date string for GIBS time-varying layers (default: yesterday)
    """
    if _is_rate_limited():
        return _err("Rate limit exceeded", 429)

    date_param = request.args.get("date")

    osm_config = get_tile_config()
    gibs_config = get_gibs_layers(date=date_param)
    aerial_config = get_aerial_tile_config()
    esri_layers = fetch_esri_layers_config()

    return _ok(
        {
            "base_layers": {
                "osm": osm_config,
            },
            "overlay_layers": {
                "nasa_gibs": gibs_config,
                "aerial": aerial_config,
            },
            "data_layers": {
                "esri": esri_layers,
            },
            "natural_earth": {
                "description": "Natural Earth public-domain vector overlays",
                "source_url": "https://www.naturalearthdata.com/",
                "license": "Public Domain (CC0)",
                "endpoints": {
                    "coastlines": "/api/v1/geo/coastlines",
                    "states": "/api/v1/geo/coastlines?layer=states",
                },
            },
        }
    )

@bp.route("/api/v1/geo/environmental")
def geo_environmental():
    """Return water quality and environmental metrics for a location.

    Required query parameters:
        lat     Latitude (decimal degrees)
        lng     Longitude (decimal degrees)

    Optional:
        state   Two-letter US state code (for beach closure lookup)
    """
    if _is_rate_limited():
        return _err("Rate limit exceeded", 429)

    coords = _parse_latlon()
    if coords is None:
        return _err("lat and lng are required (valid decimal degrees)")
    lat, lng = coords

    state = request.args.get("state", "").upper().strip()

    # Water quality summary (EPA WQP) — primary data
    wq_summary = get_water_quality_summary(lat, lng)

    # SST tile config for the map overlay
    sst_config = get_sst_tile_config()

    # Beach closures (if state code provided)
    beach_closures: list = []
    if state and len(state) == 2:
        beach_closures = fetch_beach_closures(state)[:10]

    return _ok(
        {
            "water_quality": wq_summary,
            "sst_tile": sst_config,
            "beach_closures": beach_closures,
            "location": {"lat": lat, "lng": lng},
        }
    )

@bp.route("/api/v1/geo/coastlines")
def geo_coastlines():
    """Return Natural Earth coastline GeoJSON, optionally clipped to a bbox.

    Optional query parameters:
        south, west, north, east    Bounding box (decimal degrees)
        layer   ``"coastline"`` (default) or ``"states"``
        res     ``"110m"`` (default) or ``"10m"``
    """
    if _is_rate_limited():
        return _err("Rate limit exceeded", 429)

    bbox = _parse_bbox()  # None → return full global dataset (110m)
    layer = request.args.get("layer", "coastline")
    res = request.args.get("res", "110m")

    if res not in ("110m", "10m"):
        return _err("res must be '110m' or '10m'")

    if layer == "states":
        geojson = get_state_boundaries_geojson()
    else:
        geojson = get_coastlines_geojson(bbox=bbox, resolution=res)

    return jsonify(geojson)

@bp.route("/api/v1/geo/osm/amenities")
def geo_osm_amenities():
    """Return OpenStreetMap marine amenities near a point.

    Required:
        lat, lng    Decimal degrees

    Optional:
        radius_m    Search radius in metres (default 2000, max 10000)
    """
    if _is_rate_limited():
        return _err("Rate limit exceeded", 429)

    coords = _parse_latlon()
    if coords is None:
        return _err("lat and lng are required (valid decimal degrees)")
    lat, lng = coords

    try:
        radius_m = min(int(request.args.get("radius_m", 2000)), 10000)
    except (ValueError, TypeError):
        radius_m = 2000

    amenities = fetch_osm_amenities(lat, lng, radius_m=radius_m)
    return _ok({"amenities": amenities, "count": len(amenities)})

@bp.route("/api/v1/geo/esri/piers")
def geo_esri_piers():
    """Return pier / marina features for a bounding box (Esri Open Data)."""
    if _is_rate_limited():
        return _err("Rate limit exceeded", 429)

    bbox = _parse_bbox()
    if bbox is None:
        return _err("south, west, north, east are required (valid decimal degrees)")
    south, west, north, east = bbox

    features = fetch_pier_locations(south, west, north, east)
    return _ok({"features": features, "count": len(features)})

@bp.route("/api/v1/geo/esri/beaches")
def geo_esri_beaches():
    """Return EPA-monitored beach locations for a bounding box."""
    if _is_rate_limited():
        return _err("Rate limit exceeded", 429)

    bbox = _parse_bbox()
    if bbox is None:
        return _err("south, west, north, east are required (valid decimal degrees)")
    south, west, north, east = bbox

    beaches = fetch_epa_beaches(south, west, north, east)
    return _ok({"features": beaches, "count": len(beaches)})

@bp.route("/api/v1/geo/esri/parks")
def geo_esri_parks():
    """Return NPS coastal park boundaries for a bounding box."""
    if _is_rate_limited():
        return _err("Rate limit exceeded", 429)

    bbox = _parse_bbox()
    if bbox is None:
        return _err("south, west, north, east are required (valid decimal degrees)")
    south, west, north, east = bbox

    parks = fetch_coastal_parks(south, west, north, east)
    return _ok({"features": parks, "count": len(parks)})

@bp.route("/api/v1/geo/aerial/oam")
def geo_oam_imagery():
    """Return OpenAerialMap imagery catalog results for a bounding box."""
    if _is_rate_limited():
        return _err("Rate limit exceeded", 429)

    bbox = _parse_bbox()
    if bbox is None:
        return _err("south, west, north, east are required (valid decimal degrees)")
    south, west, north, east = bbox

    results = search_oam_imagery(south, west, north, east, limit=8)
    return _ok({"imagery": results, "count": len(results)})

@bp.route("/api/v1/geo/hdx-fao")
def geo_hdx_fao():
    """Return FAO fishing-zone and HDX dataset enrichment for a location.

    Required:
        lat, lng    Decimal degrees

    Optional:
        species     Comma-separated list of species common names
    """
    if _is_rate_limited():
        return _err("Rate limit exceeded", 429)

    coords = _parse_latlon()
    if coords is None:
        return _err("lat and lng are required (valid decimal degrees)")
    lat, lng = coords

    species_param = request.args.get("species", "")
    species_names = (
        [s.strip() for s in species_param.split(",") if s.strip()][:5]
        if species_param
        else []
    )

    enrichment = get_hdx_fao_enrichment(lat, lng, species_names)
    return _ok(enrichment)
