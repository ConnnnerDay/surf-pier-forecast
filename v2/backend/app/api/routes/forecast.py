from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.location import SavedLocation
from app.models.profile import Profile
from app.models.user import User
from domain.forecast import generate_forecast
from locations import build_dynamic_location

router = APIRouter(prefix="/forecast", tags=["forecast"])

_MPH_TO_KT = 0.868976


def _profile_dict(profile: Profile | None) -> dict | None:
    """Adapt v2's Profile row to the plain dict v1's domain/forecast.py and
    domain/species.py expect (see docs/V2_PLAN.md §2 — those modules are a
    verbatim port and read profile fields via `.get()`, so an absent key
    just falls back to "no preference" rather than erroring)."""
    if profile is None:
        return None
    return {
        "fishing_types": profile.fishing_styles or None,
        "targets": profile.target_species or None,
        "experience": profile.experience_level or None,
        "max_wind_kt": profile.max_wind_mph * _MPH_TO_KT if profile.max_wind_mph else None,
        "max_wave_ft": profile.max_surf_ft,
    }


def _strip_internal_fields(forecast: dict) -> dict:
    """v1's regulation data carries a `source_file` field holding the
    server's absolute local path to regulations_data.json (an internal
    implementation detail, not filtered anywhere in v1 either) — drop it so
    a public API response never leaks server filesystem paths."""
    for sp in forecast.get("species") or []:
        reg = sp.get("regulation")
        if isinstance(reg, dict):
            reg.pop("source_file", None)
    return forecast


@router.get("/{location_id}")
def get_forecast(
    location_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    location = db.query(SavedLocation).filter_by(id=location_id, user_id=user.id).one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

    profile = db.query(Profile).filter_by(user_id=user.id).one_or_none()

    # TODO(follow-up): this calls out to NOAA/NWS/NDBC synchronously on every
    # request. v1 fronts generate_forecast() with a 4-hour SQLite TTL cache
    # and background refresh (see CLAUDE.md "Data flow") — v2 doesn't have
    # that caching layer yet, so every dashboard load is a live (~seconds)
    # fetch. Fine for the beta, not fine at any real scale.
    loc_dict = build_dynamic_location(location.lat, location.lng)
    forecast = generate_forecast(location=loc_dict, profile=_profile_dict(profile))

    # Prefer the user's own label and v2's own location id over the ones
    # build_dynamic_location() invents for an anonymous lat/lng point.
    forecast["location_id"] = location.id
    forecast["location_name"] = location.label

    return _strip_internal_fields(forecast)
