"""FastAPI application entry point."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Non-sensitive process health response."""

    status: Literal["ok"] = "ok"
    service: Literal["surf-pier-api"] = "surf-pier-api"


def create_app() -> FastAPI:
    """Create the forecast API application."""
    application = FastAPI(
        title="Surf & Pier Forecast API",
        version="0.0.0",
        docs_url=None,
        redoc_url=None,
    )

    @application.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def live() -> HealthResponse:
        return HealthResponse()

    @application.get("/health/ready", response_model=HealthResponse, tags=["health"])
    async def ready() -> HealthResponse:
        return HealthResponse()

    return application


app = create_app()
