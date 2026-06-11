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
5. Result is cached to `forecast_cache` SQLite table; daemon threads handle async background refresh for stale entries and (when enabled) the opt-in notification poll

### Key files to reach for first

| File | Role |
|---|---|
| `app.py` | Application factory, blueprint registration, CSRF, security headers, session gate, background daemons (cache prune, notification poller) |
| `domain/forecast.py` | Main forecast assembly. `score_conditions()` is the 0-100 go/no-go index (+ explanation/threshold warnings); `classify_conditions()` is the label-only wrapper; `build_activity_timeline()` is the hourly model with sun/tide/feeding overlays |
| `domain/species.py` | 851-species DB, scoring, rig/bait recommendations (`build_rig_recommendations` is condition-aware), regulations |
| `domain/catch_insights.py` | Pure catch-log pattern analysis (`analyze_catch_patterns`) |
| `services/notifications.py` | Opt-in email/web-push alerts: `evaluate_forecast()` (pure rule), `run_notification_check()` (injectable runner), background poller |
| `services/push.py` | VAPID web-push send (no-op until configured; lazy `pywebpush`) |
| `storage/sqlite.py` | Full DB schema + all CRUD (users, profiles, forecasts, catch log w/ condition snapshot, push_subscriptions, notification_log, WebAuthn) |
| `web/api.py` | JSON API v1 blueprint (forecast, profile, log + `/log/patterns`, `/push/*`) |
| `web/auth.py` | Login, register, email verification, passkeys (WebAuthn), Google/Apple OAuth |
| `services/` | One file per external API (nws, noaa, ndbc, astro, etc.) |

### Blueprints

- `auth` — `/login`, `/register`, `/verify-email`, `/passkey/*`, `/oauth/*`
- `views` — HTML pages (dashboard, setup wizard, profile, shared forecast)
- `api` — `/api/v1/*` JSON endpoints

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

Optional — notifications (no-op until a user opts in *and* a channel is configured):

`NOTIFICATIONS_ENABLED` (default on; `0` disables the poller), `NOTIFICATION_POLL_INTERVAL` (seconds, default 900), `SITE_URL` (absolute base for alert links), `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`/`VAPID_SUBJECT` (web push; needs `pywebpush`). Email alerts reuse the SMTP vars above.

Optional — dev:

`PORT` (default 5757), `FLASK_DEBUG`, `DEFAULT_LAT`/`DEFAULT_LNG` (fallback coords, default Wrightsville Beach NC), `ADMIN_USERS` (comma-separated usernames)

### Storage layout

- `data/app.db` — SQLite (users, profiles, forecast cache, catch log w/ condition snapshot, push subscriptions, notification log, WebAuthn credentials, regulations cache)
- `data/uploads/` — catch-log photos, served only via auth-gated route (not from `static/`)

### Tests

Tests use pytest with isolated temporary SQLite DBs per test (fixtures in `conftest.py`). `pytest.ini` sets `testpaths = tests` and default flags `-v --tb=short`.
