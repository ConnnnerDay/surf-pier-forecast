"""Identify fish-holding structures within a map bounding box.

Data sources
------------
- OpenStreetMap via Overpass API  (piers, reefs, channels, beaches, …)
- NOAA ENC ArcGIS REST service    (wrecks, obstructions, rocks)

Public API
----------
    find_fish_structures(south, west, north, east, types=None)
        → list[{lat, lng, type, name, tip}]

Each returned dict has:
    lat   float   WGS-84 latitude
    lng   float   WGS-84 longitude
    type  str     one of VALID_TYPES
    name  str     feature name (may be empty)
    tip   str     habitat/angling tip (may be empty)
"""

from __future__ import annotations

import json as _json
import logging
import time as _time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

_TIMEOUT_OVERPASS: tuple[float, float] = (6, 15)
_TIMEOUT_NOAA_ENC: tuple[float, float] = (4, 12)

logger = logging.getLogger(__name__)

# ── Persistent HTTP session with connection pooling ────────────────────────────
# A module-level Session re-uses TCP connections and SSL sessions across calls
# to both the Overpass API and the NOAA ENC ArcGIS service.  Without it, every
# fetch_structures() call pays DNS lookup + TCP handshake + TLS negotiation for
# each of the ~3-5 outgoing requests (Overpass + up to 3 NOAA layers).
# pool_connections=4 covers the Overpass mirrors + NOAA host pair.
_HTTP: requests.Session = requests.Session()
_HTTP.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=8))
_HTTP.mount("http://", HTTPAdapter(pool_connections=4, pool_maxsize=8))

# ── Result cache  ─────────────────────────────────────────────────────────────
# Keyed on (south2dp, west2dp, north2dp, east2dp, frozenset(active_types)).
# Entries expire after _CACHE_TTL seconds; the dict is capped at _CACHE_MAX
# entries — oldest insertion is dropped first once the cap is hit.

_CACHE: dict[
    tuple, dict[str, Any]
] = {}  # {key: {"ts": float, "data": list, "failed": bool}}
_CACHE_TTL: int = 1800  # 30 minutes — piers and reefs don't move
_CACHE_TTL_FAILED: int = 90  # 90 seconds — retry failed bboxes less aggressively
_CACHE_MAX: int = 256  # max bbox+types combinations kept in memory

def _cache_key(
    south: float, west: float, north: float, east: float, types: set[str]
) -> tuple:
    """Stable, hashable cache key rounded to 2 decimal places (~1 km grid)."""
    return (
        round(south, 2),
        round(west, 2),
        round(north, 2),
        round(east, 2),
        frozenset(types),
    )

_CACHE_EVICT_INTERVAL: float = 60.0  # seconds between full TTL scans
_cache_last_evict: float = 0.0

def _cache_evict() -> None:
    """Purge entries older than TTL; if still over cap, drop oldest by insertion.

    Full TTL scan is throttled to once per _CACHE_EVICT_INTERVAL so that
    high-traffic periods don't run O(n) list comprehension on every miss.
    Uses .pop() and try/except to handle concurrent deletes safely.
    """
    global _cache_last_evict
    now = _time.time()
    if now - _cache_last_evict >= _CACHE_EVICT_INTERVAL:
        _cache_last_evict = now
        stale = [
            k
            for k, v in list(_CACHE.items())
            if now - v["ts"] >= (_CACHE_TTL_FAILED if v.get("failed") else _CACHE_TTL)
        ]
        for k in stale:
            _CACHE.pop(k, None)  # safe if another thread already removed it
    while len(_CACHE) >= _CACHE_MAX:
        try:
            del _CACHE[next(iter(_CACHE))]
        except (KeyError, StopIteration):
            break  # another thread cleared it first

def cache_clear() -> None:
    """Remove all cached results.  Intended for tests and cache-invalidation hooks."""
    _CACHE.clear()

# ── Habitat types rendered as filled polygon overlays ─────────────────────────
# These are area features (wetlands, beaches, …) whose OSM ways carry closed
# ring geometry.  When a way element in this set has ≥3 geometry points the
# backend includes a ``geometry`` list so the client can draw a filled outline
# instead of placing a single-point marker.
POLYGON_HABITAT_TYPES: frozenset[str] = frozenset(
    {
        "saltmarsh",
        "mangrove",
        "tidal_flat",
        "grass_flat",
        "beach",
        "oyster_reef",
        "inlet",
        "kelp",
    }
)

# ── Recognised structure types  ───────────────────────────────────────────────
# Matches SPOT_TYPES in static/js/fishing_map.js
VALID_TYPES: frozenset[str] = frozenset(
    {
        "oyster_reef",
        "reef",
        "grass_flat",
        "saltmarsh",
        "mangrove",
        "tidal_flat",
        "kelp",
        "shoal",
        "pier",
        "jetty",
        "bridge",
        "marina",
        "inlet",
        "point",
        "beach",
        "wreck",
        "buoy",
        "fishing_shop",
        "fishing",
        "boat_ramp",
        "dive_site",
        "seawall",
    }
)

# ── Fishing context tips  ──────────────────────────────────────────────────────
# Mirrors STRUCTURE_TIPS in static/js/fishing_map.js
STRUCTURE_TIPS: dict[str, str] = {
    "pier": (
        "Work the pilings and shadow lines — baitfish stack against current "
        "breaks at dawn and dusk."
    ),
    "jetty": (
        "Fish the tip on falling tides; predators ambush bait funneled through "
        "the gap. Work the rocks for sheepshead and black drum."
    ),
    "bridge": (
        "Bridge pilings concentrate bait and create current seams. Night "
        "fishing under bridge lights is especially productive."
    ),
    "reef": (
        "Hard bottom holds structure species — grouper, snapper, sheepshead. "
        "Work the upcurrent edge."
    ),
    "oyster_reef": (
        "Oyster reefs are magnets. Shrimp and crabs hide in the shell; redfish, "
        "flounder, and drum patrol the edges on every tide change."
    ),
    "wreck": (
        "Wrecks act as artificial reefs — they concentrate ambush predators. "
        "Cast up-current and let bait drift past the structure."
    ),
    "inlet": (
        "Tidal inlets and channels funnel bait on every tide change — one of the "
        "most consistent year-round spots. Fish the current seam at the channel edge."
    ),
    "marina": (
        "Marinas concentrate bait around dock pilings and channel edges. Work the "
        "shadow lines early morning and at last light."
    ),
    "shoal": (
        "Work the drop from shallow to deep — fish hold on the seam waiting for "
        "bait washing off the flat."
    ),
    "point": (
        "Current eddies form on the downcurrent side of headlands and points — "
        "predators stack here to ambush bait swept past the tip."
    ),
    "beach": (
        "Work the gutters, rip cuts, and troughs running parallel to shore. Cast "
        "beyond the first sandbar — pompano, drum, and stripers feed along the break."
    ),
    "grass_flat": (
        "Seagrass holds shrimp and baitfish. Redfish, speckled trout, and flounder "
        "push shallow on rising tides and drop to the flat edges at low."
    ),
    "tidal_flat": (
        "Fish move onto tidal flats as the tide floods, chasing crabs and shrimp "
        "into the shallows. Work the edges as the water begins falling."
    ),
    "saltmarsh": (
        "Marsh creek mouths and grass edges are ambush points — redfish and snook "
        "use incoming current to pick off bait washing out of the marsh."
    ),
    "mangrove": (
        "Work the mangrove root edges on rising tides; snook, redfish, and tarpon "
        "ambush prey along the shadow line."
    ),
    "buoy": (
        "Channel markers and buoys identify edges where deep water meets shallow "
        "structure — fish the up-current side."
    ),
    "fishing": "Local fishing access point.",
    "fishing_shop": "Local bait & tackle — stop in for real-time bite reports.",
    "kelp": (
        "Kelp forests hold some of the richest habitat on the Pacific coast — "
        "rockfish, lingcod, and kelp bass hold along the canopy edge and at the "
        "base of the fronds. Work the outer edge and depth transitions."
    ),
    "boat_ramp": (
        "Boat ramps and launch sites draw early activity. Cast along the ramp "
        "edges and nearby channel drops — baitfish concentrate where the bottom "
        "changes and fish ambush from the shadows."
    ),
    "dive_site": (
        "Dive sites flag clear water over structure — the same reefs, ledges, "
        "and wrecks divers explore hold trophy fish. Work the upcurrent edge "
        "and depth transitions."
    ),
    "seawall": (
        "Seawalls create hard current edges and shadow lines — stripers, bluefish, "
        "snook, and tarpon patrol the base of the wall on tide changes. "
        "Night fishing near lit walls is consistently productive."
    ),
}

# ── Proximity deduplication thresholds (decimal degrees) ─────────────────────
# ~0.001° ≈ 111 m.  Polygon habitat types use a sentinel value of 0 to
# *skip* centroid-proximity dedup entirely — adjacent wetland/beach patches
# are distinct features and must not be collapsed just because their
# computed centroids happen to be close.  Only name-based dedup applies to
# those types.  Tight thresholds remain for point structures.
_PROX: dict[str, float] = {
    "inlet": 0.005,  # ~550 m — tidal channel nodes cluster heavily
    "marina": 0.004,  # ~440 m
    "wreck": 0.003,  # ~330 m — unnamed OSM + NOAA ENC wrecks often overlap
    "shoal": 0.003,  # ~330 m — OSM/NOAA obstruction nodes cluster at a point
    "beach": 0.0,  # polygon area — skip centroid proximity dedup
    "grass_flat": 0.0,  # polygon area
    "saltmarsh": 0.0,  # polygon area
    "tidal_flat": 0.0,  # polygon area
    "mangrove": 0.0,  # polygon area
    "oyster_reef": 0.0,  # polygon area
    "kelp": 0.0,  # polygon area
    "seawall": 0.003,  # ~330 m — seawall/revetment centroid markers can cluster
    "_default": 0.002,  # ~220 m — piers, jetties, buoys, etc.
}

# ── Polygon coordinate decimation ─────────────────────────────────────────────
# Keep at most this many vertices per polygon ring.  Leaflet renders at most
# ~150 screen-space segments at typical zoom levels; the remainder are clipped
# or simplified in the browser anyway.  Capping here shrinks the JSON payload
# for dense coastal features that OSM often encodes with 300–1 000 nodes.
_MAX_POLYGON_COORDS: int = 200

def _decimate_ring(coords: list[list[float]]) -> list[list[float]]:
    """Thin a coordinate ring to at most ``_MAX_POLYGON_COORDS`` points.

    Uses uniform Nth-point selection so the ring shape is preserved evenly.
    The first and last points are always kept so closed rings stay closed.
    """
    n = len(coords)
    if n <= _MAX_POLYGON_COORDS:
        return coords
    # Pick evenly-spaced indices; always include 0 and n-1
    step = (n - 1) / (_MAX_POLYGON_COORDS - 1)
    indices = {0, n - 1}
    for i in range(1, _MAX_POLYGON_COORDS - 1):
        indices.add(round(i * step))
    return [coords[i] for i in sorted(indices)]

# ── Overpass API endpoints (primary + mirror fallback) ────────────────────────
_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# ── NOAA ENC ArcGIS REST service  ─────────────────────────────────────────────
_NOAA_ENC_BASE = (
    "https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/ENCOnline/MapServer"
)
_NOAA_LAYER_WRECKS = 2
_NOAA_LAYER_OBSTRUCTIONS = 3
_NOAA_LAYER_ROCKS = 4

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_overpass_query(bbox: str, types: set[str]) -> str:
    """Return an Overpass QL query string for the requested types.

    ``bbox`` must be the Overpass format string ``"south,west,north,east"``.
    Returns an empty string when no tags match the requested types so the
    caller can skip the network request.

    The query is split into two named sets so that each set can use the most
    efficient output mode:

    * ``.h`` (habitat area types) → ``out geom;``  — full ring coordinates
      are needed so the client can render filled polygon overlays.
    * ``.s`` (structure point types) → ``out center;``  — centroid only;
      these are rendered as small icon markers so full geometry would just
      inflate the payload for no benefit.
    """
    habitat: list[str] = []  # will use `out geom;`
    struct: list[str] = []  # will use `out center;`

    # ── Habitat area types (polygon rendering) ──────────────────────────────
    if "grass_flat" in types:
        habitat += [
            f'way["natural"="wetland"]["wetland"="seagrass"]({bbox});',
            f'node["natural"="wetland"]["wetland"="seagrass"]({bbox});',
        ]
    if "saltmarsh" in types:
        habitat += [f'way["natural"="wetland"]["wetland"="saltmarsh"]({bbox});']
    if "mangrove" in types:
        habitat += [f'way["natural"="wetland"]["wetland"="mangrove"]({bbox});']
    if "tidal_flat" in types:
        habitat += [
            f'way["natural"="wetland"]["wetland"="tidalflat"]({bbox});',
            f'way["natural"="mud"]({bbox});',
        ]
    if "beach" in types:
        habitat += [
            f'way["natural"="beach"]({bbox});',
            f'node["natural"="beach"]({bbox});',
            f'way["natural"="sand"]["access"!="private"]({bbox});',
        ]
    if "oyster_reef" in types:
        habitat += [
            f'node["landuse"="aquaculture"]["produce"="oyster"]({bbox});',
            f'way["landuse"="aquaculture"]["produce"="oyster"]({bbox});',
            f'way["landuse"="aquaculture"]["product"="oysters"]({bbox});',
        ]
    if "kelp" in types:
        habitat += [
            f'way["natural"="wetland"]["wetland"="kelp"]({bbox});',
            f'way["natural"="kelp"]({bbox});',
        ]
    if "inlet" in types:
        # Waterways (linestrings) and bay polygons both go into habitat so
        # the client can decide: closed ring → filled polygon, open → polyline.
        habitat += [
            f'way["waterway"="tidal_channel"]({bbox});',
            f'way["waterway"="river"]({bbox});',
            f'way["waterway"="canal"]({bbox});',
            f'node["waterway"="stream"]({bbox});',
            f'way["waterway"="stream"]({bbox});',
            f'node["harbour"="yes"]({bbox});',
            f'way["harbour"="yes"]({bbox});',
            f'node["natural"="bay"]({bbox});',
            f'way["natural"="bay"]({bbox});',
        ]

    # ── Structure point / linear types (centroid only) ───────────────────────
    if "reef" in types:
        struct += [
            f'node["natural"="reef"]({bbox});',
            f'way["natural"="reef"]({bbox});',
            f'node["seamark:type"="artificial_reef"]({bbox});',
            f'way["seamark:type"="artificial_reef"]({bbox});',
            f'node["landuse"="artificial_reef"]({bbox});',
            f'way["landuse"="artificial_reef"]({bbox});',
        ]
    if "wreck" in types:
        struct += [
            f'node["historic"="wreck"]({bbox});',
            f'way["historic"="wreck"]({bbox});',
            f'node["seamark:type"="wreck"]({bbox});',
            f'way["seamark:type"="wreck"]({bbox});',
        ]
    if "shoal" in types:
        struct += [
            f'node["natural"="shoal"]({bbox});',
            f'way["natural"="shoal"]({bbox});',
            f'node["natural"="sandbank"]({bbox});',
            f'way["natural"="sandbank"]({bbox});',
            f'node["natural"="rock"]({bbox});',
            f'node["seamark:type"="rock_awash"]({bbox});',
            f'node["seamark:type"="underwater_rock"]({bbox});',
            f'node["seamark:type"="rock_submerged"]({bbox});',
            f'node["seamark:type"="obstruction"]({bbox});',
        ]
    if "pier" in types:
        # Only fetch publicly accessible piers — private/restricted docks are excluded
        struct += [
            f'node["man_made"="pier"]["access"!="private"]["access"!="no"]({bbox});',
            f'way["man_made"="pier"]["access"!="private"]["access"!="no"]({bbox});',
            f'node["leisure"="pier"]["access"!="private"]["access"!="no"]({bbox});',
            f'way["leisure"="pier"]["access"!="private"]["access"!="no"]({bbox});',
        ]
    if "jetty" in types:
        struct += [
            f'node["man_made"="jetty"]({bbox});',
            f'way["man_made"="jetty"]({bbox});',
            f'node["man_made"="groyne"]({bbox});',
            f'way["man_made"="groyne"]({bbox});',
            f'node["man_made"="breakwater"]({bbox});',
            f'way["man_made"="breakwater"]({bbox});',
            f'node["waterway"="weir"]({bbox});',
            f'way["waterway"="weir"]({bbox});',
            f'node["waterway"="dam"]({bbox});',
            f'way["waterway"="dam"]({bbox});',
            f'node["waterway"="waterfall"]({bbox});',
            f'node["waterway"="rapids"]({bbox});',
            f'way["waterway"="rapids"]({bbox});',
            f'node["waterway"="fish_pass"]({bbox});',
            f'way["waterway"="fish_pass"]({bbox});',
            f'node["waterway"="lock"]({bbox});',
        ]
    if "bridge" in types:
        struct += [
            'way["bridge"="yes"]'
            '["highway"~"^(primary|secondary|tertiary|trunk|unclassified|residential|service)$"]'
            f"({bbox});"
        ]
    if "marina" in types:
        struct += [
            f'node["amenity"="marina"]({bbox});',
            f'way["amenity"="marina"]({bbox});',
            f'node["leisure"="marina"]({bbox});',
            f'way["leisure"="marina"]({bbox});',
            f'relation["leisure"="marina"]({bbox});',
        ]
    if "point" in types:
        struct += [
            f'node["natural"="cape"]({bbox});',
            f'node["natural"="headland"]({bbox});',
            f'way["natural"="headland"]({bbox});',
            f'node["natural"="peninsula"]({bbox});',
            f'node["natural"="promontory"]({bbox});',
            f'node["man_made"="lighthouse"]({bbox});',
            f'node["man_made"="offshore_platform"]({bbox});',
        ]
    if "fishing" in types:
        struct += [
            f'node["leisure"="fishing"]({bbox});',
            f'way["leisure"="fishing"]({bbox});',
            f'node["leisure"="fishing_stand"]({bbox});',
            f'node["fishing"="yes"]["leisure"!="slipway"]["amenity"!="boat_ramp"]({bbox});',
            f'node["sport"="fishing"]({bbox});',
        ]
    if "buoy" in types:
        struct += [
            f'node["seamark:type"="buoy_lateral"]({bbox});',
            f'node["seamark:type"="buoy_cardinal"]({bbox});',
            f'node["seamark:type"="buoy_safe_water"]({bbox});',
            f'node["seamark:type"="buoy_isolated_danger"]({bbox});',
            f'node["seamark:type"="beacon_lateral"]({bbox});',
            f'node["seamark:type"="beacon_cardinal"]({bbox});',
            f'node["seamark:type"="beacon_safe_water"]({bbox});',
            f'node["seamark:type"="beacon_isolated_danger"]({bbox});',
            f'node["seamark:type"="light_major"]({bbox});',
            f'node["seamark:type"="light_minor"]({bbox});',
            f'node["man_made"="buoy"]({bbox});',
        ]
    if "fishing_shop" in types:
        struct += [f'node["shop"="fishing"]({bbox});']
    if "boat_ramp" in types:
        struct += [
            f'node["amenity"="boat_ramp"]({bbox});',
            f'way["amenity"="boat_ramp"]({bbox});',
            f'node["leisure"="slipway"]({bbox});',
            f'way["leisure"="slipway"]({bbox});',
        ]
    if "dive_site" in types:
        struct += [
            f'node["sport"="scuba_diving"]({bbox});',
            f'node["sport"="diving"]({bbox});',
            f'way["sport"="scuba_diving"]({bbox});',
        ]
    if "seawall" in types:
        struct += [
            f'node["man_made"="seawall"]({bbox});',
            f'way["man_made"="seawall"]({bbox});',
            f'node["man_made"="revetment"]({bbox});',
            f'way["man_made"="revetment"]({bbox});',
        ]

    if not habitat and not struct:
        return ""

    # Build the combined query using named sets so each half can use the
    # correct output mode.  Both sets' results land in the same `elements`
    # array in the Overpass JSON response.
    parts: list[str] = ["[out:json][timeout:14];"]
    if habitat:
        parts.append("(" + "".join(habitat) + ")->.h;")
    if struct:
        parts.append("(" + "".join(struct) + ")->.s;")
    if habitat:
        parts.append(".h out geom;")
    if struct:
        parts.append(".s out center;")
    return "".join(parts)

def _classify_osm_tags(tags: dict[str, Any]) -> Optional[str]:
    """Map an OSM element's tags to a VALID_TYPES string, or None to discard."""
    natural = tags.get("natural", "")
    wetland = tags.get("wetland", "")
    waterway = tags.get("waterway", "")
    man_made = tags.get("man_made", "")
    seamark = tags.get("seamark:type", "")

    # ── Habitats ──────────────────────────────────────────────────────────────
    if natural == "wetland":
        if wetland == "seagrass":
            return "grass_flat"
        if wetland == "saltmarsh":
            return "saltmarsh"
        if wetland == "mangrove":
            return "mangrove"
        if wetland == "tidalflat":
            return "tidal_flat"
        if wetland == "kelp":
            return "kelp"
        return None  # unknown wetland subtype — skip

    if natural == "kelp":
        return "kelp"
    if natural == "mud":
        return "tidal_flat"
    if natural == "beach":
        return "beach"
    if natural == "bay":
        return "inlet"
    if natural == "reef":
        return "reef"
    if natural in ("shoal", "rock", "sandbank"):
        return "shoal"
    if natural in ("cape", "headland", "peninsula", "promontory"):
        return "point"
    if natural in ("sand",) and tags.get("access") not in ("private", "no"):
        return "beach"

    if tags.get("harbour") == "yes":
        return "inlet"

    if tags.get("landuse") == "aquaculture" and (
        tags.get("produce") == "oyster" or tags.get("product") == "oysters"
    ):
        return "oyster_reef"

    if tags.get("historic") == "wreck" or seamark == "wreck":
        return "wreck"

    if seamark in ("rock_awash", "underwater_rock", "rock_submerged", "obstruction"):
        return "shoal"
    if seamark == "artificial_reef" or tags.get("landuse") == "artificial_reef":
        return "reef"
    if seamark.startswith("beacon") or seamark in ("light_major", "light_minor"):
        return "buoy"

    # ── Waterways ─────────────────────────────────────────────────────────────
    if waterway in ("tidal_channel", "river", "canal", "stream"):
        return "inlet"
    if waterway in ("weir", "dam", "waterfall", "rapids", "fish_pass", "lock"):
        return "jetty"  # turbulent/oxygenated water — same angling context

    # ── Man-made structures ───────────────────────────────────────────────────
    if man_made == "pier" or tags.get("leisure") == "pier":
        # Exclude private/restricted access — private docks and boat yards are not public fishing piers
        if tags.get("access") in ("private", "no"):
            return None
        return "pier"
    if man_made == "jetty":
        return "jetty"
    if man_made in ("groyne", "breakwater"):
        return "jetty"
    if man_made in ("seawall", "revetment"):
        return "seawall"
    if man_made in ("lighthouse", "offshore_platform"):
        return "point"
    if man_made == "buoy":
        return "buoy"

    if tags.get("bridge") == "yes" and tags.get("highway"):
        return "bridge"

    amenity = tags.get("amenity", "")
    if amenity in ("marina",):
        return "marina"
    if amenity == "boat_ramp":
        return "boat_ramp"

    leisure = tags.get("leisure", "")
    if leisure == "marina":
        return "marina"
    if leisure in ("fishing", "fishing_stand"):
        return "fishing"
    if leisure == "slipway":
        return "boat_ramp"

    sport = tags.get("sport", "")
    if sport in ("scuba_diving", "diving"):
        return "dive_site"
    if sport == "fishing":
        return "fishing"

    if tags.get("fishing") == "yes" and amenity not in ("boat_ramp",) and leisure not in ("slipway",):
        return "fishing"

    if seamark.startswith("buoy"):
        return "buoy"

    if tags.get("shop") == "fishing":
        return "fishing_shop"

    return None

def _deduplicate(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate markers by name and by proximity.

    Strategy
    --------
    1. **Name dedup** — same type + same normalised name → keep first only.
       This collapses the many OSM way segments that share a name (e.g. a
       bridge made of several road segments).
    2. **Proximity dedup** — same type within a per-type threshold → keep first.
       Prevents marker stacking when OSM and NOAA report the same wreck/reef.

    The proximity check uses an O(n) grid-cell approach instead of the
    naïve O(n²) scan.  Each accepted spot occupies the grid cell
    ``(type, floor(lat/thresh), floor(lng/thresh))``.  A new spot is
    rejected when any of the 9 cells covering its neighbourhood is
    occupied.  Because the cell size equals the threshold, this correctly
    identifies all pairs closer than ``thresh`` with at most a small
    border-zone artefact (spots between 1× and √2× thresh apart near a
    cell corner).  For display deduplication that trade-off is acceptable.
    """
    named_seen: dict[str, bool] = {}
    grid: dict[tuple, bool] = {}  # (type, grid_lat, grid_lng) → True
    out: list[dict[str, Any]] = []

    for spot in spots:
        name = (spot.get("name") or "").strip()
        if name:
            key = f"{spot['type']}|{name.lower()}"
            if key in named_seen:
                continue
            named_seen[key] = True

        thresh = _PROX.get(spot["type"], _PROX["_default"])
        # thresh == 0 → polygon habitat type; skip centroid-proximity dedup
        # so adjacent polygon patches are not erroneously collapsed.
        if thresh > 0:
            gl = int(spot["lat"] / thresh)
            gn = int(spot["lng"] / thresh)
            t = spot["type"]
            # Check the 3×3 neighbourhood (9 cells) to catch spots that sit
            # near a cell boundary and would otherwise slip through.
            too_close = any(
                (t, gl + dl, gn + dm) in grid for dl in (-1, 0, 1) for dm in (-1, 0, 1)
            )
            if too_close:
                continue
            grid[(t, gl, gn)] = True

        out.append(spot)

    return out

def _post_overpass(query: str) -> list[dict[str, Any]]:
    """POST an Overpass QL query, falling back to the mirror on failure.

    Returns the raw ``elements`` list from the Overpass JSON response.
    Raises ``requests.RequestException`` if all endpoints fail.
    """
    body = "data=" + urllib.parse.quote(query)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    last_exc: Optional[Exception] = None

    for url in _OVERPASS_URLS:
        try:
            resp = _HTTP.post(url, data=body, headers=headers, timeout=_TIMEOUT_OVERPASS)
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except Exception as exc:
            logger.warning("Overpass request failed url=%s error=%s", url, exc)
            last_exc = exc

    raise last_exc or RuntimeError("All Overpass endpoints failed")

def _get_noaa_enc_layer(
    layer: int,
    south: float,
    west: float,
    north: float,
    east: float,
) -> list[dict[str, Any]]:
    """Query a single NOAA ENC ArcGIS layer within the bounding box.

    Returns the raw ``features`` list, or an empty list on any error so that
    a NOAA outage never blocks the OSM results from being returned.
    """
    geometry = _json.dumps(
        {
            "xmin": west,
            "ymin": south,
            "xmax": east,
            "ymax": north,
            "spatialReference": {"wkid": 4326},
        }
    )
    params: dict[str, str] = {
        "geometry": geometry,
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "OBJNAM,INFORM,CATNMK,WATLEV",
        "returnGeometry": "true",
        "f": "json",
    }
    url = f"{_NOAA_ENC_BASE}/{layer}/query"
    try:
        resp = _HTTP.get(url, params=params, timeout=_TIMEOUT_NOAA_ENC)
        resp.raise_for_status()
        return resp.json().get("features", [])
    except Exception as exc:
        logger.warning("NOAA ENC layer=%s query failed: %s", layer, exc)
        return []

def _noaa_features_to_spots(
    features: list[dict[str, Any]],
    spot_type: str,
) -> list[dict[str, Any]]:
    """Convert NOAA ENC ArcGIS feature dicts to our ``{lat, lng, type, name}`` format.

    Handles both point geometries (``x``/``y``) and polygon geometries
    (``rings``), using the centroid of the first ring for polygons.
    """
    spots: list[dict[str, Any]] = []
    for feat in features:
        geom = feat.get("geometry") or {}
        attrs = feat.get("attributes") or {}

        x: Optional[float] = geom.get("x")
        y: Optional[float] = geom.get("y")

        if x is None or y is None:
            rings = geom.get("rings", [])
            if rings and rings[0]:
                coords = rings[0]
                x = sum(p[0] for p in coords) / len(coords)
                y = sum(p[1] for p in coords) / len(coords)

        if x is None or y is None:
            continue

        name = attrs.get("OBJNAM") or attrs.get("INFORM") or ""
        spots.append(
            {"lat": float(y), "lng": float(x), "type": spot_type, "name": name,
             "source": "noaa"}
        )

    return spots

# ─────────────────────────────────────────────────────────────────────────────
# Public functions
# ─────────────────────────────────────────────────────────────────────────────

def fetch_osm_structures(
    south: float,
    west: float,
    north: float,
    east: float,
    types: set[str],
) -> list[dict[str, Any]]:
    """Fetch fish-holding structures from OpenStreetMap via the Overpass API.

    Parameters
    ----------
    south, west, north, east:
        Bounding box in WGS-84 decimal degrees.
    types:
        Set of type strings (must be a subset of ``VALID_TYPES``).

    Returns
    -------
    List of ``{lat, lng, type, name}`` dicts for elements inside the bbox.
    """
    bbox = f"{south},{west},{north},{east}"
    query = _build_overpass_query(bbox, types)
    if not query:
        return []

    elements = _post_overpass(query)

    spots: list[dict[str, Any]] = []
    for el in elements:
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lng = el.get("lon") or (el.get("center") or {}).get("lon")
        # ``out geom;`` omits the ``center`` key for ways — fall back to the
        # mean of the geometry coordinates so we always have a valid centroid.
        if lat is None or lng is None:
            raw = el.get("geometry") or []
            pts = [(g["lat"], g["lon"]) for g in raw if "lat" in g and "lon" in g]
            if pts:
                lat = sum(p[0] for p in pts) / len(pts)
                lng = sum(p[1] for p in pts) / len(pts)
        if not lat or not lng:
            continue
        # Drop way-centroids that Overpass computed outside the user's viewport
        if not (south <= lat <= north and west <= lng <= east):
            continue

        tags = el.get("tags") or {}
        spot_type = _classify_osm_tags(tags)
        if not spot_type or spot_type not in types:
            continue

        name = (
            tags.get("name")
            or tags.get("seamark:name")
            or tags.get("seamark:buoy:colour")
            or tags.get("addr:housename")
            or ""
        )
        osm_id = el.get("id")
        spot: dict[str, Any] = {
            "lat": lat, "lng": lng, "type": spot_type, "name": name,
            "source": "osm",
        }
        if osm_id:
            spot["osm_id"] = osm_id
            spot["osm_type"] = el.get("type", "node")

        # Attach polygon geometry for habitat area types so the client can
        # draw a filled outline instead of a single-point marker.
        if el.get("type") == "way" and spot_type in POLYGON_HABITAT_TYPES:
            raw_geom = el.get("geometry", [])
            coords = [
                [g["lat"], g["lon"]] for g in raw_geom if "lat" in g and "lon" in g
            ]
            if len(coords) >= 3:
                spot["geometry"] = _decimate_ring(coords)

        spots.append(spot)

    return spots

def fetch_noaa_structures(
    south: float,
    west: float,
    north: float,
    east: float,
    types: set[str],
) -> list[dict[str, Any]]:
    """Fetch wrecks and marine obstructions from the NOAA ENC chart service.

    Queries the NOAA OCS ENCOnline ArcGIS REST service for:
    - Wrecks (layer 2) when ``"wreck"`` is in *types*
    - Obstructions (layer 3) and rocks (layer 4) when ``"shoal"`` or
      ``"reef"`` is in *types*

    Parameters
    ----------
    south, west, north, east:
        Bounding box in WGS-84 decimal degrees.
    types:
        Set of type strings (must be a subset of ``VALID_TYPES``).

    Returns
    -------
    List of ``{lat, lng, type, name}`` dicts.  Returns an empty list (rather
    than raising) if the NOAA service is unavailable.
    """
    # Determine which layers to fetch, then request them all in parallel.
    # Previously the 3 NOAA layers were sequential; each _get_noaa_enc_layer()
    # call takes up to 4+12 s on a cold connection.  Running them concurrently
    # halves the worst-case NOAA latency when both wrecks and shoals are active.
    layer_jobs: list[tuple] = []  # [(layer_id, spot_type), ...]
    if "wreck" in types:
        layer_jobs.append((_NOAA_LAYER_WRECKS, "wreck"))
    if types & {"shoal", "reef"}:
        layer_jobs.append((_NOAA_LAYER_OBSTRUCTIONS, "shoal"))
        layer_jobs.append((_NOAA_LAYER_ROCKS, "shoal"))

    if not layer_jobs:
        return []

    spots: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(layer_jobs)) as pool:
        futs = {
            pool.submit(_get_noaa_enc_layer, lid, south, west, north, east): stype
            for lid, stype in layer_jobs
        }
        for fut, stype in futs.items():
            try:
                feats = fut.result(timeout=14)
                spots.extend(_noaa_features_to_spots(feats, stype))
            except Exception as exc:
                logger.warning("NOAA layer fetch failed (type=%s): %s", stype, exc)

    return spots

def find_fish_structures(
    south: float,
    west: float,
    north: float,
    east: float,
    types: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Identify fish-holding structures within a map bounding box.

    Queries OpenStreetMap (via Overpass API) and NOAA ENC (for wrecks and
    marine obstructions), merges the results, deduplicates by name and
    proximity, and attaches habitat-specific fishing tips.

    Parameters
    ----------
    south, west, north, east:
        WGS-84 bounding box of the user's current map view.
    types:
        Set of structure-type strings to include.  Pass ``None`` (default) to
        include all ``VALID_TYPES``.  Any unrecognised strings are silently
        ignored.

    Returns
    -------
    JSON-serialisable list of dicts, each containing:

    =========  =======  ================================================
    Key        Type     Description
    =========  =======  ================================================
    lat        float    WGS-84 latitude
    lng        float    WGS-84 longitude
    type       str      One of ``VALID_TYPES``
    name       str      Feature name, or empty string
    =========  =======  ================================================

    Fishing tips (``tip``) are intentionally omitted — the JS client owns
    ``STRUCTURE_TIPS`` locally and looks them up as
    ``f.tip || STRUCTURE_TIPS[f.type]``, halving the wire payload.
    """
    active_types: set[str] = (
        set(VALID_TYPES) if types is None else (set(types) & VALID_TYPES)
    )
    if not active_types:
        return []

    # ── Cache check  ─────────────────────────────────────────────────────────
    key = _cache_key(south, west, north, east, active_types)
    cached = _CACHE.get(key)
    if cached:
        ttl = _CACHE_TTL_FAILED if cached.get("failed") else _CACHE_TTL
        if (_time.time() - cached["ts"]) < ttl:
            logger.debug(
                "find_fish_structures cache hit key=%s failed=%s",
                key,
                cached.get("failed"),
            )
            return cached["data"]

    # ── Fetch from sources in parallel  ──────────────────────────────────────
    # OSM (Overpass) and NOAA ENC run concurrently so neither blocks the other.
    # Each is caught individually so a NOAA outage never drops the OSM results.
    osm_spots: list[dict[str, Any]] = []
    noaa_spots: list[dict[str, Any]] = []
    fetch_failed = False

    with ThreadPoolExecutor(max_workers=2) as pool:
        osm_fut = pool.submit(
            fetch_osm_structures, south, west, north, east, active_types
        )
        noaa_fut = pool.submit(
            fetch_noaa_structures, south, west, north, east, active_types
        )

        try:
            osm_spots = osm_fut.result(timeout=22)
        except FutureTimeoutError:
            logger.warning("fetch_osm_structures timed out (parallel executor)")
            fetch_failed = True
        except Exception as exc:
            logger.warning(
                "fetch_osm_structures failed, continuing with NOAA only: %s", exc
            )
            fetch_failed = True

        try:
            noaa_spots = noaa_fut.result(timeout=14)
        except FutureTimeoutError:
            logger.warning("fetch_noaa_structures timed out (parallel executor)")
        except Exception as exc:
            logger.warning("fetch_noaa_structures failed: %s", exc)

    # OSM first — it generally has richer names; NOAA supplements with
    # authoritative wreck/obstruction records not always in OSM.
    # Post-filter by type to guard against any source returning extras.
    all_spots = [s for s in osm_spots + noaa_spots if s["type"] in active_types]
    deduped = _deduplicate(all_spots)
    # Tips are not attached server-side — the JS client owns STRUCTURE_TIPS and
    # looks them up locally via ``f.tip || STRUCTURE_TIPS[f.type]``.  Omitting
    # them here shrinks the wire payload and the in-memory cache by ~50 % for
    # point markers and keeps the two tables in sync without duplication.

    logger.info(
        "find_fish_structures bbox=(%.4f,%.4f,%.4f,%.4f) types=%s "
        "osm=%d noaa=%d merged=%d deduped=%d",
        south,
        west,
        north,
        east,
        sorted(active_types),
        len(osm_spots),
        len(noaa_spots),
        len(all_spots),
        len(deduped),
    )

    # ── Cache store  ──────────────────────────────────────────────────────────
    if len(_CACHE) >= _CACHE_MAX:
        _cache_evict()
    _CACHE[key] = {"ts": _time.time(), "data": deduped, "failed": fetch_failed}

    return deduped


# ── AI Habitat Spot Finder ────────────────────────────────────────────────────
# Serves the AI Habitat overlay on the fishing map.  Previously the browser
# called Overpass directly; routing it through the backend adds a 30-minute
# server-side cache so the same viewport/species combination from multiple users
# triggers only one Overpass round-trip.
#
# Tag lists mirror HABITAT_DEFS.tags in static/js/fishing_map.js — keep them
# in sync if either side changes.

_HABITAT_TAGS: dict[str, list[str]] = {
    "surf": [
        'way["natural"="beach"]',
        'node["natural"="beach"]',
        'node["natural"="shoal"]',
        'way["natural"="shoal"]',
        'node["natural"="sandbank"]',
        'way["natural"="sandbank"]',
        'node["seamark:type"="rock_awash"]',
        'node["seamark:type"="rock_submerged"]',
    ],
    "mangrove": [
        'way["natural"="wetland"]["wetland"="mangrove"]',
        'way["waterway"="tidal_channel"]',
        'way["waterway"="stream"]["tidal"="yes"]',
        'way["waterway"="drain"]["tidal"="yes"]',
    ],
    "grassflat": [
        'way["natural"="wetland"]["wetland"="seagrass"]',
        'way["natural"="wetland"]["wetland"="saltmarsh"]',
        'node["natural"="wetland"]["wetland"="seagrass"]',
        'way["waterway"="tidal_channel"]',
        'way["natural"="shoal"]',
    ],
    "estuary": [
        'way["natural"="wetland"]["wetland"="saltmarsh"]',
        'way["waterway"="tidal_channel"]',
        'node["natural"="shoal"]',
        'way["natural"="wetland"]["wetland"="tidalflat"]',
        'node["natural"="wetland"]["wetland"="tidalflat"]',
        'way["landuse"="aquaculture"]["produce"="oyster"]',
        'way["landuse"="aquaculture"]["product"="oysters"]',
        'node["seamark:type"="beacon_lateral"]',
        'node["seamark:type"="buoy_lateral"]',
    ],
    "reef": [
        'way["natural"="reef"]',
        'node["natural"="reef"]',
        'node["natural"="shoal"]',
        'node["seamark:type"="wreck"]',
        'node["historic"="wreck"]',
        'way["seamark:type"="wreck"]',
        'way["historic"="wreck"]',
        'node["seamark:type"="artificial_reef"]',
        'node["seamark:type"="obstruction"]',
        'node["man_made"="pier"]["access"!="private"]',
        'node["man_made"="jetty"]',
        'node["seamark:type"="rock_awash"]',
    ],
    "bottom": [
        'node["natural"="shoal"]',
        'way["natural"="shoal"]',
        'node["natural"="sandbank"]',
        'way["natural"="sandbank"]',
        'way["waterway"="tidal_channel"]',
        'way["natural"="wetland"]["wetland"="tidalflat"]',
        'node["natural"="wetland"]["wetland"="tidalflat"]',
    ],
    "general": [
        'way["natural"="reef"]',
        'node["natural"="reef"]',
        'node["natural"="shoal"]',
        'way["natural"="wetland"]["wetland"="saltmarsh"]',
        'way["waterway"="tidal_channel"]',
        'node["man_made"="pier"]["access"!="private"]',
        'node["man_made"="breakwater"]',
    ],
}

_HABITAT_CACHE: dict[tuple, dict[str, Any]] = {}
_HABITAT_CACHE_TTL: int = 1800   # 30 minutes
_HABITAT_CACHE_MAX: int = 128    # bbox × habitat_type slots


def _osm_tags_to_type(tags: dict[str, str]) -> str:
    """Classify OSM element tags into a habitat type string.

    Mirrors osmTagsToType() in fishing_map.js for consistent feature labelling.
    """
    if tags.get("wetland") == "saltmarsh":  return "saltmarsh"
    if tags.get("wetland") == "seagrass":   return "seagrass"
    if tags.get("wetland") == "mangrove":   return "mangrove"
    if tags.get("wetland") == "tidalflat":  return "tidalflat"
    if tags.get("natural") == "reef":       return "reef"
    if tags.get("natural") == "shoal":      return "shoal"
    if tags.get("natural") == "beach":      return "beach"
    if tags.get("natural") == "bay":        return "bay"
    if tags.get("waterway"):                return "channel"
    if tags.get("seamark:type") == "wreck" or tags.get("historic") == "wreck":
        return "wreck"
    return "general"


def fetch_ai_habitats(
    south: float,
    west: float,
    north: float,
    east: float,
    habitat_type: str,
) -> list[dict[str, Any]]:
    """Return Overpass OSM features for the AI Habitat map overlay.

    Results are cached server-side for _HABITAT_CACHE_TTL seconds.
    Returns an empty list for the ``"pelagic"`` habitat type (open-ocean
    species have no fixed OSM features) or when all Overpass mirrors fail.
    """
    tags = _HABITAT_TAGS.get(habitat_type, [])
    if not tags:
        return []

    cache_key = (
        round(south, 2),
        round(west, 2),
        round(north, 2),
        round(east, 2),
        habitat_type,
    )
    now = _time.time()
    cached = _HABITAT_CACHE.get(cache_key)
    if cached and now - cached["ts"] < _HABITAT_CACHE_TTL:
        logger.debug("fetch_ai_habitats cache hit key=%s", cache_key)
        return cached["data"]

    if len(_HABITAT_CACHE) >= _HABITAT_CACHE_MAX:
        stale = [
            k for k, v in list(_HABITAT_CACHE.items())
            if now - v["ts"] >= _HABITAT_CACHE_TTL
        ]
        for k in stale:
            _HABITAT_CACHE.pop(k, None)
        if len(_HABITAT_CACHE) >= _HABITAT_CACHE_MAX:
            try:
                del _HABITAT_CACHE[next(iter(_HABITAT_CACHE))]
            except (KeyError, StopIteration):
                pass

    bbox = f"{south},{west},{north},{east}"
    tag_str = "".join(f"{t}({bbox});" for t in tags)
    query = f"[out:json][timeout:14];({tag_str});out center;"

    features: list[dict[str, Any]] = []
    for url in _OVERPASS_URLS:
        try:
            resp = _HTTP.post(url, data={"data": query}, timeout=_TIMEOUT_OVERPASS)
            if resp.status_code != 200:
                logger.warning(
                    "fetch_ai_habitats: Overpass %s returned HTTP %s", url, resp.status_code
                )
                continue
            for el in resp.json().get("elements", []):
                lat = el.get("lat") or (el.get("center") or {}).get("lat")
                lng = el.get("lon") or (el.get("center") or {}).get("lon")
                if not (lat and lng):
                    continue
                el_tags: dict[str, str] = el.get("tags") or {}
                features.append(
                    {
                        "lat": lat,
                        "lng": lng,
                        "name": el_tags.get("name", ""),
                        "osm_type": _osm_tags_to_type(el_tags),
                    }
                )
            break
        except Exception as exc:
            logger.warning(
                "fetch_ai_habitats: Overpass %s failed (%s); trying next mirror", url, exc
            )
            continue

    _HABITAT_CACHE[cache_key] = {"ts": _time.time(), "data": features}
    logger.info(
        "fetch_ai_habitats bbox=(%.2f,%.2f,%.2f,%.2f) habitat=%s features=%d",
        south, west, north, east, habitat_type, len(features),
    )
    return features
