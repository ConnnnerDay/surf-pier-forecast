"""Station catalog provider adapter (sprint 17).

Ports nearest-station resolution for arbitrary US coastal coordinates
from the legacy `services/stations.py` (identical in `v2/backend`), behind
typed contracts, `app.infra.http_client.BoundedHTTPClient`, and an
explicit, independently-testable cache. Per docs/R1_RECONCILIATION_AUDIT.md,
this is an adapt, not a verbatim carry-over.

These are the public NOAA CO-OPS and NDBC station *catalogs* — metadata
used to find which station ID to query elsewhere (sprints 13-15's
adapters), not themselves decision-relevant readings. Catalog fetches
therefore degrade gracefully: a `ProviderError` is caught and logged,
returning `[]` rather than propagating, matching the legacy module's
"this feature can only add coverage, never make a location worse"
design. This is the opposite resilience posture from sprint 14's
water-temperature/tide-*reading* fetches, which raise — the distinction
is metadata-for-routing vs. a value the forecast is built on.

Adaptations from the legacy module:

- **Idempotent, testable refresh** (the sprint's "timestamps" and
  "idempotent refresh" requirements): `StationCatalogCache` replaces the
  legacy module's module-level `dict` + `threading.Lock`, tracks
  `fetched_at` explicitly (queryable via `last_fetched_at`), uses
  `asyncio.Lock` (this module is async, unlike the legacy
  `requests`-based one), and accepts an injectable clock so its TTL
  behavior — "still fresh, don't refetch" vs. "expired, refetch and
  replace" vs. "last fetch failed, use the short negative TTL" — is
  characterized deterministically rather than by sleeping in tests.
- **Fetch and distance-ranking are decoupled.** The legacy
  `nearest_coops_station`/`nearest_ndbc_stations` called the
  fetch-and-cache layer directly, coupling network I/O with pure
  haversine math. Here, `nearest_coops_station`/`nearest_ndbc_stations`
  are pure functions over an already-resolved catalog list — callers
  (a future location-resolution sprint) compose them with
  `StationCatalogCache.get_or_refresh` explicitly. This also makes the
  distance-ranking logic testable with zero network mocking.
"""

from __future__ import annotations

import asyncio
import logging
import math
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel

from app.infra.http_client import BoundedHTTPClient, ProviderError

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 24 * 3600
_DEFAULT_NEGATIVE_TTL_SECONDS = 120

_COOPS_MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
_COOPS_TIDE_URL = f"{_COOPS_MDAPI}?type=tidepredictions"
_COOPS_TEMP_URL = f"{_COOPS_MDAPI}?type=watertemp"
_NDBC_URL = "https://www.ndbc.noaa.gov/activestations.xml"

_EARTH_RADIUS_MILES = 3958.8


class CoopsStationCatalogEntry(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    state: str


class NdbcStationCatalogEntry(BaseModel):
    id: str
    lat: float
    lng: float
    has_met: bool


class NearestStation(BaseModel):
    id: str
    distance_miles: float


class NearestCoopsStation(NearestStation):
    name: str
    state: str


T = TypeVar("T")


class StationCatalogCache(Generic[T]):
    """Caches one station catalog with the legacy module's TTL policy,
    made explicit and idempotent: a successful (non-empty) fetch is
    reused for `ttl_seconds`; an empty result (the fetch degraded to
    `[]`) is reused for only `negative_ttl_seconds`, so an upstream
    outage doesn't turn every caller into a fresh blocking fetch, while
    still recovering quickly once the source is healthy again.

    `clock` is injectable so tests can control elapsed time
    deterministically instead of sleeping.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        negative_ttl_seconds: float = _DEFAULT_NEGATIVE_TTL_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._negative_ttl_seconds = negative_ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._stations: list[T] | None = None
        self._fetched_at: datetime | None = None

    @property
    def last_fetched_at(self) -> datetime | None:
        return self._fetched_at

    async def get_or_refresh(self, fetch: Callable[[], Awaitable[list[T]]]) -> list[T]:
        """Return the cached catalog if it's still within its TTL;
        otherwise call *fetch*, cache the result with the current time,
        and return it. Concurrent callers serialize on the same fetch
        rather than triggering duplicate requests.
        """
        async with self._lock:
            if self._stations is not None and self._fetched_at is not None:
                ttl = (
                    self._ttl_seconds if self._stations else self._negative_ttl_seconds
                )
                age = (self._clock() - self._fetched_at).total_seconds()
                if age < ttl:
                    return self._stations
            stations = await fetch()
            self._stations = stations
            self._fetched_at = self._clock()
            return stations


def _haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in miles."""
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return _EARTH_RADIUS_MILES * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_coops_station(
    stations: list[CoopsStationCatalogEntry], lat: float, lng: float
) -> NearestCoopsStation | None:
    """Return the nearest CO-OPS station in *stations* to `(lat, lng)`,
    or `None` if *stations* is empty (e.g. the catalog fetch degraded).
    """
    best: CoopsStationCatalogEntry | None = None
    best_distance = float("inf")
    for station in stations:
        distance = _haversine_miles(lat, lng, station.lat, station.lng)
        if distance < best_distance:
            best_distance = distance
            best = station
    if best is None:
        return None
    return NearestCoopsStation(
        id=best.id,
        name=best.name,
        state=best.state,
        distance_miles=round(best_distance, 1),
    )


def nearest_ndbc_stations(
    stations: list[NdbcStationCatalogEntry], lat: float, lng: float, n: int = 2
) -> list[NearestStation]:
    """Return up to *n* nearest met-reporting NDBC buoys in *stations* to
    `(lat, lng)`, nearest-first. Buoys with `has_met=False` (no
    wind/wave sensors) are excluded — they're not useful for marine
    conditions.
    """
    scored = [
        (_haversine_miles(lat, lng, station.lat, station.lng), station.id)
        for station in stations
        if station.has_met
    ]
    scored.sort(key=lambda pair: pair[0])
    return [
        NearestStation(id=station_id, distance_miles=round(distance, 1))
        for distance, station_id in scored[:n]
    ]


async def fetch_coops_tide_catalog(
    client: BoundedHTTPClient,
) -> list[CoopsStationCatalogEntry]:
    """Fetch the CO-OPS tide-prediction station catalog. Degrades to
    `[]` on any provider failure — see the module docstring.
    """
    return await _fetch_coops_catalog(client, _COOPS_TIDE_URL, label="tide")


async def fetch_coops_watertemp_catalog(
    client: BoundedHTTPClient,
) -> list[CoopsStationCatalogEntry]:
    """Fetch the CO-OPS water-temperature station catalog. Degrades to
    `[]` on any provider failure — see the module docstring.
    """
    return await _fetch_coops_catalog(client, _COOPS_TEMP_URL, label="water-temp")


async def _fetch_coops_catalog(
    client: BoundedHTTPClient, url: str, *, label: str
) -> list[CoopsStationCatalogEntry]:
    try:
        data = cast("dict[str, Any]", await client.get_json(url))
    except ProviderError:
        logger.warning("CO-OPS %s station catalog unavailable", label, exc_info=True)
        return []

    out: list[CoopsStationCatalogEntry] = []
    for raw in data.get("stations", []):
        try:
            out.append(
                CoopsStationCatalogEntry(
                    id=str(raw["id"]),
                    name=raw.get("name", ""),
                    lat=float(raw["lat"]),
                    lng=float(raw["lng"]),
                    state=(raw.get("state") or "").strip(),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def fetch_ndbc_catalog(
    client: BoundedHTTPClient,
) -> list[NdbcStationCatalogEntry]:
    """Fetch the NDBC active-station catalog (XML). Degrades to `[]` on
    any provider failure or malformed XML — see the module docstring.
    """
    try:
        text = await client.get_text(_NDBC_URL)
    except ProviderError:
        logger.warning("NDBC station catalog unavailable", exc_info=True)
        return []

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        logger.warning("NDBC station catalog returned malformed XML", exc_info=True)
        return []

    out: list[NdbcStationCatalogEntry] = []
    for station_el in root.iter("station"):
        station_id = station_el.get("id")
        lat = station_el.get("lat")
        lng = station_el.get("lon")
        if not station_id or lat is None or lng is None:
            continue
        try:
            out.append(
                NdbcStationCatalogEntry(
                    id=str(station_id),
                    lat=float(lat),
                    lng=float(lng),
                    has_met=(station_el.get("met", "n") or "n").lower() == "y",
                )
            )
        except (TypeError, ValueError):
            continue
    return out
