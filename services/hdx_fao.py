"""Humanitarian Data Exchange (HDX) and FAO GeoNetwork integration.

Two complementary open-data sources for fisheries and ocean biodiversity:

1. **Humanitarian Data Exchange (HDX)** (https://data.humdata.org/)
   OCHA's open data platform for humanitarian datasets.  Exposes a CKAN
   REST API at https://data.humdata.org/api/3/action/ that is freely
   accessible without authentication.  Relevant datasets include marine
   biodiversity, fisheries production, and ocean pollution records.

2. **FAO GeoNetwork** (https://www.fao.org/geonetwork/)
   FAO's spatial data infrastructure with fisheries, aquaculture, and
   ocean-health datasets.  Accessed via standard OGC WFS 2.0 endpoints
   and the GeoNetwork REST API — no API key required for public records.

Integration points
------------------
    search_hdx_datasets(query, rows=5) -> list[dict]
        Search HDX for fisheries / coastal / ocean datasets by keyword.

    fetch_fao_fisheries_zones(lat, lng) -> dict
        Fetch FAO major fishing area boundaries and return the zone
        containing the given coordinates.

    fetch_fao_species_info(common_name) -> Optional[dict]
        Look up ASFIS species metadata from FAO's fisheries database.

    get_hdx_fao_enrichment(lat, lng, species_names) -> dict
        Combined enrichment call that populates species regulation notices,
        FAO zone ID, and relevant HDX dataset links for the dashboard.

Caching
-------
HDX search results are cached for 24 hours.  FAO zone lookups are cached
for 7 days (zone boundaries do not change).  Species metadata is cached
indefinitely (static reference data).
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
_HTTP.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=4))

# ── In-process cache ──────────────────────────────────────────────────────────
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_HDX_TTL: int = 86400  # 24 hours — HDX catalog changes slowly
_CACHE_FAO_ZONE_TTL: int = 604800  # 7 days — zone boundaries are static
_CACHE_SPECIES_TTL: int = 604800  # 7 days — ASFIS species list is static
_CACHE_MAX: int = 512

# ── HDX CKAN API ──────────────────────────────────────────────────────────────
_HDX_BASE = "https://data.humdata.org/api/3/action"

# Pre-selected HDX dataset IDs of high relevance to coastal fisheries.
# Fetched lazily via search_hdx_datasets().
_HDX_FISHERIES_TAGS = [
    "fisheries",
    "marine",
    "ocean",
    "fish",
    "coastal",
    "aquaculture",
]

# ── FAO endpoints ─────────────────────────────────────────────────────────────
# FAO Fisheries & Aquaculture geographic data (OGC WFS 2.0, no key)
_FAO_WFS_BASE = "https://www.fao.org/fishery/geoserver/wfs"
# FAO ASFIS species REST service (no key)
_FAO_SPECIES_BASE = "https://www.fao.org/fishery/api/fao-species"

# ── FAO major fishing area look-up table ─────────────────────────────────────
# Maps FAO Major Fishing Area codes to human-readable names and lat/lng ranges.
# Source: FAO Fisheries Circular No. 826 Rev.3
_FAO_MAJOR_AREAS: list[dict[str, Any]] = [
    {
        "code": "21",
        "name": "Northwest Atlantic",
        "lat_range": (27, 78),
        "lng_range": (-100, -40),
    },
    {
        "code": "27",
        "name": "Northeast Atlantic",
        "lat_range": (36, 82),
        "lng_range": (-45, 70),
    },
    {
        "code": "31",
        "name": "Western Central Atlantic",
        "lat_range": (7, 30),
        "lng_range": (-98, -52),
    },
    {
        "code": "34",
        "name": "Eastern Central Atlantic",
        "lat_range": (-6, 36),
        "lng_range": (-45, 20),
    },
    {
        "code": "41",
        "name": "Southwest Atlantic",
        "lat_range": (-60, 5),
        "lng_range": (-65, -25),
    },
    {
        "code": "47",
        "name": "Southeast Atlantic",
        "lat_range": (-50, 0),
        "lng_range": (-20, 30),
    },
    {
        "code": "48",
        "name": "Atlantic, Antarctic",
        "lat_range": (-90, -45),
        "lng_range": (-180, 180),
    },
    {
        "code": "51",
        "name": "Western Indian Ocean",
        "lat_range": (-45, 30),
        "lng_range": (30, 80),
    },
    {
        "code": "57",
        "name": "Eastern Indian Ocean",
        "lat_range": (-55, 30),
        "lng_range": (80, 150),
    },
    {
        "code": "58",
        "name": "Indian Ocean, Antarctic",
        "lat_range": (-90, -45),
        "lng_range": (20, 150),
    },
    {
        "code": "61",
        "name": "Northwest Pacific",
        "lat_range": (0, 65),
        "lng_range": (100, 180),
    },
    {
        "code": "67",
        "name": "Northeast Pacific",
        "lat_range": (5, 75),
        "lng_range": (-180, -120),
    },
    {
        "code": "71",
        "name": "Western Central Pacific",
        "lat_range": (-25, 25),
        "lng_range": (100, 180),
    },
    {
        "code": "77",
        "name": "Eastern Central Pacific",
        "lat_range": (-5, 40),
        "lng_range": (-180, -75),
    },
    {
        "code": "81",
        "name": "Southwest Pacific",
        "lat_range": (-55, 0),
        "lng_range": (150, 180),
    },
    {
        "code": "87",
        "name": "Southeast Pacific",
        "lat_range": (-60, 5),
        "lng_range": (-120, -70),
    },
    {
        "code": "88",
        "name": "Pacific, Antarctic",
        "lat_range": (-90, -45),
        "lng_range": (-180, 180),
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def search_hdx_datasets(
    query: str = "fisheries coastal",
    rows: int = 5,
) -> list[dict[str, Any]]:
    """Search HDX for fisheries / coastal / ocean datasets.

    Uses the HDX CKAN ``package_search`` action which is fully public.
    Results include title, description, license, format, and download links.

    Parameters
    ----------
    query   Free-text search query (default: ``"fisheries coastal"``).
    rows    Maximum number of results to return (default 5).

    Returns
    -------
    List of dataset dicts: {id, title, notes, organization, license,
                            resources, tags, num_resources}
    """
    cache_key = f"hdx_search:{query}:{rows}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    params = {
        "q": query,
        "rows": str(rows),
        "sort": "score desc",
        "fq": "tags:fisheries OR tags:marine OR tags:ocean",
    }

    results: list[dict[str, Any]] = []
    failed = True

    try:
        resp = _HTTP.get(
            f"{_HDX_BASE}/package_search",
            params=params,
            timeout=(5, 20),
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        packages = data.get("result", {}).get("results", [])
        for pkg in packages:
            results.append(_parse_hdx_package(pkg))
        failed = False
    except Exception as exc:  # noqa: BLE001
        logger.warning("hdx_fao: HDX search failed: %s", exc)

    ttl = _CACHE_HDX_TTL if not failed else 300
    _cache_set(cache_key, results, ttl=ttl)
    return results

def fetch_fao_fisheries_zones(lat: float, lng: float) -> dict[str, Any]:
    """Identify the FAO Major Fishing Area and sub-area for a coordinate.

    Falls back to a pure-Python lookup table when the WFS request fails.

    Parameters
    ----------
    lat, lng    WGS-84 decimal degrees

    Returns
    -------
    dict: {area_code, area_name, sub_area, description, fao_url}
    """
    cache_key = f"fao_zone:{round(lat, 1)}:{round(lng, 1)}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    # Try live WFS query first
    result = _fetch_fao_zone_wfs(lat, lng)
    if not result:
        # Fallback: coordinate-range look-up table
        result = _lookup_fao_area(lat, lng)

    _cache_set(cache_key, result, ttl=_CACHE_FAO_ZONE_TTL)
    return result

def fetch_fao_species_info(common_name: str) -> Optional[dict[str, Any]]:
    """Look up ASFIS species metadata from FAO's fisheries species list.

    The FAO ASFIS (Aquatic Sciences and Fisheries Information System) list
    covers 12,700+ aquatic species used in fisheries statistics.

    Parameters
    ----------
    common_name     Common English name (case-insensitive).

    Returns
    -------
    dict: {scientific_name, family, order, asfis_code, fao_url}  or None.
    """
    name_key = common_name.lower().strip()
    cache_key = f"fao_species:{name_key}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    try:
        resp = _HTTP.get(
            f"{_FAO_SPECIES_BASE}/search",
            params={"q": common_name, "limit": "5"},
            timeout=(5, 15),
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            items = resp.json()
            if items:
                result = _parse_fao_species(items[0])
                _cache_set(cache_key, result, ttl=_CACHE_SPECIES_TTL)
                return result
    except Exception as exc:  # noqa: BLE001
        logger.debug("hdx_fao: FAO species lookup failed for %s: %s", common_name, exc)

    _cache_set(cache_key, None, ttl=_CACHE_SPECIES_TTL)
    return None

def get_hdx_fao_enrichment(
    lat: float,
    lng: float,
    species_names: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Combined enrichment call for the dashboard template.

    Fetches:
    - FAO fishing zone for the location
    - Relevant HDX datasets for the region's fisheries
    - FAO species info for up to 3 of the given species names

    Parameters
    ----------
    lat, lng        Location coordinates
    species_names   List of species common names from the forecast

    Returns
    -------
    dict: {fao_zone, hdx_datasets, species_enrichment, available}
    """
    fao_zone = fetch_fao_fisheries_zones(lat, lng)
    hdx_query = f"{fao_zone.get('area_name', 'marine fisheries')} fish"
    hdx_datasets = search_hdx_datasets(hdx_query, rows=3)

    species_enrichment: list[dict[str, Any]] = []
    if species_names:
        for name in (species_names or [])[:3]:
            info = fetch_fao_species_info(name)
            if info:
                info["common_name"] = name
                species_enrichment.append(info)

    return {
        "available": bool(fao_zone.get("area_code")),
        "fao_zone": fao_zone,
        "hdx_datasets": hdx_datasets,
        "species_enrichment": species_enrichment,
        "source": "FAO GeoNetwork + Humanitarian Data Exchange",
        "hdx_url": "https://data.humdata.org/",
        "fao_url": "https://www.fao.org/fishery/",
    }

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_fao_zone_wfs(lat: float, lng: float) -> Optional[dict[str, Any]]:
    """Query FAO GeoServer WFS for the fishing zone at a coordinate."""
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": "fifao:FAO_AREAS_CWP",
        "outputFormat": "application/json",
        "CQL_FILTER": f"CONTAINS(geom, POINT({lng} {lat}))",
        "maxFeatures": "5",
    }
    try:
        resp = _HTTP.get(_FAO_WFS_BASE, params=params, timeout=(5, 15))
        if resp.status_code == 200:
            data = resp.json()
            features = data.get("features", [])
            if features:
                props = features[0].get("properties", {})
                area_code = str(props.get("F_AREA", "")).strip()
                if area_code:
                    return {
                        "area_code": area_code,
                        "area_name": props.get("NAME_EN", ""),
                        "sub_area": props.get("F_SUBAREA", ""),
                        "description": props.get("OCEAN", ""),
                        "fao_url": (
                            f"https://www.fao.org/fishery/en/area/{area_code}/en"
                        ),
                    }
    except Exception as exc:  # noqa: BLE001
        logger.debug("hdx_fao: FAO WFS error: %s", exc)
    return None

def _lookup_fao_area(lat: float, lng: float) -> dict[str, Any]:
    """Coordinate-range fallback for FAO area identification."""
    # Normalise longitude to [-180, 180]
    lng = ((lng + 180) % 360) - 180

    for area in _FAO_MAJOR_AREAS:
        lat_min, lat_max = area["lat_range"]
        lng_min, lng_max = area["lng_range"]
        if lat_min <= lat <= lat_max and lng_min <= lng <= lng_max:
            code = area["code"]
            return {
                "area_code": code,
                "area_name": area["name"],
                "sub_area": "",
                "description": f"FAO Major Fishing Area {code}",
                "fao_url": f"https://www.fao.org/fishery/en/area/{code}/en",
                "method": "lookup_table",
            }

    return {
        "area_code": "",
        "area_name": "Unknown",
        "sub_area": "",
        "description": "Could not determine FAO fishing area",
        "fao_url": "https://www.fao.org/fishery/en/collection/cwp",
        "method": "lookup_table",
    }

def _parse_hdx_package(pkg: dict[str, Any]) -> dict[str, Any]:
    """Extract key fields from an HDX CKAN package record."""
    resources = []
    for res in pkg.get("resources", [])[:3]:
        resources.append(
            {
                "name": res.get("name", ""),
                "format": res.get("format", ""),
                "url": res.get("url", ""),
                "size": res.get("size"),
            }
        )

    tags = [t.get("name", "") for t in pkg.get("tags", [])]

    return {
        "id": pkg.get("id", ""),
        "title": pkg.get("title", ""),
        "notes": (pkg.get("notes", "") or "")[:300],
        "organization": (pkg.get("organization") or {}).get("title", ""),
        "license": pkg.get("license_title", ""),
        "num_resources": pkg.get("num_resources", 0),
        "resources": resources,
        "tags": tags[:8],
        "hdx_url": f"https://data.humdata.org/dataset/{pkg.get('name', '')}",
        "last_modified": pkg.get("last_modified", ""),
    }

def _parse_fao_species(item: dict[str, Any]) -> dict[str, Any]:
    """Parse FAO species API result into a simplified dict."""
    return {
        "scientific_name": item.get("nameScientific", ""),
        "family": item.get("family", ""),
        "order": item.get("order", ""),
        "asfis_code": item.get("alpha3Code", ""),
        "isscaap_group": item.get("isscaapGroup", ""),
        "fao_url": (
            f"https://www.fao.org/fishery/en/species/{item.get('asfisCode', '')}"
        ),
    }

def _cache_get(key: str) -> Optional[Any]:
    entry = _CACHE.get(key)
    if not entry:
        return None
    if time.time() - entry["ts"] < entry["ttl"]:
        return entry["data"]
    return None

def _cache_set(key: str, data: Any, ttl: int = _CACHE_HDX_TTL) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k]["ts"])
        _CACHE.pop(oldest, None)
    _CACHE[key] = {"ts": time.time(), "data": data, "ttl": ttl}
