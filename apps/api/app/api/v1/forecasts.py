"""`/v1/forecasts` router (sprint 25).

`GET /v1/forecasts/{location_id}` resolves *location_id* (via the same
`resolve_location_id` helper the locations router uses — see
`app.api.deps`) and assembles a `ForecastEnvelope` with sprint 21's
`assemble_forecast`. No new domain logic: the envelope this returns is
exactly what sprint 21 already builds, sprints 22-24's score/confidence/
caching refinements aside (still an unassigned wiring follow-up, not
this sprint's job).

`POST /v1/forecasts/{location_id}/refresh` is deliberately identical to
`GET` today. The canonical roadmap names it as a distinct endpoint
because a *cached* forecast service needs a way to force a live
refetch — but `assemble_forecast` doesn't cache anything yet (sprint
24's `SnapshotCache` isn't wired into it, the same unassigned follow-up
named above), so right now every `GET` is already a live refetch and
there is nothing for `refresh` to force. Once that wiring lands,
`refresh` is where its bypass-the-cache behavior belongs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import AppState, get_app_state, resolve_location_id
from app.domain.assembly import assemble_forecast
from app.domain.models import ForecastEnvelope
from app.providers.locations import load_water_temp_profiles

router = APIRouter(prefix="/v1/forecasts", tags=["forecasts"])


@router.get("/{location_id}")
async def get_forecast(
    location_id: str,
    state: AppState = Depends(get_app_state),  # noqa: B008
) -> ForecastEnvelope:
    location = await resolve_location_id(location_id, state)
    return await assemble_forecast(
        location, state.http_client, load_water_temp_profiles(), now=datetime.now(UTC)
    )


@router.post("/{location_id}/refresh")
async def refresh_forecast(
    location_id: str,
    state: AppState = Depends(get_app_state),  # noqa: B008
) -> ForecastEnvelope:
    return await get_forecast(location_id, state)
