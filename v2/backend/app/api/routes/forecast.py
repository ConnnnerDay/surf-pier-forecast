from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.location import SavedLocation
from app.models.user import User

router = APIRouter(prefix="/forecast", tags=["forecast"])


@router.get("/{location_id}")
def get_forecast(
    location_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    location = db.query(SavedLocation).filter_by(id=location_id, user_id=user.id).one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

    # TODO(phase 2, docs/V2_PLAN.md §2/§7): wire this up to v1's
    # domain/forecast.py:generate_forecast() and domain/species.py once
    # those modules (plus services/nws.py, services/noaa.py,
    # services/ndbc.py, services/astro.py, services/stations.py, and
    # locations.py/regulations.py) are ported into this backend.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Forecast generation not yet ported from v1 — see docs/V2_PLAN.md phase 2",
    )
