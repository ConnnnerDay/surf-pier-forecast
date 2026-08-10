from typing import Annotated

import jwt
import pyotp
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.auth_helpers import age_years, hash_token, issue_tokens
from app.core.security import decode_token, hash_password, verify_password
from app.db.session import get_db
from app.models.profile import Profile
from app.models.user import BetaAllowlistEntry, RefreshToken, User
from app.schemas.auth import (
    MIN_SIGNUP_AGE_YEARS,
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenPair,
    TwoFactorConfirmRequest,
    TwoFactorDisableRequest,
    TwoFactorEnrollResponse,
)
from app.schemas.user import UserOut
from services.email import send_email

router = APIRouter(prefix="/auth", tags=["auth"])


def _is_new_device(db: Session, user: User, device_label: str | None) -> bool:
    """True when `device_label` has never been recorded for this user before
    (across all of their refresh tokens, revoked or not) — used to decide
    whether a login is worth a "new device" alert email. A missing label
    can't be told apart from any other login, so it never counts as new."""
    if not device_label:
        return False
    seen_before = (
        db.query(RefreshToken).filter_by(user_id=user.id, device_label=device_label).first()
    )
    return seen_before is None


def _send_login_alert(to_email: str, device_label: str | None) -> None:
    device = device_label or "an unrecognized device"
    send_email(
        to=to_email,
        subject="New sign-in to your Fishing Forecast account",
        body_text=(
            f"Your account was just signed into from {device}.\n\n"
            "If this was you, no action is needed. If it wasn't, change your "
            "password and consider enabling two-factor authentication."
        ),
    )


@router.post("/signup", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Annotated[Session, Depends(get_db)]) -> TokenPair:
    if age_years(payload.date_of_birth) < MIN_SIGNUP_AGE_YEARS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You must be at least {MIN_SIGNUP_AGE_YEARS} to sign up",
        )

    allowlist_entry = (
        db.query(BetaAllowlistEntry).filter_by(email=payload.email, used=False).one_or_none()
    )
    if allowlist_entry is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This email isn't on the private beta allowlist yet",
        )

    if db.query(User).filter_by(email=payload.email).one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        date_of_birth=payload.date_of_birth,
    )
    db.add(user)
    db.flush()
    db.add(Profile(user_id=user.id))
    allowlist_entry.used = True
    db.commit()
    db.refresh(user)

    return issue_tokens(db, user, device_label=None)


@router.post("/login", response_model=TokenPair)
def login(
    payload: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
) -> TokenPair:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
    )
    user = db.query(User).filter_by(email=payload.email).one_or_none()
    if user is None or user.password_hash is None:
        raise unauthorized
    if not verify_password(payload.password, user.password_hash):
        raise unauthorized

    if user.totp_enabled:
        if not payload.totp_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="TOTP code required"
            )
        assert user.totp_secret is not None  # invariant: set whenever totp_enabled is True
        totp = pyotp.TOTP(user.totp_secret)
        if not totp.verify(payload.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid TOTP code"
            )

    if _is_new_device(db, user, payload.device_label):
        # Backgrounded so a slow/unconfigured SMTP server never delays the
        # login response itself.
        background_tasks.add_task(_send_login_alert, user.email, payload.device_label)

    return issue_tokens(db, user, device_label=payload.device_label)


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, db: Annotated[Session, Depends(get_db)]) -> TokenPair:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
    )
    try:
        claims = decode_token(payload.refresh_token)
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    if claims.get("type") != "refresh":
        raise unauthorized

    token_row = (
        db.query(RefreshToken)
        .filter_by(token_hash=hash_token(payload.refresh_token), revoked=False)
        .one_or_none()
    )
    if token_row is None:
        raise unauthorized

    user = db.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise unauthorized

    token_row.revoked = True
    db.commit()
    return issue_tokens(db, user, device_label=token_row.device_label)


@router.get("/me", response_model=UserOut)
def me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user


@router.post("/2fa/enroll", response_model=TwoFactorEnrollResponse)
def enroll_2fa(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TwoFactorEnrollResponse:
    """Generate a new TOTP secret and store it *unconfirmed* — totp_enabled
    stays False (so login doesn't start demanding a code) until the user
    proves they've actually got it in an authenticator app via /2fa/confirm.
    Calling this again before confirming replaces the pending secret."""
    secret = pyotp.random_base32()
    user.totp_secret = secret
    user.totp_enabled = False
    db.commit()

    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Fishing Forecast")
    return TwoFactorEnrollResponse(secret=secret, provisioning_uri=uri)


@router.post("/2fa/confirm", response_model=UserOut)
def confirm_2fa(
    payload: TwoFactorConfirmRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if not user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Call /2fa/enroll first"
        )
    if not pyotp.TOTP(user.totp_secret).verify(payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid code")

    user.totp_enabled = True
    db.commit()
    db.refresh(user)
    return user


@router.post("/2fa/disable", response_model=UserOut)
def disable_2fa(
    payload: TwoFactorDisableRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if user.password_hash is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")

    user.totp_enabled = False
    user.totp_secret = None
    db.commit()
    db.refresh(user)
    return user
