"""Natural Earth public-domain GIS data integration.

Natural Earth (https://www.naturalearthdata.com/) is a public-domain global
GIS dataset produced by volunteer cartographers and released under a CC0
(public domain) license.  No registration or API key is required.

This module downloads and locally caches simplified coastline and ocean-
boundary GeoJSON from the Natural Earth GitHub CDN.  When ``geopandas`` is
installed it is used for filtering and reprojection; otherwise the module
falls back to raw JSON slicing.

Integration points
------------------
- ``get_coastlines_geojson(bbox)``
    Clip the global coastline to a bounding box and return GeoJSON — ready
    to be sent to the front-end for Leaflet overlay rendering.

- ``get_ocean_boundaries_geojson()``
    Return the ocean polygon layer (large-scale, low-res) for background
    ocean shading on the dashboard map.

Caching strategy
----------------
Files are downloaded once and persisted in ``data/natural_earth/`` to avoid
repeated network fetches.  An in-process metadata dict tracks download age so
the app can refresh stale files in the background without blocking requests.

Shapefile support (optional)
----------------------------
If ``geopandas`` ≥ 0.14 is installed the module also exposes
``load_ne_shapefile(name, resolution)`` which downloads, unzips, and loads any
Natural Earth vector layer as a GeoDataFrame.  Install via:
    pip install geopandas>=0.14
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
import zipfile
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

# ── HTTP session ──────────────────────────────────────────────────────────────
_HTTP: requests.Session = requests.Session()
_HTTP.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=4))

# ── File cache directory ──────────────────────────────────────────────────────
_BASE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "natural_earth"
)
_CACHE_TTL_DAYS = 30  # re-download Natural Earth data every 30 days

# ── Natural Earth GeoJSON CDN (pre-built, no conversion required) ──────────────
# These are maintained by the nvkelso/natural-earth-vector repository.
# 110m resolution (~1:110 000 000) keeps file sizes small (< 300 KB each).
_NE_GEOJSON_BASE = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
)

_NE_LAYERS: Dict[str, str] = {
    # Coastlines (lines)
    "coastline_110m": f"{_NE_GEOJSON_BASE}/ne_110m_coastline.geojson",
    # Land polygons (useful for land-masking ocean layers)
    "land_110m": f"{_NE_GEOJSON_BASE}/ne_110m_land.geojson",
    # Ocean polygons
    "ocean_110m": f"{_NE_GEOJSON_BASE}/ne_110m_ocean.geojson",
    # Country boundaries (lines)
    "countries_110m": f"{_NE_GEOJSON_BASE}/ne_110m_admin_0_boundary_lines_land.geojson",
    # US state boundaries
    "states_110m": f"{_NE_GEOJSON_BASE}/ne_110m_admin_1_states_provinces_lines.geojson",
    # 10m (higher resolution) coastline — larger download (~1.5 MB)
    "coastline_10m": f"{_NE_GEOJSON_BASE}/ne_10m_coastline.geojson",
}

# ── In-process metadata cache ─────────────────────────────────────────────────
_meta: Dict[str, Dict[str, Any]] = {}  # {layer_name: {path, loaded_at, ok}}
_meta_lock = threading.Lock()

# ── Optional geopandas import ─────────────────────────────────────────────────
try:
    import geopandas as gpd  # type: ignore

    _HAS_GEOPANDAS = True
    logger.debug("natural_earth: geopandas %s available", gpd.__version__)
except ImportError:
    gpd = None  # type: ignore
    _HAS_GEOPANDAS = False
    logger.debug("natural_earth: geopandas not installed; using pure-Python fallback")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def get_coastlines_geojson(
    bbox: Optional[tuple[float, float, float, float]] = None,
    resolution: str = "110m",
) -> Dict[str, Any]:
    """Return Natural Earth coastline GeoJSON, optionally clipped to a bbox.

    Parameters
    ----------
    bbox
        Optional ``(south, west, north, east)`` in WGS-84 decimal degrees.
        When provided the feature collection is filtered to features whose
        bounding box intersects the region of interest.  Uses geopandas
        spatial clip when available; otherwise uses a simple coordinate-range
        filter on feature bounding boxes.
    resolution
        ``"110m"`` (default, ~300 KB) or ``"10m"`` (~1.5 MB, much finer
        coastline detail for zoomed-in views).

    Returns
    -------
    GeoJSON FeatureCollection dict (may be empty on download failure).
    """
    layer_name = f"coastline_{resolution}"
    geojson = _load_layer(layer_name)
    if not geojson:
        return {"type": "FeatureCollection", "features": [], "source": "Natural Earth"}

    if bbox is not None:
        geojson = _clip_geojson(geojson, bbox)

    geojson["source"] = "Natural Earth"
    geojson["license"] = "Public Domain (CC0)"
    geojson["attribution"] = (
        "Made with Natural Earth. Free vector and raster map data @ "
        "naturalearthdata.com."
    )
    return geojson


def get_ocean_boundaries_geojson() -> Dict[str, Any]:
    """Return the 110m-resolution ocean polygon layer as GeoJSON.

    Suitable for a faint ocean-fill overlay on a transparent canvas layer.
    """
    geojson = _load_layer("ocean_110m")
    if not geojson:
        return {"type": "FeatureCollection", "features": [], "source": "Natural Earth"}
    geojson["source"] = "Natural Earth"
    geojson["license"] = "Public Domain (CC0)"
    return geojson


def get_state_boundaries_geojson() -> Dict[str, Any]:
    """Return US state / province boundary lines as GeoJSON."""
    geojson = _load_layer("states_110m")
    if not geojson:
        return {"type": "FeatureCollection", "features": [], "source": "Natural Earth"}
    geojson["source"] = "Natural Earth"
    geojson["license"] = "Public Domain (CC0)"
    return geojson


def load_ne_shapefile(name: str, resolution: str = "10m"):
    """Download and load a Natural Earth shapefile layer as a GeoDataFrame.

    Requires ``geopandas`` to be installed.  Downloads and unzips the
    shapefile from the Natural Earth CDN into ``data/natural_earth/shp/``.

    Parameters
    ----------
    name
        Natural Earth layer name without resolution prefix, e.g.
        ``"coastline"``, ``"admin_0_countries"``.
    resolution
        ``"10m"``, ``"50m"``, or ``"110m"``.

    Returns
    -------
    GeoDataFrame or None if geopandas is not installed / download fails.
    """
    if not _HAS_GEOPANDAS:
        logger.warning(
            "natural_earth: geopandas not installed; cannot load shapefile %s", name
        )
        return None

    layer_id = f"{resolution}_{name}"
    shp_dir = os.path.join(_BASE_DIR, "shp", layer_id)

    # Use cached version if fresh enough
    if os.path.isdir(shp_dir):
        shp_files = [f for f in os.listdir(shp_dir) if f.endswith(".shp")]
        if shp_files:
            mtime = os.path.getmtime(os.path.join(shp_dir, shp_files[0]))
            age_days = (time.time() - mtime) / 86400
            if age_days < _CACHE_TTL_DAYS:
                shp_path = os.path.join(shp_dir, shp_files[0])
                logger.debug("natural_earth: loading cached shapefile %s", shp_path)
                return gpd.read_file(shp_path)

    # Download from Natural Earth CDN
    zip_url = (
        f"https://naturalearth.s3.amazonaws.com/{resolution}_physical/"
        f"ne_{resolution}_{name}.zip"
    )
    logger.info("natural_earth: downloading shapefile %s", zip_url)
    try:
        resp = _HTTP.get(zip_url, timeout=(10, 60))
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("natural_earth: download failed for %s: %s", name, exc)
        return None

    os.makedirs(shp_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(shp_dir)
    except zipfile.BadZipFile:
        logger.error("natural_earth: bad zip for %s", name)
        return None

    shp_files = [f for f in os.listdir(shp_dir) if f.endswith(".shp")]
    if not shp_files:
        return None
    return gpd.read_file(os.path.join(shp_dir, shp_files[0]))


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _layer_path(layer_name: str) -> str:
    return os.path.join(_BASE_DIR, f"{layer_name}.geojson")


def _load_layer(layer_name: str) -> Optional[Dict[str, Any]]:
    """Load a Natural Earth GeoJSON layer from disk, downloading if needed."""
    path = _layer_path(layer_name)

    # Check disk cache
    if os.path.exists(path):
        age_days = (time.time() - os.path.getmtime(path)) / 86400
        if age_days < _CACHE_TTL_DAYS:
            return _read_geojson(path)
        # Stale — refresh in background, return stale data for now
        threading.Thread(
            target=_download_layer, args=(layer_name,), daemon=True
        ).start()
        return _read_geojson(path)

    # Not cached — download synchronously (first time only)
    return _download_layer(layer_name)


def _download_layer(layer_name: str) -> Optional[Dict[str, Any]]:
    """Download a Natural Earth GeoJSON layer and persist it to disk."""
    url = _NE_LAYERS.get(layer_name)
    if url is None:
        logger.warning("natural_earth: unknown layer %s", layer_name)
        return None

    logger.info("natural_earth: downloading %s from %s", layer_name, url)
    try:
        resp = _HTTP.get(url, timeout=(10, 60))
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as exc:
        logger.error("natural_earth: download failed for %s: %s", layer_name, exc)
        return None

    path = _layer_path(layer_name)
    os.makedirs(_BASE_DIR, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.chmod(path, 0o600)
        logger.info(
            "natural_earth: saved %s (%d features)",
            layer_name,
            len(data.get("features", [])),
        )
    except OSError as exc:
        logger.error("natural_earth: could not save %s: %s", layer_name, exc)

    return data


def _read_geojson(path: str) -> Optional[Dict[str, Any]]:
    """Read a GeoJSON file from disk, returning None on parse error."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("natural_earth: could not read %s: %s", path, exc)
        return None


def _clip_geojson(
    geojson: Dict[str, Any],
    bbox: tuple[float, float, float, float],
) -> Dict[str, Any]:
    """Return a new FeatureCollection with only features intersecting bbox.

    Parameters
    ----------
    geojson  source FeatureCollection
    bbox     (south, west, north, east)

    Uses geopandas ``cx`` spatial indexer when available for speed.
    Falls back to a coordinate-range scan on geometry bounding boxes.
    """
    south, west, north, east = bbox

    if _HAS_GEOPANDAS:
        try:
            gdf = gpd.GeoDataFrame.from_features(geojson["features"])
            gdf = gdf.set_crs("EPSG:4326", allow_override=True)
            clipped = gdf.cx[west:east, south:north]  # type: ignore[misc]
            return json.loads(clipped.to_json())
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "natural_earth: geopandas clip failed, using fallback: %s", exc
            )

    # Pure-Python bbox filter: include feature if any coordinate is within bbox
    features = geojson.get("features", [])
    filtered = [
        f for f in features if _feature_intersects_bbox(f, south, west, north, east)
    ]
    return {"type": "FeatureCollection", "features": filtered}


def _feature_intersects_bbox(
    feature: Dict[str, Any],
    south: float,
    west: float,
    north: float,
    east: float,
) -> bool:
    """Check whether any coordinate of a GeoJSON feature falls inside bbox."""
    geom = feature.get("geometry") or {}
    coords = _flatten_coords(geom.get("coordinates", []))
    for lng, lat in coords:
        if south <= lat <= north and west <= lng <= east:
            return True
    return False


def _flatten_coords(coords: Any) -> List[tuple[float, float]]:
    """Recursively flatten nested coordinate arrays to (lng, lat) pairs."""
    if not coords:
        return []
    if isinstance(coords[0], (int, float)):
        return [(coords[0], coords[1])]
    out: List[tuple[float, float]] = []
    for item in coords:
        out.extend(_flatten_coords(item))
    return out
