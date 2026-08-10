from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.schemas.auth import TokenPair


class OAuthLoginURL(BaseModel):
    authorize_url: str
    state: str


class GoogleCallbackRequest(BaseModel):
    code: str
    redirect_uri: str | None = None


class AppleCallbackRequest(BaseModel):
    id_token: str


class OAuthLoginResult(BaseModel):
    status: Literal["logged_in", "needs_signup_info"]
    tokens: TokenPair | None = None
    # Only set when status == "needs_signup_info" — a first-time OAuth
    # sign-in whose identity is verified but still needs a date of birth
    # before an account can be created (the 13+ age gate applies to OAuth
    # signups exactly like password signups; neither Google nor Apple
    # reliably hand back a birthdate).
    pending_token: str | None = None


class OAuthCompleteSignupRequest(BaseModel):
    pending_token: str
    date_of_birth: date
