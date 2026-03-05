# Surf & Pier Fishing Forecast

A self-hosted Flask web app that combines NOAA/NWS/NDBC marine data with species logic, rig guidance, and personal fishing preferences to generate a practical surf & pier game plan.

## Highlights

- **Live fishing outlook dashboard** with conditions cards, confidence/verdict summary, and trend charts
- **Location-aware forecast engine** (wind, waves, tide windows, sunrise/sunset, solunar, pressure, weather)
- **Species + tactics guidance** (ranked target species, natural bait picks, rig recommendations, knots, and spot tips)
- **User accounts** with login/register, profile setup, favorites, catch logging, and photo uploads
- **Shareable forecast links** via `/f/<location_id>`
- **SQLite-backed caching + background refresh** for fast page loads and stale-while-refresh behavior
- **PWA/offline-ready setup** (manifest + service worker)
- **Security hardening**: CSRF protection, password complexity rules, and login rate limiting

---

## Requirements

- Python **3.9+**
- Linux/macOS/WSL (Windows works too, but service instructions use systemd)
- No API keys required

Python packages are listed in `requirements.txt`:

- `Flask`
- `requests`
- `Werkzeug`
- `gunicorn` (recommended for production)

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

If you're on macOS or Windows, skip the `apt-get` line and run the remaining commands in your terminal.

---

## One-click install (systemd)

```bash
./install.sh
```

What it does:
1. Creates `.venv`
2. Installs dependencies
3. Installs/starts `surf-forecast.service`
4. Enables auto-start on boot

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

Environment variables:

- `SECRET_KEY` (recommended in production)
- `PORT` (default: `5757`)

Example:

```bash
SECRET_KEY='change-me' PORT=8080 python app.py
```

---

## Core routes

### Pages

- `/` dashboard
- `/setup` location picker
- `/profile` fishing profile setup
- `/account` account/settings/dashboard
- `/f/<location_id>` shareable location forecast
- `/login`, `/register`
- `/sw.js` service worker endpoint (root-scoped)

### APIs

- `/api/forecast` (legacy JSON)
- `/api/v1/forecast`
- `/api/v1/forecast/<location_id>/status`
- `/api/v1/profile`
- `/api/v1/log`
- `/api/v1/log/<entry_id>`
- `/api/openapi.json`
- `/api/refresh` (POST)
- `/api/v1/log/<entry_id>/photos` (POST)

---

## Data sources

- **NWS** marine forecast/grid/weather
- **NOAA CO-OPS** water temperature + tide predictions
- **NDBC** buoy observations
- **Astronomy math** for sunrise/sunset + moon/solunar timing

---

## Database & caching notes

SQLite DB: `data/app.db`

Primary tables include:
- `users`
- `profiles`
- `locations`
- `forecasts` (historical)
- `forecast_cache` (user/location scoped cache)
- `catch_log`

Cache behavior:
- Forecasts are stored as a base cache per location for shared refresh jobs
- Dashboard serves stale cache immediately and enqueues a background refresh
- Missing cache is generated synchronously once, then reused
- Poll `/api/v1/forecast/<location_id>/status` for `last_generated_at`, `is_stale`, and `is_refreshing`
- Background refresh uses a built-in daemon thread queue (no Redis worker setup required for local dev)
- Legacy JSON cache files can still be read/migrated

Run migrations/init manually:

```bash
python migrate.py
python migrate_sqlite.py
```

---

## Security behavior

- Browser form POSTs require a valid CSRF token
- Registration enforces password complexity:
  - at least 8 chars
  - uppercase + lowercase + number
- Login attempts are rate-limited per session window

---

## Development

Run tests:

```bash
pytest -q
```

Project layout:

```text
app.py
locations.py
requirements.txt
install.sh
surf-forecast.service

/domain      # forecast + species domain logic
/services    # external data integrations (NWS/NOAA/NDBC/astro)
/storage     # sqlite/cache layers
/web         # Flask blueprints (views/api/auth)
/templates   # Jinja pages/partials
/static      # css/js/images/icons
/tests       # unit/integration tests
```

---

## Uninstall / delete everything

If you want to fully remove the app, service, virtual environment, and local database/cache files:

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

Optional cleanup:

```bash
# Remove systemd logs older than retention policy (optional)
sudo journalctl --vacuum-time=1s
```

> ⚠️ `rm -rf` is destructive. Double-check `PROJECT_DIR` before running.


---

## Troubleshooting

- **Port in use**: set `PORT=8080`
- **Service not starting**: check `journalctl -u surf-forecast -n 100`
- **No forecast data**: verify internet access; upstream NOAA/NWS/NDBC endpoints may be temporarily unavailable
- **Auth form POST 400**: CSRF token missing/invalid (refresh page and retry)
