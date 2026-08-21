"""Canonical FastAPI entrypoint.

Versioned `/v1` routes land in sprint 25: `app/api/v1/locations.py`
(`GET /v1/locations/search`, `POST /v1/locations/resolve`) and
`app/api/v1/forecasts.py` (`GET /v1/forecasts/{location_id}`,
`POST /v1/forecasts/{location_id}/refresh`). The canonical roadmap's
"Required API surface" also names `GET`/`PATCH /v1/me/preferences` —
deliberately not attempted here: it needs Better Auth (sprint 28) and a
Postgres-backed preferences store, neither of which exists yet. This
sprint only builds what's buildable without them.

The `lifespan` context manager owns the app-lifetime resources every
`/v1` route depends on (`app.api.deps.AppState`): one pooled
`BoundedHTTPClient` (sprint 12), the three sprint-17
`StationCatalogCache` instances, and the sprint-24
`SnapshotCache[ForecastEnvelope]` (`app.domain.forecast_cache`'s
caching wiring) — created on startup and closed on shutdown rather than
per-request, same pooling rationale sprint 12's docstring gives for the
HTTP client itself.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.deps import AppState
from app.api.v1.forecasts import router as forecasts_router
from app.api.v1.locations import router as locations_router
from app.infra.http_client import BoundedHTTPClient
from app.infra.snapshot_cache import SnapshotCache
from app.providers.stations import StationCatalogCache


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with BoundedHTTPClient() as client:
        app.state.app_state = AppState(
            http_client=client,
            coops_tide_cache=StationCatalogCache(),
            coops_watertemp_cache=StationCatalogCache(),
            ndbc_cache=StationCatalogCache(),
            forecast_cache=SnapshotCache(),
        )
        yield


app = FastAPI(title="Surf & Pier Forecast API", version="0.1.0", lifespan=lifespan)

app.include_router(locations_router)
app.include_router(forecasts_router)


@app.get("/health/live")
def health_live() -> dict[str, str]:
    """Process is up. No dependency checks — used for liveness probes."""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    """Ready to serve traffic. Will grow real dependency checks (database,
    upstream reachability) once this app has any to check — see sprint 10.
    """
    return {"status": "ok"}
