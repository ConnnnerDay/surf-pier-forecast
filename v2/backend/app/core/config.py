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


@lru_cache
def get_settings() -> Settings:
    return Settings()
