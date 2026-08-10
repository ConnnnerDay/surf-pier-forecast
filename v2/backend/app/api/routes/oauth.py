import secrets
from typing import Annotated
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from jwt import PyJWKClient
from sqlalchemy.orm import Session

from app.core.auth_helpers import age_years, issue_tokens
from app.core.config import get_settings
from app.core.security import create_oauth_pending_token, decode_token
from app.db.session import get_db
from app.models.profile import Profile
from app.models.user import BetaAllowlistEntry, User
from app.schemas.auth import MIN_SIGNUP_AGE_YEARS, TokenPair
from app.schemas.oauth import (
    AppleCallbackRequest,
    GoogleCallbackRequest,
    OAuthCompleteSignupRequest,
    OAuthLoginResult,
    OAuthLoginURL,
)

router = APIRouter(prefix="/oauth", tags=["oauth"])

_GOOGLE_JWKS = PyJWKClient("https://www.googleapis.com/oauth2/v3/certs")
_APPLE_JWKS = PyJWKClient("https://appleid.apple.com/auth/keys")

_GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
_APPLE_ISSUERS = {"https://appleid.apple.com"}


def _verify_id_token(
    id_token: str, jwks: PyJWKClient, audience: str, allowed_issuers: set[str]
) -> dict:
    try:
        signing_key = jwks.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            options={"verify_iss": False},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid identity token"
        ) from exc

    if claims.get("iss") not in allowed_issuers:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid issuer")
    if not claims.get("email"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Identity provider did not share an email address",
        )
    return claims


def _login_or_start_signup(
    db: Session, provider: str, provider_sub: str, email: str
) -> OAuthLoginResult:
    field = "google_sub" if provider == "google" else "apple_sub"

    user = db.query(User).filter(getattr(User, field) == provider_sub).one_or_none()
    if user is None:
        # Same email, different (or no) sign-in method yet — link this
        # provider to the existing account rather than erroring or creating
        # a duplicate.
        user = db.query(User).filter_by(email=email).one_or_none()
        if user is not None:
            setattr(user, field, provider_sub)
            db.commit()

    if user is not None:
        tokens = issue_tokens(db, user, device_label=f"{provider} sign-in")
        return OAuthLoginResult(status="logged_in", tokens=tokens)

    pending = create_oauth_pending_token(provider, provider_sub, email)
    return OAuthLoginResult(status="needs_signup_info", pending_token=pending)


@router.get("/google/login", response_model=OAuthLoginURL)
def google_login_url() -> OAuthLoginURL:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Google sign-in isn't configured"
        )
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
    }
    return OAuthLoginURL(
        authorize_url=f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}",
        state=state,
    )


@router.post("/google/callback", response_model=OAuthLoginResult)
def google_callback(
    payload: GoogleCallbackRequest, db: Annotated[Session, Depends(get_db)]
) -> OAuthLoginResult:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Google sign-in isn't configured"
        )

    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "code": payload.code,
            "grant_type": "authorization_code",
            "redirect_uri": payload.redirect_uri or settings.google_redirect_uri,
        },
        timeout=10,
    )
    if token_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not verify Google sign-in"
        )
    id_token = token_resp.json().get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Google did not return an ID token"
        )

    claims = _verify_id_token(id_token, _GOOGLE_JWKS, settings.google_client_id, _GOOGLE_ISSUERS)
    return _login_or_start_signup(db, "google", claims["sub"], claims["email"])


@router.get("/apple/login", response_model=OAuthLoginURL)
def apple_login_url() -> OAuthLoginURL:
    settings = get_settings()
    if not settings.apple_client_id or not settings.apple_redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Apple sign-in isn't configured"
        )
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": settings.apple_client_id,
        "redirect_uri": settings.apple_redirect_uri,
        "response_type": "code id_token",
        "response_mode": "fragment",
        "scope": "email",
        "state": state,
    }
    return OAuthLoginURL(
        authorize_url=f"https://appleid.apple.com/auth/authorize?{urlencode(params)}",
        state=state,
    )


@router.post("/apple/callback", response_model=OAuthLoginResult)
def apple_callback(
    payload: AppleCallbackRequest, db: Annotated[Session, Depends(get_db)]
) -> OAuthLoginResult:
    settings = get_settings()
    if not settings.apple_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Apple sign-in isn't configured"
        )
    # Apple hands back a signed id_token directly (response_type "code
    # id_token"); verifying its signature is sufficient to trust the email —
    # no server-side token exchange (and therefore no ES256 client-secret
    # JWT) is needed for a login-only flow.
    claims = _verify_id_token(
        payload.id_token, _APPLE_JWKS, settings.apple_client_id, _APPLE_ISSUERS
    )
    return _login_or_start_signup(db, "apple", claims["sub"], claims["email"])


@router.post("/complete-signup", response_model=TokenPair)
def complete_oauth_signup(
    payload: OAuthCompleteSignupRequest, db: Annotated[Session, Depends(get_db)]
) -> TokenPair:
    try:
        claims = decode_token(payload.pending_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign-in link expired or invalid — start over",
        ) from exc
    if claims.get("type") != "oauth_pending":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if age_years(payload.date_of_birth) < MIN_SIGNUP_AGE_YEARS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You must be at least {MIN_SIGNUP_AGE_YEARS} to sign up",
        )

    email = claims["email"]
    allowlist_entry = db.query(BetaAllowlistEntry).filter_by(email=email, used=False).one_or_none()
    if allowlist_entry is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This email isn't on the private beta allowlist yet",
        )
    if db.query(User).filter_by(email=email).one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    provider = claims["provider"]
    field = "google_sub" if provider == "google" else "apple_sub"
    user = User(email=email, date_of_birth=payload.date_of_birth)
    setattr(user, field, claims["provider_sub"])
    db.add(user)
    db.flush()
    db.add(Profile(user_id=user.id))
    allowlist_entry.used = True
    db.commit()
    db.refresh(user)
    return issue_tokens(db, user, device_label=f"{provider} sign-in")
