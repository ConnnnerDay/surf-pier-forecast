# v2 Backend

FastAPI backend for the v2 rewrite. See [`/docs/V2_PLAN.md`](../../docs/V2_PLAN.md)
for the full product/architecture spec this implements.

## Status

Phase 1 scaffold: JWT auth (email/password, beta-allowlist-gated, 13+ age
gate), saved locations (capped at 5), SQLAlchemy models, Alembic, and a
test suite. **Not yet wired up:** the actual forecast engine — v1's
`domain/forecast.py`, `domain/species.py`, `services/*.py`, `locations.py`,
and `regulations.py` still need to be ported in (see the `TODO(phase 2)`
comment in `app/api/routes/forecast.py`), along with Google/Apple OAuth,
passkeys, 2FA enrollment, and the profile API.

## Setup

```bash
cd v2/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# create the SQLite schema
alembic upgrade head

uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000/docs** for the interactive API docs.

## Test / lint

```bash
pytest -q
ruff check .
mypy app
```

## Config

Copy `.env.example` to `.env` (or export env vars directly) to override
`app/core/config.py:Settings` — notably `database_url` and `jwt_secret`
in any real deployment; the defaults are dev-only.
