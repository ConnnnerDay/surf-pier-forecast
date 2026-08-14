from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import verify_password
from app.db.session import get_db
from app.models.forecast_cache import ForecastCache
from app.models.location import SavedLocation
from app.models.passkey import PasskeyCredential, WebAuthnChallenge
from app.models.profile import Profile
from app.models.user import RefreshToken, User
from app.schemas.account import (
    AccountDeleteRequest,
    AccountExportLocation,
    AccountExportOut,
    AccountExportProfile,
)

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/export", response_model=AccountExportOut)
def export_account(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AccountExportOut:
    """Everything stored about this account, for the self-service data
    export required by docs/V2_PLAN.md §6 MVP scope. Excludes security
    material (password hash, TOTP secret, refresh tokens, passkey public
    keys) — this is a portability export, not a security audit log."""
    profile = db.query(Profile).filter_by(user_id=user.id).one_or_none()
    locations = db.query(SavedLocation).filter_by(user_id=user.id).all()
    return AccountExportOut(
        id=user.id,
        email=user.email,
        date_of_birth=user.date_of_birth,
        created_at=user.created_at,
        has_password=user.has_password,
        google_linked=user.google_sub is not None,
        apple_linked=user.apple_sub is not None,
        totp_enabled=user.totp_enabled,
        profile=AccountExportProfile.model_validate(profile) if profile else None,
        locations=[AccountExportLocation.model_validate(loc) for loc in locations],
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    payload: AccountDeleteRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Permanently deletes the account and everything tied to it. Once the
    User row is gone, get_current_user's DB lookup fails for any token still
    in circulation — no separate session-revocation step is needed."""
    if user.password_hash is not None:
        if payload.password is None or not verify_password(payload.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password"
            )

    location_ids = [loc.id for loc in db.query(SavedLocation).filter_by(user_id=user.id).all()]
    if location_ids:
        db.query(ForecastCache).filter(ForecastCache.location_id.in_(location_ids)).delete(
            synchronize_session=False
        )

    db.query(RefreshToken).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.query(PasskeyCredential).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.query(WebAuthnChallenge).filter_by(user_id=user.id).delete(synchronize_session=False)

    # SavedLocation and Profile cascade off the User relationship
    # (cascade="all, delete-orphan" in app/models/user.py).
    db.delete(user)
    db.commit()
