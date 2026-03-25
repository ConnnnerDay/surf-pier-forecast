# Surf & Pier Fishing Forecast

A self-hosted Flask web app that combines NOAA/NWS/NDBC marine data with species logic, rig guidance, and personal fishing preferences to generate a practical surf & pier game plan.

## Highlights

- **Live fishing outlook dashboard** with conditions cards, confidence/verdict summary, and trend charts
- **Location-aware forecast engine** (wind, waves, tide windows, sunrise/sunset, solunar, pressure, weather)
- **851-species database** with ranked targets, natural bait picks, rig recommendations, knots, and spot tips
- **Fishing styles & profiles** — customize tactics by style (surf, pier, kayak, etc.) and personal preferences
- **Regulations coverage** — 2,700+ regulation entries across U.S. states and coasts
- **User accounts** with login/register, profile setup, favorites, catch logging, and photo uploads
- **Passkey support** (Face ID, Touch ID, Windows Hello) alongside password login
- **Google & Apple Sign In** (optional, requires OAuth credentials)
- **Email verification** — new accounts must confirm their email before accessing the app
- **Shareable forecast links** via `/f/<location_id>`
- **SQLite-backed caching + background refresh** for fast page loads and stale-while-refresh behavior
- **PWA/offline-ready** (manifest + service worker)
- **Security hardening**: CSRF, email verification gate, session versioning, passkeys, rate limiting, CSP headers

---

## Requirements

- Python **3.9+**
- Linux/macOS/WSL (Windows works too, but service instructions use systemd)
- No API keys required for core forecast features

Python packages (`requirements.txt`):

- `Flask` — web framework
- `Werkzeug` — WSGI utilities and password hashing
- `requests` — HTTP client for NWS/NOAA/NDBC calls
- `gunicorn` — production WSGI server
- `webauthn` — passkey / WebAuthn support
- `cryptography` — Apple Sign In JWT verification
- `beautifulsoup4` — regulation HTML parsing

---

## Quick start (local dev)

```bash
git clone https://github.com/ConnnnerDay/surf-pier-forecast.git
cd surf-pier-forecast

# Linux only: install venv support if missing
sudo apt-get update && sudo apt-get install -y python3-venv

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open: **http://localhost:5757**

The SQLite database (`data/app.db`) is created automatically on first startup.

If you're on macOS or Windows, skip the `apt-get` line and run the remaining commands in your terminal.

---

## One-click install (systemd)

```bash
./install.sh
```

What it does:
1. Installs system packages (`python3-venv`, `python3-pip`)
2. Creates `.venv` and installs dependencies
3. Initializes the SQLite database (`migrate_sqlite.py`)
4. Installs and starts `surf-forecast.service`
5. Enables auto-start on boot

If your distro does not include the Python `venv` module by default, install it first:

```bash
sudo apt-get update && sudo apt-get install -y python3-venv
./install.sh
```

Useful service commands:

```bash
sudo systemctl status surf-forecast
sudo systemctl restart surf-forecast
sudo journalctl -u surf-forecast -f
```

---

## Configuration

Copy `.env.example` to `.env` and set the values you need.  The app loads `.env`
automatically in development when `python-dotenv` is installed.  In production
the values are set via the systemd unit environment.

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | auto-generated | Flask session signing key. Set a fixed value in production so all gunicorn workers share it. |
| `PORT` | `5757` | Dev server port (`python app.py`). |
| `FLASK_DEBUG` | `0` | Set to `1` for debug mode. Never use in production. |
| `TRUSTED_PROXY` | _(unset)_ | Set to `1` when behind nginx/Caddy so IP-based rate limiting uses the real client IP from `X-Forwarded-For`. |
| `SESSION_COOKIE_SECURE` | _(unset)_ | Set to `1` to mark session cookies as `Secure` (HTTPS only). Required in production behind TLS. |
| `SMTP_HOST` | _(unset)_ | SMTP server for email verification. If unset, verification emails are skipped (accounts auto-confirmed). |
| `SMTP_PORT` | `587` | SMTP port. |
| `SMTP_USER` | _(unset)_ | SMTP username. |
| `SMTP_PASS` | _(unset)_ | SMTP password (app password for Gmail). |
| `SMTP_FROM` | _(unset)_ | From address for outgoing email. |
| `SMTP_USE_TLS` | `1` | Use STARTTLS for SMTP. |
| `GOOGLE_CLIENT_ID` | _(unset)_ | Enables "Sign in with Google". Requires a Google OAuth 2.0 client. |
| `GOOGLE_CLIENT_SECRET` | _(unset)_ | Google OAuth client secret. |
| `APPLE_CLIENT_ID` | _(unset)_ | Enables "Sign in with Apple". Requires an Apple Services ID. |
| `APPLE_TEAM_ID` | _(unset)_ | Apple Developer Team ID. |
| `APPLE_KEY_ID` | _(unset)_ | Apple Sign In key ID. |
| `APPLE_PRIVATE_KEY` | _(unset)_ | Apple Sign In ES256 private key (PEM, `\n`-escaped). |

---

## Core routes

### Pages

- `/` — dashboard
- `/setup` — location picker
- `/profile` — fishing profile setup
- `/account` — account/settings
- `/f/<location_id>` — shareable location forecast
- `/login`, `/register`, `/verify-email`
- `/sw.js` — service worker (root-scoped for full-site offline support)

### APIs

- `/api/forecast` (legacy JSON)
- `/api/v1/forecast`
- `/api/v1/forecast/<location_id>/status` — poll for cache freshness
- `/api/v1/profile`
- `/api/v1/log` — catch log entries
- `/api/v1/log/<entry_id>`
- `/api/v1/log/<entry_id>/photos` (POST)
- `/api/openapi.json`
- `/api/refresh` (POST)

---

## Data sources

- **NWS** — marine forecast, grid data, weather alerts
- **NOAA CO-OPS** — water temperature and tide predictions
- **NDBC** — buoy wave and wind observations
- **Astronomy math** — sunrise/sunset, moon phase, solunar timing

---

## Database & caching notes

SQLite DB: `data/app.db` (auto-created on first run)

Primary tables:
- `users` — accounts, hashed passwords, email verification, session versioning
- `profiles` — fishing preferences, theme, units, favorites
- `locations` — user default locations
- `forecasts` — historical forecast cache per location
- `forecast_cache` — per-user/location scoped cache
- `catch_log` — fishing log entries with optional photos
- `webauthn_credentials` — stored passkeys
- `social_accounts` — linked Google/Apple accounts
- `reg_scrape_cache` — regulation cache

Cache behavior:
- Dashboard serves stale cache immediately and enqueues a background refresh
- Missing cache is generated synchronously once, then reused
- Poll `/api/v1/forecast/<location_id>/status` for `last_generated_at`, `is_stale`, `is_refreshing`
- Background refresh uses a built-in daemon thread queue (no Redis required)

Initialize or re-initialize the database manually:

```bash
python migrate_sqlite.py
```

---

## Security behavior

- **Email verification** — accounts with an email address must confirm it before accessing any protected route
- **Passkeys** — WebAuthn/FIDO2 support for Face ID, Touch ID, and Windows Hello
- **Session versioning** — new logins increment a version counter; older sessions on other devices are invalidated
- **3-day session lifetime**
- **CSRF protection** — double-submit cookie for form POSTs; Origin/Referer validation for JSON requests
- **Password complexity** — 8+ characters, uppercase, lowercase, and a number required
- **Rate limiting** — login, registration, password reset, and API endpoints are rate-limited per IP
- **HTTP security headers** — CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options`, HSTS (over TLS), Referrer-Policy
- **Upload isolation** — user photos stored in `data/uploads/` (outside `static/`), served only through an auth-gated route

---

## Development

Install dev dependencies (linter, type checker, test runner):

```bash
pip install -r requirements-dev.txt
```

Run tests:

```bash
pytest -q
```

Run with coverage:

```bash
pytest --cov
```

Project layout:

```text
app.py                  # app factory + middleware
locations.py            # coastal location data
requirements.txt
requirements-dev.txt
install.sh
surf-forecast.service

/domain      # forecast + species domain logic
/services    # external data integrations (NWS/NOAA/NDBC/astro)
/storage     # SQLite DAL and cache layer
/web         # Flask blueprints (views / api / auth)
/templates   # Jinja2 pages and partials
/static      # CSS, JS, images, icons, PWA manifest
/tests       # pytest unit and integration tests
```

---

## Uninstall / delete everything

```bash
# From anywhere
PROJECT_DIR="/absolute/path/to/surf-pier-forecast"

# Stop + disable service (ignore errors if it was never installed)
sudo systemctl stop surf-forecast || true
sudo systemctl disable surf-forecast || true
sudo rm -f /etc/systemd/system/surf-forecast.service
sudo systemctl daemon-reload

# Remove project files (includes .venv and data/app.db)
rm -rf "$PROJECT_DIR"
```

> ⚠️ `rm -rf` is destructive. Double-check `PROJECT_DIR` before running.

---

## Troubleshooting

- **"This site can't be reached"** — the app is not running. Start it with `source .venv/bin/activate && python app.py`, or check the service with `sudo systemctl status surf-forecast`. If `.venv` doesn't exist yet, run `./install.sh` first.
- **Port in use** — set `PORT=8080 python app.py` to use a different port
- **Service not starting** — check `journalctl -u surf-forecast -n 100`
- **No forecast data** — verify internet access; upstream NOAA/NWS/NDBC endpoints may be temporarily unavailable
- **Auth form POST 400** — CSRF token missing/expired; refresh the page and retry
- **Email verification not sending** — set `SMTP_*` environment variables; without them, accounts auto-confirm and no emails are sent
