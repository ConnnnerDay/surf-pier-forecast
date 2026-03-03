# Surf & Pier Fishing Forecast

A self-hosted Flask dashboard that builds an actionable surf/pier fishing forecast from NOAA/NWS/NDBC data, then personalizes targets/rigs/bait by user profile.

## Highlights

- **Location-aware forecast engine** (winds, waves, tide state, sunrise/sunset, solunar, pressure, weather)
- **User-scoped forecast cache** in SQLite (`forecast_cache`) with stale invalidation
- **Authentication + account settings** (login/register/account)
- **Security hardening**: CSRF protection on browser form POSTs, password complexity, login rate limiting
- **Fishing workflow tools**: profile setup, favorites, catch log, share links, quick refresh
- **Responsive UI** with shared nav and interactive charts

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

## Quick start

```bash
git clone https://github.com/ConnnnerDay/surf-pier-forecast.git
cd surf-pier-forecast
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open: **http://localhost:5757**

---

## One-command install (systemd)

```bash
./install.sh
```

What it does:
1. Creates `.venv`
2. Installs dependencies
3. Installs/starts `surf-forecast.service`
4. Enables auto-start on boot

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

### APIs

- `/api/forecast` (legacy JSON)
- `/api/v1/forecast`
- `/api/v1/profile`
- `/api/v1/log`
- `/api/v1/log/<entry_id>`
- `/api/openapi.json`
- `/api/refresh` (POST)

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
- Forecasts are keyed by **user + location** when available
- Stale entries are invalidated automatically
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

## Troubleshooting

- **Port in use**: set `PORT=8080`
- **Service not starting**: check `journalctl -u surf-forecast -n 100`
- **No forecast data**: verify internet access; upstream NOAA/NWS/NDBC endpoints may be temporarily unavailable
- **Auth form POST 400**: CSRF token missing/invalid (refresh page and retry)

