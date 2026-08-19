"""Canonical FastAPI entrypoint (R3 skeleton).

Versioned /v1 routes, the forecast domain port, and Postgres wiring land in
Phase 2 sprints (see docs/CANONICAL_ROADMAP.md). This file intentionally
only proves the app boots and exposes the two health endpoints the
canonical technical contract requires before any of that lands.
"""

from fastapi import FastAPI

app = FastAPI(title="Surf & Pier Forecast API", version="0.1.0")


@app.get("/health/live")
def health_live() -> dict[str, str]:
    """Process is up. No dependency checks — used for liveness probes."""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    """Ready to serve traffic. Will grow real dependency checks (database,
    upstream reachability) once this app has any to check — see sprint 10.
    """
    return {"status": "ok"}
