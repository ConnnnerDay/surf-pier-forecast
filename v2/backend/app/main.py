from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    account,
    auth,
    beta,
    forecast,
    health,
    locations,
    oauth,
    passkey,
    profile,
    regulations,
)
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(oauth.router)
    app.include_router(passkey.router)
    app.include_router(beta.router)
    app.include_router(locations.router)
    app.include_router(profile.router)
    app.include_router(forecast.router)
    app.include_router(regulations.router)
    app.include_router(account.router)

    return app


app = create_app()
