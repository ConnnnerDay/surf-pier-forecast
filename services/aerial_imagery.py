"""Aerial imagery overlay integration.

This module provides access to two free, key-free aerial/satellite imagery
sources suitable for close-up coastal and pier views:

1. **OpenAerialMap** (https://openaerialmap.org/)
   A crowdsourced repository of openly-licensed drone and aerial imagery.
   The OAM Catalog API allows searching by bounding box without authentication.
   Images can be particularly useful for pier and harbour areas where drone
   surveys have been conducted.

2. **Zoom Earth tile fallback (ESRI World Imagery)**
   The ESRI World Imagery basemap is available as a public WMTS service
   through ArcGIS Online and Living Atlas without a key.  It provides
   high-resolution satellite imagery globally and is the recommended
   fallback when OAM coverage is absent.

Integration points
------------------
    get_aerial_tile_config() -> dict
        Returns tile layer configurations for Leaflet (ESRI + optional OAM).

    search_oam_imagery(south, west, north, east) -> List[dict]
        Searches the OAM catalog for imagery covering a bounding box.

    get_imagery_sources() -> dict
        Combined metadata for all imagery sources — used to populate the
        front-end layer switcher.
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

# ── In-process cache ──────────────────────────────────────────────────────────
_CACHE: Dict[tuple, Dict[str, Any]] = {}
_CACHE_TTL: int = 3600  # 1 hour — OAM catalog changes infrequently
_CACHE_TTL_FAIL: int = 300
_CACHE_MAX: int = 128

# ── OpenAerialMap Catalog API ─────────────────────────────────────────────────
# Documentation: https://docs.openaerialmap.org/#tag/Catalog
_OAM_CATALOG_URL = "https://api.openaerialmap.org/meta"

# ── ESRI World Imagery (ArcGIS Online public WMTS) ────────────────────────────
# This is freely accessible for non-commercial use as part of ArcGIS Online's
# public services.  No API key required for basic tile access.
_ESRI_WORLD_IMAGERY_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
_ESRI_ATTRIB = (
    "Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, "
    "GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community"
)

# ── ESRI World Street Map (labelled, complements imagery) ─────────────────────
_ESRI_LABELS_URL = (
    "https://services.arcgisonline.com/ArcGIS/rest/services/"
    "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def get_aerial_tile_config() -> Dict[str, Any]:
    """Return Leaflet tile-layer configurations for aerial/satellite imagery.

    The ESRI World Imagery layer is always included as a reliable fallback.
    An optional label overlay is included for orientation.

    Returns
    -------
    dict with ``layers`` list and ``default`` layer id.
    """
    return {
        "default": "esri_world_imagery",
        "source": "Esri Open Data / OpenAerialMap",
        "layers": [
            {
                "id": "esri_world_imagery",
                "label": "Satellite (Esri World Imagery)",
                "url": _ESRI_WORLD_IMAGERY_URL,
                "options": {
                    "attribution": _ESRI_ATTRIB,
                    "maxZoom": 19,
                },
                "source": "Esri / USDA / USGS",
                "source_url": "https://www.arcgis.com/",
                "license": "Esri Master License Agreement",
                "offline_fallback": True,
            },
            {
                "id": "esri_labels",
                "label": "Satellite + Labels (Esri)",
                "url": _ESRI_WORLD_IMAGERY_URL,
                "label_url": _ESRI_LABELS_URL,
                "options": {
                    "attribution": _ESRI_ATTRIB,
                    "maxZoom": 19,
                },
                "source": "Esri",
                "source_url": "https://www.arcgis.com/",
                "license": "Esri Master License Agreement",
                "composite": True,
            },
        ],
    }


def search_oam_imagery(
    south: float,
    west: float,
    north: float,
    east: float,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Search OpenAerialMap for imagery covering a geographic bounding box.

    Parameters
    ----------
    south, west, north, east    WGS-84 bounding box
    limit                       Maximum number of results (default 10)

    Returns
    -------
    List of imagery result dicts:
        {title, provider, acquisition_date, resolution_m, thumbnail_url,
         tile_url, bbox, license, sensor}

    Returns [] on error or no coverage.
    """
    cache_key = (
        "oam",
        round(south, 2),
        round(west, 2),
        round(north, 2),
        round(east, 2),
        limit,
    )
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    params = {
        "bbox": f"{west},{south},{east},{north}",
        "limit": str(limit),
        "orderby": "acquisition_end",  # most recent first
    }

    results: List[Dict[str, Any]] = []
    failed = True

    try:
        resp = _HTTP.get(
            _OAM_CATALOG_URL,
            params=params,
            timeout=(5, 20),
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("results", [])
            for item in items:
                entry = _parse_oam_result(item)
                if entry:
                    results.append(entry)
            failed = False
        else:
            logger.debug(
                "aerial_imagery: OAM catalog returned status %s", resp.status_code
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("aerial_imagery: OAM catalog error: %s", exc)

    _cache_set(cache_key, results, failed=failed)
    return results


def get_imagery_sources() -> Dict[str, Any]:
    """Return combined metadata for all imagery sources.

    Used to populate the front-end layer switcher panel and attribution
    footer.  No network calls — static configuration only.
    """
    return {
        "sources": [
            {
                "id": "esri_imagery",
                "name": "Esri World Imagery",
                "description": (
                    "Global high-resolution satellite and aerial imagery "
                    "from Esri's ArcGIS Living Atlas.  Freely accessible "
                    "for non-commercial use without an API key."
                ),
                "url": "https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9",
                "license": "Esri Master License Agreement",
                "key_required": False,
                "coverage": "global",
            },
            {
                "id": "openaerial_map",
                "name": "OpenAerialMap",
                "description": (
                    "Open repository of drone and aerial imagery with "
                    "CC-licensed content.  Particularly useful for "
                    "coastal and pier areas with recent drone surveys."
                ),
                "url": "https://openaerialmap.org/",
                "license": "Various (CC0 / CC-BY)",
                "key_required": False,
                "coverage": "crowdsourced",
                "api_url": "https://api.openaerialmap.org/meta",
            },
            {
                "id": "zoom_earth_fallback",
                "name": "Zoom Earth (via ESRI tiles)",
                "description": (
                    "High-resolution satellite base using publicly "
                    "accessible tile services.  Provides cloud-free "
                    "composite imagery for coastal areas."
                ),
                "url": "https://zoom.earth/",
                "license": "Imagery from multiple providers via Esri",
                "key_required": False,
                "coverage": "global",
                "note": "Served via Esri World Imagery tile service",
            },
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _parse_oam_result(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a single OAM catalog result into a simplified dict."""
    uuid = item.get("uuid", "")
    title = item.get("title", item.get("provider", "OpenAerialMap image"))
    provider = item.get("provider", "")
    acquisition_end = item.get("acquisition_end", "")
    # GSD is ground sample distance in metres
    gsd = item.get("gsd")
    # thumbnail
    thumbnail = item.get("thumbnail", "")
    # Tile endpoint — OAM provides XYZ tilesets for each image
    tile_url = item.get("properties", {}).get("tms", "")
    # Bounding box [west, south, east, north]
    bbox = item.get("bbox", [])
    license_type = item.get("license", "CC-BY 4.0")
    sensor = item.get("properties", {}).get("sensor", "")

    if not uuid:
        return None

    # Compute a centre point from the bbox [west, south, east, north] so the
    # feature-layer renderer can place a marker.  Without lat/lng the JS code
    # filters every OAM item out and the layer appears empty.
    lat: Optional[float] = None
    lng: Optional[float] = None
    if isinstance(bbox, list) and len(bbox) == 4:
        try:
            lat = (float(bbox[1]) + float(bbox[3])) / 2
            lng = (float(bbox[0]) + float(bbox[2])) / 2
        except (TypeError, ValueError):
            pass

    return {
        "id": uuid,
        "title": title,
        "provider": provider,
        "acquisition_date": acquisition_end[:10] if acquisition_end else None,
        "resolution_m": round(float(gsd), 2) if gsd is not None else None,
        "thumbnail_url": thumbnail,
        "tile_url": tile_url,
        "bbox": bbox,
        "lat": lat,
        "lng": lng,
        "license": license_type,
        "sensor": sensor,
        "oam_url": f"https://openaerialmap.org/image/{uuid}",
    }


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
