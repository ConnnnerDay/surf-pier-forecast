"""NASA Worldview / GIBS satellite imagery layer configuration.

NASA's Global Imagery Browse Services (GIBS) provides near-real-time and
historical satellite imagery via standard OGC WMTS and TMS endpoints.
All layers are freely accessible with no API key or registration.

Reference: https://nasa-gibs.github.io/gibs-api-docs/

This module exposes:
- Tile URL templates for Leaflet (TMS/XYZ format via GIBS's EPSG:3857 endpoint)
- Layer metadata (temporal coverage, update frequency, resolution)
- A helper to build dated tile URLs for GIBS layers that use a date path segment

Included layers
---------------
VIIRS_SNPP_TrueColor_375m       Near-real-time true-colour (0.375 km, daily)
MODIS_Terra_CorrectedReflectance_TrueColor   Terra MODIS true-colour (250 m)
GHRSST_L4_MUR_Sea_Surface_Temperature        Multi-scale SST (1 km, daily)
MODIS_Terra_Chlorophyll_A                    Chlorophyll concentration
VIIRS_NOAA20_CorrectedReflectance_TrueColor  NOAA-20 VIIRS true-colour
Coastlines_15m                               SRTM coastline overlay

Integration points
------------------
    get_gibs_layers() -> dict
        Returns all available GIBS layer descriptors for the front-end.

    get_sst_tile_config(date=None) -> dict
        Returns the Sea Surface Temperature tile config, optionally for a
        specific date (ISO format, e.g. ``"2024-01-15"``).

    get_imagery_tile_config(layer_id, date=None) -> dict
        Generic tile config for any GIBS layer by its canonical ID.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── GIBS WMTS endpoints ───────────────────────────────────────────────────────
# GIBS supports two projection sets.  For Leaflet (Web Mercator) we use the
# EPSG:3857 endpoint.  The TMS path pattern supports standard XYZ tile addressing.

_GIBS_WMTS_BASE = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
_GIBS_WMS_BASE = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"

# GIBS attribution — required by NASA usage guidelines
_GIBS_ATTRIB = (
    "Imagery provided by services from the Global Imagery Browse Services (GIBS), "
    "operated by the NASA/GSFC/Earth Science Data and Information System (ESDIS) "
    "with funding provided by NASA/HQ."
)


def _gibs_url(layer_id: str, date: Optional[str] = None) -> str:
    """Build a Leaflet-compatible XYZ tile URL for a GIBS layer.

    GIBS WMTS tile URLs follow the pattern:
        {base}/{layer}/{date}/{tilematrixset}/{z}/{y}/{x}.{format}

    For layers without a time dimension, ``{date}`` is omitted.
    Leaflet's {z}/{x}/{y} placeholders are inserted literally.
    """
    layer_info = _LAYERS.get(layer_id, {})
    fmt = layer_info.get("format", "jpg")
    matrix_set = layer_info.get("matrix_set", "GoogleMapsCompatible_Level9")
    has_time = layer_info.get("has_time", True)

    if has_time:
        d = date or _today()
        return (
            f"{_GIBS_WMTS_BASE}/{layer_id}/{d}/{matrix_set}"
            "/{{z}}/{{y}}/{{x}}.{fmt}"
        ).format(fmt=fmt)
    else:
        return (
            f"{_GIBS_WMTS_BASE}/{layer_id}/default/{matrix_set}"
            "/{{z}}/{{y}}/{{x}}.{fmt}"
        ).format(fmt=fmt)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


# ── Layer registry ─────────────────────────────────────────────────────────────
# Each entry maps a canonical layer_id to Leaflet / GIBS metadata.
# max_zoom is set conservatively — GIBS tiles degrade gracefully beyond native res.

_LAYERS: Dict[str, Dict[str, Any]] = {
    "VIIRS_SNPP_TrueColor_375m": {
        "label": "True Color (VIIRS, 375 m)",
        "description": "Near-real-time true-color composite from Suomi NPP VIIRS, updated daily.",
        "category": "true_color",
        "format": "jpg",
        "matrix_set": "GoogleMapsCompatible_Level9",
        "max_zoom": 9,
        "has_time": True,
        "update_freq": "daily",
        "latency_hours": 3,
        "native_res_km": 0.375,
        "temporal_extent": ("2012-01-20", None),
    },
    "MODIS_Terra_CorrectedReflectance_TrueColor": {
        "label": "True Color (MODIS Terra, 250 m)",
        "description": "Daily true-color composite from Terra MODIS band combination.",
        "category": "true_color",
        "format": "jpg",
        "matrix_set": "GoogleMapsCompatible_Level9",
        "max_zoom": 9,
        "has_time": True,
        "update_freq": "daily",
        "latency_hours": 4,
        "native_res_km": 0.25,
        "temporal_extent": ("2000-02-24", None),
    },
    "MODIS_Aqua_CorrectedReflectance_TrueColor": {
        "label": "True Color (MODIS Aqua, 250 m)",
        "description": "Daily true-color composite from Aqua MODIS (afternoon orbit).",
        "category": "true_color",
        "format": "jpg",
        "matrix_set": "GoogleMapsCompatible_Level9",
        "max_zoom": 9,
        "has_time": True,
        "update_freq": "daily",
        "latency_hours": 6,
        "native_res_km": 0.25,
        "temporal_extent": ("2002-07-04", None),
    },
    "GHRSST_L4_MUR_Sea_Surface_Temperature": {
        "label": "Sea Surface Temperature (MUR, 1 km)",
        "description": (
            "GHRSST Level-4 MUR sea surface temperature analysis. "
            "A blended product from multiple satellite sensors. Updated daily."
        ),
        "category": "sst",
        "format": "png",
        "matrix_set": "GoogleMapsCompatible_Level7",
        "max_zoom": 7,
        "has_time": True,
        "update_freq": "daily",
        "latency_hours": 48,
        "native_res_km": 1.0,
        "temporal_extent": ("2002-06-01", None),
        "colorbar": "sst",
        "units": "°C",
        "opacity": 0.75,
    },
    "MODIS_Terra_Chlorophyll_A": {
        "label": "Chlorophyll A (MODIS Terra, 4 km)",
        "description": (
            "Ocean chlorophyll-A concentration — a proxy for phytoplankton "
            "biomass and nutrient-rich upwelling zones that attract baitfish."
        ),
        "category": "ocean_color",
        "format": "png",
        "matrix_set": "GoogleMapsCompatible_Level7",
        "max_zoom": 7,
        "has_time": True,
        "update_freq": "8-day",
        "latency_hours": 48,
        "native_res_km": 4.0,
        "temporal_extent": ("2000-02-24", None),
        "units": "mg/m³",
        "opacity": 0.7,
        "fishing_relevance": (
            "High chlorophyll areas indicate productive feeding zones — "
            "look for baitfish concentrations near these patches."
        ),
    },
    "VIIRS_NOAA20_CorrectedReflectance_TrueColor": {
        "label": "True Color (NOAA-20 VIIRS, 375 m)",
        "description": "Near-real-time true-color from NOAA-20 (JPSS-1) VIIRS sensor.",
        "category": "true_color",
        "format": "jpg",
        "matrix_set": "GoogleMapsCompatible_Level9",
        "max_zoom": 9,
        "has_time": True,
        "update_freq": "daily",
        "latency_hours": 3,
        "native_res_km": 0.375,
        "temporal_extent": ("2018-01-01", None),
    },
    "Coastlines_15m": {
        "label": "Coastlines (15 m)",
        "description": "SRTM-derived coastline vector rendered as a tile overlay.",
        "category": "reference",
        "format": "png",
        "matrix_set": "GoogleMapsCompatible_Level9",
        "max_zoom": 9,
        "has_time": False,
        "update_freq": "static",
        "opacity": 1.0,
    },
    "MODIS_Terra_Sea_Ice": {
        "label": "Sea Ice (MODIS Terra)",
        "description": "Daily sea-ice extent for polar regions.",
        "category": "sea_ice",
        "format": "png",
        "matrix_set": "GoogleMapsCompatible_Level7",
        "max_zoom": 7,
        "has_time": True,
        "update_freq": "daily",
        "latency_hours": 24,
        "native_res_km": 0.5,
        "temporal_extent": ("2000-02-24", None),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def get_gibs_layers(date: Optional[str] = None) -> Dict[str, Any]:
    """Return all GIBS layer descriptors with Leaflet-ready tile URLs.

    Parameters
    ----------
    date
        ISO date string (``"YYYY-MM-DD"``) to use for time-varying layers.
        Defaults to yesterday (GIBS near-real-time latency is ~3-48 hours).

    Returns
    -------
    dict with keys:
        layers      list of layer dicts (id, label, url, options, metadata)
        date        the date used for time-varying layers
        attribution string required by NASA usage guidelines
    """
    effective_date = date or _yesterday()

    layers: List[Dict[str, Any]] = []
    for layer_id, meta in _LAYERS.items():
        url = _gibs_url(layer_id, effective_date)
        layers.append(
            {
                "id": layer_id,
                "label": meta["label"],
                "description": meta.get("description", ""),
                "category": meta.get("category", "other"),
                "url": url,
                "options": {
                    "attribution": _GIBS_ATTRIB,
                    "maxZoom": meta.get("max_zoom", 9),
                    "opacity": meta.get("opacity", 1.0),
                    "tileSize": 256,
                },
                "update_freq": meta.get("update_freq", "unknown"),
                "native_res_km": meta.get("native_res_km"),
                "units": meta.get("units"),
                "fishing_relevance": meta.get("fishing_relevance"),
                "has_time": meta.get("has_time", True),
            }
        )

    return {
        "layers": layers,
        "date": effective_date,
        "attribution": _GIBS_ATTRIB,
        "source": "NASA GIBS",
        "source_url": "https://nasa-gibs.github.io/gibs-api-docs/",
        "license": "NASA Open Data Policy — public domain",
    }


def get_sst_tile_config(date: Optional[str] = None) -> Dict[str, Any]:
    """Return Sea Surface Temperature layer config for Leaflet.

    Parameters
    ----------
    date    ISO date string; defaults to yesterday.

    Returns
    -------
    dict suitable for passing directly to Leaflet's ``L.tileLayer(url, options)``.
    """
    return get_imagery_tile_config("GHRSST_L4_MUR_Sea_Surface_Temperature", date)


def get_imagery_tile_config(
    layer_id: str, date: Optional[str] = None
) -> Dict[str, Any]:
    """Return Leaflet tile config for any GIBS layer by ID.

    Parameters
    ----------
    layer_id    Canonical GIBS layer identifier (key in ``_LAYERS``).
    date        ISO date string; defaults to yesterday.

    Returns
    -------
    dict: {url, options, metadata}  or empty dict if layer_id is unknown.
    """
    if layer_id not in _LAYERS:
        logger.warning("nasa_worldview: unknown layer_id %s", layer_id)
        return {}

    effective_date = date or _yesterday()
    meta = _LAYERS[layer_id]
    url = _gibs_url(layer_id, effective_date)

    return {
        "url": url,
        "options": {
            "attribution": _GIBS_ATTRIB,
            "maxZoom": meta.get("max_zoom", 9),
            "opacity": meta.get("opacity", 1.0),
            "tileSize": 256,
        },
        "metadata": {
            "id": layer_id,
            "label": meta["label"],
            "date": effective_date,
            "description": meta.get("description", ""),
            "category": meta.get("category", "other"),
            "update_freq": meta.get("update_freq"),
            "native_res_km": meta.get("native_res_km"),
            "units": meta.get("units"),
            "fishing_relevance": meta.get("fishing_relevance"),
        },
    }


def get_default_date_for_layer(layer_id: str) -> str:
    """Return the most appropriate date to use for a given GIBS layer.

    Near-real-time layers use yesterday (accounting for latency).
    Static / no-time layers return today's date (it is ignored by the URL
    builder for those layers).
    """
    meta = _LAYERS.get(layer_id, {})
    latency_hours = meta.get("latency_hours", 24)
    delta_days = max(1, (latency_hours + 12) // 24)
    return (datetime.now(timezone.utc) - timedelta(days=delta_days)).strftime(
        "%Y-%m-%d"
    )
