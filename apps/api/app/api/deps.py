"""Shared, app-lifetime dependencies for the /v1 routers (sprint 25;
gained `forecast_cache` in the caching-wiring follow-up).

Everything a route handler needs that's expensive to create per-request —
the pooled `BoundedHTTPClient` (sprint 12), the three station-catalog
`StationCatalogCache` instances (sprint 17, one each for CO-OPS tide,
CO-OPS water-temperature, and NDBC), and the `SnapshotCache[ForecastEnvelope]`
(sprint 24, wired by `app.domain.forecast_cache`) — is created once in
`app.main`'s FastAPI lifespan and stored on `app.state`. `AppState` and
the `get_app_state`/`get_http_client` dependency functions here are the
typed accessors route handlers use instead of reaching into
`request.app.state` directly, and the seam tests use to inject a
mocked `BoundedHTTPClient` via `app.dependency_overrides`.

Curated locations and water-temperature profiles are deliberately not
part of `AppState`: `app.providers.locations.load_curated_locations`/
`load_water_temp_profiles` are already process-lifetime cached
(`functools.lru_cache`) since sprint 19, so there's nothing extra to
manage here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.domain.models import ForecastEnvelope
from app.infra.http_client import BoundedHTTPClient
from app.infra.snapshot_cache import SnapshotCache
from app.providers.coastal_bounds import gate_coastal_point
from app.providers.locations import (
    ResolvedLocation,
    find_curated_location,
    load_curated_locations,
    parse_dynamic_id,
    resolve_dynamic_location,
    resolved_from_curated,
)
from app.providers.stations import (
    CoopsStationCatalogEntry,
    NdbcStationCatalogEntry,
    StationCatalogCache,
    fetch_coops_tide_catalog,
    fetch_coops_watertemp_catalog,
    fetch_ndbc_catalog,
)


@dataclass
class AppState:
    http_client: BoundedHTTPClient
    coops_tide_cache: StationCatalogCache[CoopsStationCatalogEntry]
    coops_watertemp_cache: StationCatalogCache[CoopsStationCatalogEntry]
    ndbc_cache: StationCatalogCache[NdbcStationCatalogEntry]
    forecast_cache: SnapshotCache[ForecastEnvelope]


def get_app_state(request: Request) -> AppState:
    return request.app.state.app_state  # type: ignore[no-any-return]


def get_http_client(request: Request) -> BoundedHTTPClient:
    return get_app_state(request).http_client


async def resolve_location_id(location_id: str, state: AppState) -> ResolvedLocation:
    """Resolve any *location_id* — a curated id or a dynamic `pt_<lat>_<lng>`
    id (sprint 19's `format_dynamic_id`) — to a `ResolvedLocation`.

    Raises `HTTPException`: 404 if *location_id* is neither a known
    curated id nor a parseable dynamic id; 422 if it parses as a point
    but isn't within a coastal station's reach (sprint 18's
    `gate_coastal_point`). Shared by the locations and forecasts
    routers so both give the same answer for the same id.
    """
    curated = load_curated_locations()

    match = find_curated_location(location_id, curated)
    if match is not None:
        return resolved_from_curated(match)

    point = parse_dynamic_id(location_id)
    if point is None:
        raise HTTPException(status_code=404, detail="unknown location_id")
    lat, lng = point

    coops_tide, coops_watertemp, ndbc = await asyncio.gather(
        state.coops_tide_cache.get_or_refresh(
            lambda: fetch_coops_tide_catalog(state.http_client)
        ),
        state.coops_watertemp_cache.get_or_refresh(
            lambda: fetch_coops_watertemp_catalog(state.http_client)
        ),
        state.ndbc_cache.get_or_refresh(lambda: fetch_ndbc_catalog(state.http_client)),
    )

    gate = gate_coastal_point(lat, lng, coops_tide, ndbc)
    if not gate.is_coastal:
        raise HTTPException(
            status_code=422, detail="point is not a valid coastal location"
        )

    location, _anchor_miles = resolve_dynamic_location(
        lat, lng, curated, coops_tide, coops_watertemp, ndbc
    )
    return location
