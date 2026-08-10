from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "Fishing Forecast v2"
    environment: str = "development"

    database_url: str = "sqlite:///./data/app.db"

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    cors_origins: list[str] = ["http://localhost:5173"]

    beta_allowlist_enabled: bool = True

    # Matches v1's 4-hour forecast cache TTL (see CLAUDE.md "Data flow").
    forecast_cache_ttl_minutes: int = 240

    # Optional OAuth — sign-in stays email/password-only until these are set,
    # matching v1's "no-op until configured" pattern for optional integrations.
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None

    # Apple ("Sign in with Apple") — client_id here is the Services ID.
    # Only the id_token verification path is implemented (see
    # app/api/routes/oauth.py), so no private key / client secret JWT
    # generation is needed for this login-only flow.
    apple_client_id: str | None = None
    apple_redirect_uri: str | None = None

    # Passkeys (WebAuthn) — unlike OAuth this needs no third-party
    # credentials, but rp_id/rp_origin MUST match the real domain in
    # production (WebAuthn ties credentials to the exact origin they were
    # registered on) or verification will fail.
    passkey_rp_id: str = "localhost"
    passkey_rp_name: str = "Fishing Forecast"
    passkey_rp_origin: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
