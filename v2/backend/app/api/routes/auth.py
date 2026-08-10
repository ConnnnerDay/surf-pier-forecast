import hashlib
from datetime import date
from typing import Annotated

import jwt
import pyotp
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models.profile import Profile
from app.models.user import BetaAllowlistEntry, RefreshToken, User
from app.schemas.auth import (
    MIN_SIGNUP_AGE_YEARS,
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenPair,
)
from app.schemas.user import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _age_years(dob: date) -> int:
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _issue_tokens(db: Session, user: User, device_label: str | None) -> TokenPair:
    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(refresh),
            device_label=device_label,
        )
    )
    db.commit()
    # TODO(phase 2): send a login-alert email when this is a new device,
    # per the "stricter account security" decision in docs/V2_PLAN.md.
    return TokenPair(access_token=access, refresh_token=refresh)


@router.post("/signup", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Annotated[Session, Depends(get_db)]) -> TokenPair:
    if _age_years(payload.date_of_birth) < MIN_SIGNUP_AGE_YEARS:
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

    return _issue_tokens(db, user, device_label=None)


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> TokenPair:
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

    return _issue_tokens(db, user, device_label=None)


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
        .filter_by(token_hash=_hash_token(payload.refresh_token), revoked=False)
        .one_or_none()
    )
    if token_row is None:
        raise unauthorized

    user = db.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise unauthorized

    token_row.revoked = True
    db.commit()
    return _issue_tokens(db, user, device_label=token_row.device_label)


@router.get("/me", response_model=UserOut)
def me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user
