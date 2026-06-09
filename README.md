# Surf & Pier Fishing Forecast

A self-hosted Flask web app that combines NOAA/NWS/NDBC marine data with species logic, rig guidance, and personal fishing preferences to generate a practical surf & pier fishing game plan.

## Highlights

- **Live fishing outlook dashboard** with conditions cards, a 0–100 go/no-go index, plain-language "why" explanation, and a day timeline that overlays feeding windows, tide states, and sunrise/sunset on the hourly activity chart
- **Personal comfort thresholds** — set a max wind and max surf you'll fish; the rating downgrades and flags any day that exceeds your limits
- **Location-aware forecast engine** — wind, waves, tide windows, sunrise/sunset, solunar, pressure, weather
- **851-species database** with ranked targets, natural bait picks, rig recommendations, knots, and spot tips
- **Fishing styles & profiles** — customize tactics by style (surf, pier, kayak, etc.) and personal preferences
- **Regulations coverage** — 2,700+ regulation entries across U.S. states and coasts
- **User accounts** — register, log in with a password, catch logging, favorites, profile setup
- **Shareable forecast links** via `/f/<location_id>`
- **SQLite-backed caching + background refresh** for fast page loads and stale-while-refresh behavior
- **PWA/offline-ready** (manifest + service worker)
- **Security hardening** — CSRF, session versioning, rate limiting, CSP headers

---

## Requirements

- Python **3.9+**
- Linux/macOS/WSL (Windows works, but service instructions use systemd)
- No API keys required for core forecast features

Python packages (`requirements.txt`):

- `Flask` — web framework
- `Werkzeug` — WSGI utilities and password hashing
- `requests` — HTTP client for NWS/NOAA/NDBC calls
- `gunicorn` — production WSGI server
- `beautifulsoup4` — regulation HTML parsing
- `python-dotenv` — loads `.env` in development

---

## Quick start (local dev)

```bash
git clone https://github.com/ConnnnerDay/surf-pier-forecast.git
cd surf-pier-forecast

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python app.py
```

Open: **http://localhost:5757**

The SQLite database (`data/app.db`) and `data/` directory are created automatically on first startup.

---

## One-click install (systemd)

```bash
./install.sh
```

What it does:
1. Installs system packages (`python3-venv`, `python3-pip`)
2. Creates `.venv` and installs all dependencies
3. Initializes the SQLite database (`migrate_sqlite.py`)
4. Installs and starts `surf-forecast.service`
5. Enables auto-start on boot

Useful service commands:

```bash
sudo systemctl status surf-forecast
sudo systemctl restart surf-forecast
sudo journalctl -u surf-forecast -f
```

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | auto-generated | Flask session signing key. Set a fixed value in production so all gunicorn workers share it. |
| `PORT` | `5757` | Dev server port. |
| `FLASK_DEBUG` | `0` | Set to `1` for debug mode. Never use in production. |
| `TRUSTED_PROXY` | _(unset)_ | Set to `1` when behind nginx/Caddy so rate limiting uses the real client IP. |
| `SESSION_COOKIE_SECURE` | _(unset)_ | Set to `1` to mark session cookies `Secure` (HTTPS only). Required in production. |
| `DEFAULT_LAT` / `DEFAULT_LNG` | Wrightsville Beach NC | Fallback coordinates when no location is set. |
| `ADMIN_USERS` | _(unset)_ | Comma-separated usernames granted admin access. |

---

## Core routes

### Pages

- `/` — dashboard
- `/setup` — location picker
- `/profile` — fishing profile setup
- `/account` — account/settings
- `/f/<location_id>` — shareable location forecast
- `/login`, `/register`

### APIs

- `/api/forecast` (legacy JSON)
- `/api/v1/forecast`
- `/api/v1/forecast/<location_id>/status` — poll for cache freshness
- `/api/v1/profile`
- `/api/v1/log` — catch log entries
- `/api/openapi.json`
- `/api/refresh` (POST)

---

## Data sources

- **NWS** — marine forecast, grid data, weather alerts
- **NOAA CO-OPS** — water temperature and tide predictions
- **NDBC** — buoy wave and wind observations
- **Astronomy math** — sunrise/sunset, moon phase, solunar timing

---

## Database & caching

SQLite DB: `data/app.db` (auto-created on first run)

Primary tables:
- `users` — accounts, hashed passwords, session versioning
- `profiles` — fishing preferences, theme, units, favorites
- `locations` — user default locations
- `forecast_cache` — cached forecast results (4-hour TTL)
- `catch_log` — fishing log entries
- `reg_scrape_cache` — regulation cache

Initialize or re-initialize the database:

```bash
python migrate_sqlite.py
```

Cache behavior:
- Dashboard serves stale cache immediately and enqueues a background refresh
- Missing cache is generated synchronously once, then reused
- Poll `/api/v1/forecast/<location_id>/status` for `last_generated_at`, `is_stale`, `is_refreshing`

---

## Security

- **Session versioning** — new logins increment a version counter; older sessions are invalidated
- **CSRF protection** — double-submit cookie for form POSTs; Origin/Referer validation for JSON requests
- **Password complexity** — 8+ characters, uppercase, lowercase, and a number required
- **Rate limiting** — login, registration, and API endpoints are rate-limited per IP
- **HTTP security headers** — CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options`, HSTS (over TLS)

---

## Development

```bash
# Install dev deps (linter, type checker, test runner)
pip install -r requirements-dev.txt

# Run all tests
pytest -q

# Single file / pattern
pytest tests/test_forecast.py
pytest -k "test_species" -v

# Lint
ruff check .
ruff format .
mypy .
```

Project layout:

```text
app.py                  # app factory + middleware
locations.py            # coastal location data
data/
  species_classifications.json  # species category/classification data
domain/                 # forecast + species domain logic
services/               # external data integrations (NWS/NOAA/NDBC/astro)
storage/                # SQLite DAL and species loader
web/                    # Flask blueprints (views / api / auth)
templates/              # Jinja2 pages and partials
static/                 # CSS, JS, images, icons, PWA manifest
tests/                  # pytest unit and integration tests
```

---

## Troubleshooting

- **"This site can't be reached"** — the app is not running. Start it with `source .venv/bin/activate && python app.py`, or check the service with `sudo systemctl status surf-forecast`.
- **Port in use** — set `PORT=8080 python app.py` to use a different port
- **Service not starting** — check `journalctl -u surf-forecast -n 100`
- **No forecast data** — verify internet access; NOAA/NWS/NDBC endpoints may be temporarily unavailable
- **Auth form POST 400** — CSRF token missing/expired; refresh the page and retry
