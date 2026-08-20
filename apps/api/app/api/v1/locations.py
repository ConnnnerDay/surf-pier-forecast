"""`/v1/locations` router (sprint 25).

Two of the canonical roadmap's required endpoints
(`GET /v1/locations/search`, `POST /v1/locations/resolve`), both
buildable now with no auth or database dependency — unlike
`GET`/`PATCH /v1/me/preferences`, deliberately not attempted this
sprint (see `app.main`'s module docstring for the full accounting).

`search` is a thin HTTP wrapper over sprint 19's
`search_curated_locations` — no network calls, no app state needed.
`resolve` accepts either a curated `location_id` or a raw `lat`/`lng`
point and delegates to the shared `resolve_location_id` helper in
`app.api.deps`, so a location resolved here and a location resolved
implicitly by `GET /v1/forecasts/{location_id}` go through the exact
same logic and give the exact same answer for the same id.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, model_validator

from app.api.deps import AppState, get_app_state, resolve_location_id
from app.providers.coastal_bounds import is_valid_coordinate
from app.providers.locations import (
    CuratedLocation,
    ResolvedLocation,
    format_dynamic_id,
    load_curated_locations,
    search_curated_locations,
)

router = APIRouter(prefix="/v1/locations", tags=["locations"])


@router.get("/search")
async def search_locations(q: str = Query(..., min_length=1)) -> list[CuratedLocation]:
    return search_curated_locations(q, load_curated_locations())


class LocationResolveRequest(BaseModel):
    """Exactly one of `location_id`, or both `lat` and `lng`, must be
    given — never both shapes, never neither.
    """

    location_id: str | None = None
    lat: float | None = None
    lng: float | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> LocationResolveRequest:
        has_id = self.location_id is not None
        has_point = self.lat is not None and self.lng is not None
        if has_id == has_point:
            raise ValueError("provide exactly one of location_id, or both lat and lng")
        if has_point:
            assert self.lat is not None and self.lng is not None
            if not is_valid_coordinate(self.lat, self.lng):
                raise ValueError("lat/lng out of range")
        return self


@router.post("/resolve")
async def resolve_location(
    body: LocationResolveRequest,
    state: AppState = Depends(get_app_state),  # noqa: B008
) -> ResolvedLocation:
    if body.location_id is not None:
        location_id = body.location_id
    else:
        assert body.lat is not None and body.lng is not None
        location_id = format_dynamic_id(body.lat, body.lng)
    return await resolve_location_id(location_id, state)
