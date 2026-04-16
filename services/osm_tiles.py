"""OpenStreetMap tile configuration and Overpass API enrichment.

Integration points
------------------
- Provides Leaflet tile-layer configs (URL templates + attribution) for the
  dashboard's interactive map so the front-end never needs a hard-coded URL.
- Optional Overpass API: enriches the map with nearby marinas, harbours, and
  boat-launch ramps (supplements fish_structures.py which covers piers/reefs).

Public API
----------
    get_tile_config() -> dict
        Returns tile-layer descriptors for OSM Standard and OSM Humanitarian.

    fetch_osm_amenities(lat, lng, radius_m=2000) -> List[dict]
        Returns nearby marine amenities from Overpass (no API key required).

Caching
-------
Tile config is static.  Overpass results are cached in-process for
``_CACHE_TTL`` seconds, keyed on rounded coordinates + radius.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

# ── HTTP session with connection pooling ──────────────────────────────────────
_HTTP: requests.Session = requests.Session()
_HTTP.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=4))
_HTTP.mount("http://", HTTPAdapter(pool_connections=2, pool_maxsize=4))

# ── In-process Overpass result cache ─────────────────────────────────────────
_CACHE: Dict[tuple, Dict[str, Any]] = {}
_CACHE_TTL: int = 1800  # 30 min — harbour infrastructure changes rarely
_CACHE_TTL_FAIL: int = 120  # 2 min — retry failed queries sooner
_CACHE_MAX: int = 128

# ── Overpass mirror list (tried in order, first success wins) ─────────────────
_OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# ── OSM amenity tags relevant to fishing / marine activities ──────────────────
# Each entry is an Overpass tag filter and a human-readable label.
_AMENITY_FILTERS: List[tuple[str, str]] = [
    ("amenity=marina", "marina"),
    ("leisure=marina", "marina"),
    ("amenity=boat_rental", "boat_rental"),
    ("leisure=slipway", "boat_launch"),
    ("amenity=fishing", "fishing_access"),
    ("leisure=fishing", "fishing_access"),
    ("landuse=harbour", "harbour"),
    ("man_made=pier", "pier"),
    ("man_made=jetty", "jetty"),
    ("amenity=fuel", "marine_fuel"),  # may or may not be marine
]

# Leaflet attribution strings
_OSM_ATTRIB = (
    '&copy; <a href="https://www.openstreetmap.org/copyright" '
    'rel="noopener noreferrer" target="_blank">OpenStreetMap</a> contributors'
)
_HOT_ATTRIB = (
    '&copy; <a href="https://www.openstreetmap.org/copyright" '
    'rel="noopener noreferrer" target="_blank">OpenStreetMap</a> contributors, '
    'Tiles courtesy of <a href="https://hot.openstreetmap.org/" '
    'rel="noopener noreferrer" target="_blank">Humanitarian OSM Team</a>'
)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def get_tile_config() -> Dict[str, Any]:
    """Return Leaflet tile-layer configurations for all OSM-based base maps.

    All URL templates use {z}/{x}/{y} placeholders that Leaflet resolves
    client-side — no server-side proxy is required.

    Returns
    -------
    dict with keys:
        layers  list of layer descriptors (id, label, url, options)
        default str  id of the recommended default layer
    """
    return {
        "default": "osm_standard",
        "layers": [
            {
                "id": "osm_standard",
                "label": "OpenStreetMap",
                "url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                "options": {
                    "attribution": _OSM_ATTRIB,
                    "maxZoom": 19,
                    "subdomains": ["a", "b", "c"],
                },
                "source": "OpenStreetMap",
                "source_url": "https://www.openstreetmap.org/",
                "license": "ODbL",
            },
            {
                "id": "osm_humanitarian",
                "label": "OSM Humanitarian",
                "url": "https://tile-{s}.openstreetmap.fr/hot/{z}/{x}/{y}.png",
                "options": {
                    "attribution": _HOT_ATTRIB,
                    "maxZoom": 19,
                    "subdomains": ["a", "b", "c"],
                },
                "source": "OpenStreetMap / HOT",
                "source_url": "https://hot.openstreetmap.org/",
                "license": "ODbL",
            },
            {
                "id": "osm_cycle",
                "label": "OpenCycleMap",
                "url": "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
                "options": {
                    "attribution": (
                        _OSM_ATTRIB + " &mdash; Rendering: "
                        '<a href="https://www.cyclosm.org/" rel="noopener noreferrer">CyclOSM</a>'
                    ),
                    "maxZoom": 20,
                    "subdomains": ["a", "b", "c"],
                },
                "source": "CyclOSM",
                "source_url": "https://www.cyclosm.org/",
                "license": "ODbL",
            },
        ],
    }


def fetch_osm_amenities(
    lat: float,
    lng: float,
    radius_m: int = 2000,
) -> List[Dict[str, Any]]:
    """Fetch nearby marine amenities from the Overpass API.

    Uses a compact Overpass QL query that retrieves nodes and ways matching
    fishing/marina/harbour tags within ``radius_m`` metres of the given point.
    Results are cached in-process for 30 minutes.

    Parameters
    ----------
    lat, lng    WGS-84 decimal degrees
    radius_m    search radius in metres (default 2 km)

    Returns
    -------
    List of dicts: {lat, lng, type, name, tags}
    Returns [] on any error so callers always get a list.
    """
    cache_key = (round(lat, 3), round(lng, 3), radius_m)
    now = time.time()

    # Cache hit?
    entry = _CACHE.get(cache_key)
    if entry:
        ttl = _CACHE_TTL_FAIL if entry.get("failed") else _CACHE_TTL
        if now - entry["ts"] < ttl:
            return entry["data"]

    # Build Overpass QL query — unions all amenity types in a single request
    union_parts = "\n".join(
        f'  node["{tag.split("=")[0]}"="{tag.split("=")[1]}"](around:{radius_m},{lat},{lng});'
        for tag, _ in _AMENITY_FILTERS
    )
    query = f"[out:json][timeout:15];\n(\n{union_parts}\n);\nout body;"

    results: List[Dict[str, Any]] = []
    failed = True

    for mirror in _OVERPASS_MIRRORS:
        try:
            resp = _HTTP.post(mirror, data={"data": query}, timeout=(4, 15))
            if resp.status_code == 200:
                data = resp.json()
                results = _parse_overpass_elements(data.get("elements", []))
                failed = False
                break
        except Exception as exc:  # noqa: BLE001
            logger.debug("osm_tiles: Overpass mirror %s error: %s", mirror, exc)
            continue

    if failed:
        logger.warning(
            "osm_tiles: all Overpass mirrors failed for (%.3f, %.3f)", lat, lng
        )

    # Evict stale entries if cache is too large
    if len(_CACHE) >= _CACHE_MAX:
        _evict_cache(now)

    _CACHE[cache_key] = {"ts": now, "data": results, "failed": failed}
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _parse_overpass_elements(
    elements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert raw Overpass elements into simple location dicts."""
    out: List[Dict[str, Any]] = []
    for el in elements:
        if el.get("type") != "node":
            continue
        node_lat = el.get("lat")
        node_lng = el.get("lon")
        if node_lat is None or node_lng is None:
            continue
        tags = el.get("tags", {})
        amenity_type = _classify_osm_tags(tags)
        out.append(
            {
                "lat": node_lat,
                "lng": node_lng,
                "type": amenity_type,
                "name": tags.get("name", ""),
                "tags": {
                    k: v
                    for k, v in tags.items()
                    if k
                    in (
                        "name",
                        "amenity",
                        "leisure",
                        "man_made",
                        "landuse",
                        "access",
                        "fee",
                        "opening_hours",
                        "phone",
                        "website",
                    )
                },
            }
        )
    return out


def _classify_osm_tags(tags: Dict[str, str]) -> str:
    """Map OSM tags to a canonical amenity type string."""
    for tag_expr, label in _AMENITY_FILTERS:
        key, val = tag_expr.split("=", 1)
        if tags.get(key) == val:
            return label
    return "other"


def _evict_cache(now: float) -> None:
    """Remove expired entries; if still over cap, drop oldest by timestamp."""
    stale = [
        k
        for k, v in list(_CACHE.items())
        if now - v["ts"] >= (_CACHE_TTL_FAIL if v.get("failed") else _CACHE_TTL)
    ]
    for k in stale:
        _CACHE.pop(k, None)
    # If still over cap, drop oldest entries
    if len(_CACHE) >= _CACHE_MAX:
        sorted_keys = sorted(_CACHE.keys(), key=lambda k: _CACHE[k]["ts"])
        for k in sorted_keys[: len(_CACHE) - _CACHE_MAX + 1]:
            _CACHE.pop(k, None)
