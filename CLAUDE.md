# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
pip install -r requirements-dev.txt   # install all deps (includes runtime)
python migrate_sqlite.py               # initialize SQLite schema

# Run dev server (default port 5757)
python app.py
PORT=8080 python app.py

# Test
pytest -q                             # all tests
pytest tests/test_forecast.py         # single file
pytest tests/test_app.py::TestAppFactory::test_creates_flask_app -v  # single test
pytest -k "test_email" -v             # pattern match

# Lint / type check
ruff check .
ruff format .
mypy .
```

## Architecture

This is a self-hosted Flask fishing forecast app. It fetches live marine data from free public APIs (no keys required), scores a 851-species database against current conditions, and returns fishing outlooks with rig/bait recommendations.

### Data flow

1. Request hits `/` (dashboard) or `/f/<location_id>` (shareable link)
2. `web/views.py` resolves location from session → default → setup wizard redirect
3. `domain/forecast.py:generate_forecast()` checks the SQLite cache (4-hour TTL); on miss, fires concurrent fetches via `ThreadPoolExecutor` (30 workers) against:
   - NWS (marine zone text, grid data, alerts)
   - NOAA CO-OPS (water temp, tides/currents)
   - NDBC buoys (wave height, wind)
   - Astronomy service (sunrise/sunset, moon phase, solunar)
4. Species scoring in `domain/species.py` ranks the 851-species DB by water temp, season, solunar, wind/wave conditions, then filters by user profile (offshore vs inshore) and regulations
5. Result is cached to `forecast_cache` SQLite table; a daemon thread handles async background refresh for stale entries
6. `web/geo_api.py` serves additional geospatial overlays (satellite imagery, coastlines, amenities) as separate JSON endpoints loaded client-side

### Key files to reach for first

| File | Role |
|---|---|
| `app.py` | Application factory, blueprint registration, CSRF, security headers, session gate |
| `domain/forecast.py` | Main forecast assembly — start here for any forecast logic |
| `domain/species.py` | 851-species DB, scoring, rig/bait recommendations, regulations |
| `storage/sqlite.py` | Full DB schema + all CRUD (users, profiles, forecasts, catch log, WebAuthn) |
| `web/api.py` | JSON API v1 blueprint |
| `web/auth.py` | Login, register, email verification, passkeys (WebAuthn), Google/Apple OAuth |
| `web/geo_api.py` | Geospatial/satellite overlay endpoints |
| `services/` | One file per external API (nws, noaa, ndbc, astro, etc.) |

### Blueprints

- `auth` — `/login`, `/register`, `/verify-email`, `/passkey/*`, `/oauth/*`
- `views` — HTML pages (dashboard, setup wizard, profile, shared forecast)
- `api` — `/api/v1/*` JSON endpoints
- `geo_api` — `/api/geo/*` geospatial endpoints

### Environment variables

Required in production:

| Variable | Notes |
|---|---|
| `SECRET_KEY` | Flask session key; auto-generated to `data/secret_key` if unset |
| `SESSION_COOKIE_SECURE=1` | Set when running behind TLS |
| `TRUSTED_PROXY=1` | Set when behind nginx/Caddy so rate limiting uses real client IP |

Optional — SMTP (if unset, accounts auto-confirm and no emails are sent):

`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, `SMTP_USE_TLS`

Optional — OAuth:

`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY`

Optional — dev:

`PORT` (default 5757), `FLASK_DEBUG`, `DEFAULT_LAT`/`DEFAULT_LNG` (fallback coords, default Wrightsville Beach NC), `ADMIN_USERS` (comma-separated usernames)

### Storage layout

- `data/app.db` — SQLite (users, profiles, forecast cache, catch log, WebAuthn credentials, regulations cache)
- `data/uploads/` — catch-log photos, served only via auth-gated route (not from `static/`)
- `data/natural_earth/` — coastline GeoJSON (optional; downloaded on first use if geopandas is available)

### Tests

Tests use pytest with isolated temporary SQLite DBs per test (fixtures in `conftest.py`). 23 test files cover API endpoints, auth flows, forecast generation, species scoring, caching, and geospatial services. `pytest.ini` sets `testpaths = tests` and default flags `-v --tb=short`.
