"""Esri Open Data Hub and ArcGIS Living Atlas integration.

The Esri Open Data Hub (https://hub.arcgis.com/) and the ArcGIS Living Atlas
(https://livingatlas.arcgis.com/) expose thousands of government GIS datasets
via standard ArcGIS REST endpoints that require no API key for public layers.

This module queries public ArcGIS feature services to retrieve:
- Pier and dock locations (US Army Corps, state agencies)
- Public boat ramps and water-access points
- Coastal park and reserve boundaries
- Fishing-pier and fishing-access designations

All queries use the ``f=geojson`` format specifier so results arrive as
standard GeoJSON — no ESRI-specific libraries required.

Integration points
------------------
    fetch_pier_locations(bbox) -> list[dict]
        Nearby pier/dock/water-access features as GeoJSON points.

    fetch_coastal_parks(bbox) -> list[dict]
        Coastal park / protected-area polygons for a bounding box.

    fetch_fishing_access_points(lat, lng, radius_m) -> list[dict]
        ArcGIS Living Atlas fishing and boat-access features.

Caching
-------
Results are cached in-process for 30 minutes (same TTL as fish_structures.py).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

# ── HTTP session ──────────────────────────────────────────────────────────────
_HTTP: requests.Session = requests.Session()
_HTTP.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=6))

# ── In-process cache ──────────────────────────────────────────────────────────
_CACHE: dict[tuple, dict[str, Any]] = {}
_CACHE_TTL: int = 1800
_CACHE_TTL_FAIL: int = 120
_CACHE_MAX: int = 256

# ── Public ArcGIS feature service endpoints (no API key) ─────────────────────
# These are open/public layers hosted on ArcGIS Online or Living Atlas.
# The "query" endpoint accepts standard spatial filters and returns GeoJSON.

_SERVICES: dict[str, str] = {
    # NOAA Coastal Services Center — public piers and marinas
    "noaa_marinas": (
        "https://services2.arcgis.com/C8EMgrsFcRFL6LrL/arcgis/rest/services/"
        "Marinas_Public/FeatureServer/0/query"
    ),
    # USACE National Inventory of Dams & Water Resources (public access points)
    "usace_water_access": (
        "https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/"
        "BoatRamps/FeatureServer/0/query"
    ),
    # EPA Beaches (BEACON 2.0) — public beach locations
    "epa_beaches": (
        "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/"
        "BEACON_2_Beaches/FeatureServer/0/query"
    ),
    # National Park Service boundary data (Open Data)
    "nps_boundaries": (
        "https://services1.arcgis.com/fBc8EJBxQRMcHlei/arcgis/rest/services/"
        "NPS_Park_Boundaries/FeatureServer/0/query"
    ),
    # NOAA Fisheries Essential Fish Habitat (EFH) — coastal designations
    "noaa_efh": (
        "https://services2.arcgis.com/C8EMgrsFcRFL6LrL/arcgis/rest/services/"
        "EFH_Mapper/FeatureServer/0/query"
    ),
}

# Common ArcGIS REST query parameters for GeoJSON output
_COMMON_PARAMS: dict[str, str] = {
    "f": "geojson",
    "outFields": "*",
    "returnGeometry": "true",
    "where": "1=1",
}

_TIMEOUT: tuple[float, float] = (5.0, 20.0)

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_pier_locations(
    south: float,
    west: float,
    north: float,
    east: float,
) -> list[dict[str, Any]]:
    """Return pier / dock / marina features within a geographic bounding box.

    Queries the NOAA Marinas public ArcGIS layer, which includes official pier
    and marina locations along US coastlines.

    Parameters
    ----------
    south, west, north, east    WGS-84 bounding box

    Returns
    -------
    List of GeoJSON-style feature dicts {lat, lng, name, type, props}
    """
    cache_key = (
        "piers",
        round(south, 2),
        round(west, 2),
        round(north, 2),
        round(east, 2),
    )
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    results = _query_bbox(
        _SERVICES["noaa_marinas"],
        south,
        west,
        north,
        east,
        result_record_count=50,
    )

    # Also try the USACE boat-ramps layer
    ramps = _query_bbox(
        _SERVICES["usace_water_access"],
        south,
        west,
        north,
        east,
        result_record_count=30,
    )
    results.extend(ramps)

    features = _normalise_features(results, default_type="pier")
    _cache_set(cache_key, features, failed=not results)
    return features

def fetch_coastal_parks(
    south: float,
    west: float,
    north: float,
    east: float,
) -> list[dict[str, Any]]:
    """Return coastal park and protected-area features within a bounding box.

    Queries the NPS Park Boundaries open dataset for national parks and
    recreation areas whose envelopes intersect the given bbox.

    Returns
    -------
    List of feature dicts {name, type, area_ha, geometry_type, bbox}
    """
    cache_key = (
        "parks",
        round(south, 2),
        round(west, 2),
        round(north, 2),
        round(east, 2),
    )
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    results = _query_bbox(
        _SERVICES["nps_boundaries"],
        south,
        west,
        north,
        east,
        result_record_count=20,
    )

    features = _normalise_features(results, default_type="park")
    _cache_set(cache_key, features, failed=not results)
    return features

def fetch_epa_beaches(
    south: float,
    west: float,
    north: float,
    east: float,
) -> list[dict[str, Any]]:
    """Return EPA BEACON 2.0 monitored beach locations within a bounding box.

    These locations have water-quality monitoring data (enterococcus,
    fecal coliform) that feeds the Data.gov water-quality card.

    Returns
    -------
    List of feature dicts {name, lat, lng, state, county, beach_id}
    """
    cache_key = (
        "beaches",
        round(south, 2),
        round(west, 2),
        round(north, 2),
        round(east, 2),
    )
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    results = _query_bbox(
        _SERVICES["epa_beaches"],
        south,
        west,
        north,
        east,
        result_record_count=50,
    )
    features = _normalise_features(results, default_type="beach")
    _cache_set(cache_key, features, failed=not results)
    return features

def fetch_esri_layers_config() -> dict[str, Any]:
    """Return Esri Open Data layer configuration for the front-end.

    Provides layer metadata (id, label, endpoint, type) so the JavaScript
    map can request and render each layer via the /api/v1/geo endpoints.

    Returns
    -------
    dict with ``layers`` list
    """
    return {
        "provider": "Esri Open Data Hub / ArcGIS Living Atlas",
        "provider_url": "https://hub.arcgis.com/",
        "layers": [
            {
                "id": "esri_piers",
                "label": "Piers & Marinas",
                "api_endpoint": "/api/v1/geo/esri/piers",
                "type": "point",
                "icon": "pier",
                "description": "NOAA-curated marina and pier locations",
            },
            {
                "id": "esri_beaches",
                "label": "Monitored Beaches",
                "api_endpoint": "/api/v1/geo/esri/beaches",
                "type": "point",
                "icon": "beach",
                "description": "EPA BEACON 2.0 water-quality monitoring sites",
            },
            {
                "id": "esri_parks",
                "label": "Coastal Parks",
                "api_endpoint": "/api/v1/geo/esri/parks",
                "type": "polygon",
                "icon": "park",
                "description": "National Park Service coastal boundaries",
            },
        ],
    }

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _query_bbox(
    url: str,
    south: float,
    west: float,
    north: float,
    east: float,
    result_record_count: int = 50,
) -> list[dict[str, Any]]:
    """Execute an ArcGIS REST spatial query and return raw feature list."""
    params = {
        **_COMMON_PARAMS,
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "resultRecordCount": str(result_record_count),
    }

    try:
        resp = _HTTP.get(url, params=params, timeout=_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("features", [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("esri_open_data: query failed %s: %s", url, exc)

    return []

def _normalise_features(
    features: list[dict[str, Any]],
    default_type: str = "feature",
) -> list[dict[str, Any]]:
    """Convert raw ArcGIS GeoJSON features to simple dicts."""
    out: list[dict[str, Any]] = []
    for feat in features:
        geom = feat.get("geometry") or {}
        props = feat.get("properties") or {}

        geom_type = geom.get("type", "")
        coords = geom.get("coordinates")

        # Determine lat/lng from Point geometry
        lat, lng = None, None
        if geom_type == "Point" and coords:
            lng, lat = coords[0], coords[1]
        elif geom_type in ("Polygon", "MultiPolygon") and coords:
            # Use centroid approximation (average of outer ring vertices)
            ring = coords[0] if geom_type == "Polygon" else coords[0][0]
            if ring:
                lng = sum(c[0] for c in ring) / len(ring)
                lat = sum(c[1] for c in ring) / len(ring)

        name = (
            props.get("NAME")
            or props.get("name")
            or props.get("FACILITYNAME")
            or props.get("BeachName")
            or props.get("UNIT_NAME")
            or ""
        )
        feature_type = (
            props.get("TYPE")
            or props.get("type")
            or props.get("FACILITY_TYPE")
            or default_type
        )

        out.append(
            {
                "lat": lat,
                "lng": lng,
                "name": name,
                "type": feature_type,
                "geometry_type": geom_type,
                "props": {
                    k: v
                    for k, v in props.items()
                    if k
                    in (
                        "NAME",
                        "name",
                        "TYPE",
                        "STATE",
                        "COUNTY",
                        "FACILITYNAME",
                        "BeachName",
                        "UNIT_NAME",
                        "ACRES",
                        "STATUS",
                        "PHONE",
                        "URL",
                        "ADDRESS",
                    )
                },
            }
        )
    return out

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
