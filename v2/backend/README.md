# v2 Backend

FastAPI backend for the v2 rewrite. See [`/docs/V2_PLAN.md`](../../docs/V2_PLAN.md)
for the full product/architecture spec this implements.

## Status

Full auth: email/password (beta-allowlist-gated, 13+ age gate), Google/Apple
OAuth (JWKS-verified `id_token`, with a DOB-collection step for first-time
OAuth signups so the age gate still applies), passkeys/WebAuthn
(discoverable/usernameless login, multiple credentials per user), optional
TOTP 2FA (enroll/confirm/disable), and login-alert emails on sign-in from a
new device. Saved locations (capped at 5, with rename/set-default/delete
via `GET`/`POST`/`PATCH`/`DELETE /locations`), a full profile API,
SQLAlchemy models, Alembic, and a large test suite (see `tests/`). Note:
v1 never actually had OAuth/WebAuthn routes despite CLAUDE.md describing
them — all of that is new engineering here, not a port.

`GET /forecast/{location_id}` is wired to v1's real forecast engine —
`domain/`, `services/`, `storage/`, `locations.py`, `regulations.py`, and
`utils.py` are copied verbatim into this directory (same top-level layout
as v1, so their internal imports needed zero changes) and called for real,
fronted by a 4-hour TTL cache (`ForecastCache` table, matching v1's cache
window — pass `?refresh=true` to force a live refetch). Those ported files
are excluded from this project's own ruff/mypy config (`pyproject.toml`)
since they carry v1's own lint/type debt — not this port's job to fix.
`storage/sqlite.py` here is a new, minimal shim (not copied from v1)
providing just the two small caches (`species_image_cache`,
`reg_scrape_cache`) that ported code still reads directly, kept in a
separate SQLite file from `app.db` so the schemas never collide.

**Known gaps:** no background refresh (a cache miss/expiry is still a
synchronous live fetch on the request that hits it — v1's
stale-serve-then-refresh-in-a-background-thread isn't replicated).

`GET /regulations/species`, `GET /regulations/lookup`, and
`POST /regulations/legal-catch` (`app/api/routes/regulations.py`) expose
`regulations.py`'s lookup/classification against a new legal-catch
calculator (`app/core/catch_calculator.py`) — given a regulation payload
and a measured fish length, it returns a
legal/too_small/too_large/cannot_target/unknown verdict. This is new
engineering, not a port: v1 exposes regulation text but never evaluated a
specific catch against it. Size-limit text is scraped/hand-compiled free
text, so parsing is deliberately conservative — ambiguous or
multi-region text (`"12 in TL in Gulf; 14 in TL in Atlantic"`) resolves
to `unknown` rather than guessing, and prohibited/out-of-season/
catch-and-release statuses short-circuit the size check entirely.

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
in any real deployment; the defaults are dev-only. See `.env.example` for
the full list, including the optional SMTP/OAuth/passkey settings (each
is a safe no-op — or works out of the box for passkeys — until configured).
