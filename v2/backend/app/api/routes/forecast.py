from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.forecast_cache import ForecastCache
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


def _is_fresh(generated_at: datetime, ttl_minutes: int) -> bool:
    # SQLite doesn't reliably round-trip tzinfo through DateTime(timezone=True)
    # — normalize a naive value back to UTC (everything here is always
    # written as datetime.now(UTC)) rather than let the comparison below
    # raise on naive-vs-aware subtraction.
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - generated_at < timedelta(minutes=ttl_minutes)


@router.get("/{location_id}")
def get_forecast(
    location_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    refresh: bool = False,
) -> dict:
    location = db.query(SavedLocation).filter_by(id=location_id, user_id=user.id).one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

    ttl_minutes = get_settings().forecast_cache_ttl_minutes
    cached = db.get(ForecastCache, location_id)
    if cached is not None and not refresh and _is_fresh(cached.generated_at, ttl_minutes):
        forecast = dict(cached.forecast_json)
    else:
        profile = db.query(Profile).filter_by(user_id=user.id).one_or_none()
        loc_dict = build_dynamic_location(location.lat, location.lng)
        forecast = generate_forecast(location=loc_dict, profile=_profile_dict(profile))
        forecast = _strip_internal_fields(forecast)

        if cached is None:
            cached = ForecastCache(location_id=location_id)
            db.add(cached)
        cached.forecast_json = forecast
        cached.generated_at = datetime.now(UTC)
        db.commit()

    # Prefer the user's own (possibly since-renamed) label and v2's own
    # location id over the ones build_dynamic_location() invented for the
    # raw lat/lng — applied on every response, cached or not, so a label
    # change shows up immediately without needing a fresh forecast.
    forecast["location_id"] = location.id
    forecast["location_name"] = location.label

    return forecast
