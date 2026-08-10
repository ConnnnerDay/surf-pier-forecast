from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.profile import Profile
from app.models.user import User
from app.schemas.profile import (
    VALID_EXPERIENCE_LEVELS,
    VALID_THEMES,
    VALID_UNITS,
    ProfileOut,
    ProfileUpdate,
)

router = APIRouter(prefix="/profile", tags=["profile"])


def _get_or_404(db: Session, user: User) -> Profile:
    profile = db.query(Profile).filter_by(user_id=user.id).one_or_none()
    if profile is None:
        # Every signup creates a Profile row (see auth.py:signup) — this is
        # a data-integrity invariant, not a normal 404 a client can hit.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Profile missing"
        )
    return profile


@router.get("", response_model=ProfileOut)
def get_profile(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Profile:
    return _get_or_404(db, user)


@router.patch("", response_model=ProfileOut)
def update_profile(
    payload: ProfileUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Profile:
    profile = _get_or_404(db, user)

    if payload.experience_level is not None and payload.experience_level not in (
        VALID_EXPERIENCE_LEVELS
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"experience_level must be one of {sorted(VALID_EXPERIENCE_LEVELS)}",
        )
    if payload.units is not None and payload.units not in VALID_UNITS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"units must be one of {sorted(VALID_UNITS)}",
        )
    if payload.theme is not None and payload.theme not in VALID_THEMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"theme must be one of {sorted(VALID_THEMES)}",
        )

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(profile, field, value)

    db.commit()
    db.refresh(profile)
    return profile
