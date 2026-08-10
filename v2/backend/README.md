# v2 Backend

FastAPI backend for the v2 rewrite. See [`/docs/V2_PLAN.md`](../../docs/V2_PLAN.md)
for the full product/architecture spec this implements.

## Status

JWT auth (email/password, beta-allowlist-gated, 13+ age gate), saved
locations (capped at 5), SQLAlchemy models, Alembic, and a test suite.

`GET /forecast/{location_id}` is wired to v1's real forecast engine —
`domain/`, `services/`, `storage/`, `locations.py`, `regulations.py`, and
`utils.py` are copied verbatim into this directory (same top-level layout
as v1, so their internal imports needed zero changes) and called for real.
Those ported files are excluded from this project's own ruff/mypy config
(`pyproject.toml`) since they carry v1's own lint/type debt — not this
port's job to fix. `storage/sqlite.py` here is a new, minimal shim (not
copied from v1) providing just the two small caches
(`species_image_cache`, `reg_scrape_cache`) that ported code still reads
directly, kept in a separate SQLite file from `app.db` so the schemas
never collide.

**Known gaps:** no caching in front of `generate_forecast()` — v1 has a
4-hour TTL cache + background refresh (see root `CLAUDE.md` "Data flow")
that isn't ported, so every request is a live multi-second fetch. Also
still missing: Google/Apple OAuth, passkeys, 2FA enrollment, login-alert
emails, and a profile API (the model exists; the forecast route reads it
directly via SQLAlchemy for now, there's no way to edit it yet).

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
