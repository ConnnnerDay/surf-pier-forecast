"""`/v1/forecasts` router (sprint 25; cache-wired in the caching-wiring
follow-up).

`GET /v1/forecasts/{location_id}` resolves *location_id* (via the same
`resolve_location_id` helper the locations router uses — see
`app.api.deps`) and serves a forecast through
`app.domain.forecast_cache.get_or_assemble_forecast`: a fresh cached
envelope if one exists, otherwise a live `assemble_forecast` (sprint
21), subject to sprint 24's `SnapshotCache` fresh/stale/miss/expiry
policy — see that module's docstring for the full behavior, including
why `ForecastState.STALE` is a documented-but-practically-dormant path.

`POST /v1/forecasts/{location_id}/refresh` uses
`refresh_and_assemble_forecast` instead: it bypasses the cache's
freshness check entirely and forces a live assemble, repopulating the
cache for subsequent `GET`s — the distinguishing behavior this endpoint
was always meant to have, once caching wiring landed (see this
module's git history / `docs/CANONICAL_ROADMAP.md`'s checkpoint for
sprint 25 and the scoring/confidence-wiring follow-ups that preceded
this one).

Every route on this router requires ADR-004's internal request signature
(`app.api.internal_auth.require_internal_signature`), same as the
locations router — see that module's docstring for why this is wired now
and not before.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import AppState, get_app_state, resolve_location_id
from app.api.internal_auth import require_internal_signature
from app.domain.forecast_cache import (
    get_or_assemble_forecast,
    refresh_and_assemble_forecast,
)
from app.domain.models import ForecastEnvelope
from app.providers.locations import load_water_temp_profiles

router = APIRouter(
    prefix="/v1/forecasts",
    tags=["forecasts"],
    dependencies=[Depends(require_internal_signature)],
)


@router.get("/{location_id}")
async def get_forecast(
    location_id: str,
    state: AppState = Depends(get_app_state),  # noqa: B008
) -> ForecastEnvelope:
    location = await resolve_location_id(location_id, state)
    return await get_or_assemble_forecast(
        state.forecast_cache,
        location,
        state.http_client,
        load_water_temp_profiles(),
        now=datetime.now(UTC),
    )


@router.post("/{location_id}/refresh")
async def refresh_forecast(
    location_id: str,
    state: AppState = Depends(get_app_state),  # noqa: B008
) -> ForecastEnvelope:
    location = await resolve_location_id(location_id, state)
    return await refresh_and_assemble_forecast(
        state.forecast_cache,
        location,
        state.http_client,
        load_water_temp_profiles(),
        now=datetime.now(UTC),
    )
