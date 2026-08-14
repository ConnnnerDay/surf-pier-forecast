from datetime import date, datetime

from pydantic import BaseModel


class AccountExportLocation(BaseModel):
    id: str
    label: str
    lat: float
    lng: float
    is_default: bool

    model_config = {"from_attributes": True}


class AccountExportProfile(BaseModel):
    max_wind_mph: float | None
    max_surf_ft: float | None
    fishing_styles: list[str]
    gear_limitations: list[str]
    accessibility_needs: list[str]
    experience_level: str
    target_species: list[str]
    units: str
    theme: str
    onboarding_completed: bool

    model_config = {"from_attributes": True}


class AccountExportOut(BaseModel):
    id: str
    email: str
    date_of_birth: date | None
    created_at: datetime
    has_password: bool
    google_linked: bool
    apple_linked: bool
    totp_enabled: bool
    profile: AccountExportProfile | None
    locations: list[AccountExportLocation]


class AccountDeleteRequest(BaseModel):
    # Only required when the account has a password set (email/password
    # signup) — OAuth-only and passkey-only accounts have nothing to check
    # here, the bearer token already proves the current session owns the
    # account. See app/api/routes/account.py:delete_account.
    password: str | None = None
