# Surf & Pier Fishing Forecast

A Flask web app that combines NOAA/NWS/NDBC marine data with species logic, rig guidance, and personal fishing preferences to generate a practical surf & pier game plan.

## Features

- **Live fishing outlook dashboard** with conditions cards, confidence/verdict summary, and trend charts
- **Location-aware forecast engine** (wind, waves, tide windows, sunrise/sunset, solunar, pressure, weather)
- **Species + tactics guidance** (ranked target species, natural bait picks, rig recommendations, knots, and spot tips)
- **User accounts** with login/register, profile setup, favorites, catch logging, and photo uploads
- **Shareable forecast links** via `/f/<location_id>`
- **SQLite-backed caching + background refresh** for fast page loads and stale-while-refresh behavior
- **PWA/offline-ready** (manifest + service worker)

---

## Requirements

- Python **3.9+**
- No API keys required

---

## Local Setup

```bash
git clone https://github.com/ConnnnerDay/surf-pier-forecast.git && cd surf-pier-forecast && ./install.sh
```

`install.sh` handles everything: system packages, Python venv, pip dependencies, DB init, migrations, and starts the app.

Open: **http://localhost:5757**

To restart later:
```bash
.venv/bin/python app.py
```

---

## Database Init

On first run the database is created automatically. If you need to run migrations manually:

```bash
python migrate.py
python migrate_sqlite.py
```

SQLite DB is stored at `data/app.db`.

---

## Configuration

Set environment variables as needed before running:

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5757` | Port to listen on |
| `SECRET_KEY` | `dev-key-change-in-production` | Flask session secret |

Example:

```bash
PORT=8080 python app.py
```

---

## Running Tests

```bash
pytest -q
```

---

## Project Layout

```
app.py               # App factory + entry point
locations.py         # Coastal location definitions
requirements.txt
migrate.py / migrate_sqlite.py

/domain      # Forecast + species domain logic
/services    # External data integrations (NWS/NOAA/NDBC/astro)
/storage     # SQLite + cache layers
/web         # Flask blueprints (views/api/auth)
/templates   # Jinja pages/partials
/static      # CSS/JS/images/icons
/tests       # Unit + integration tests
```

---

## Routes

### Pages

| Route | Description |
|---|---|
| `/` | Dashboard |
| `/setup` | Location picker |
| `/profile` | Fishing profile setup |
| `/account` | Account settings |
| `/f/<location_id>` | Shareable forecast link |
| `/login`, `/register` | Auth pages |

### API

| Route | Description |
|---|---|
| `/api/v1/forecast` | Current forecast (JSON) |
| `/api/v1/forecast/<location_id>/status` | Cache status (`last_generated_at`, `is_stale`, `is_refreshing`) |
| `/api/v1/profile` | User profile |
| `/api/v1/log` | Catch log list |
| `/api/v1/log/<entry_id>` | Single log entry |
| `/api/v1/log/<entry_id>/photos` | Photo upload (POST) |
| `/api/refresh` | Force forecast refresh (POST) |
| `/api/openapi.json` | OpenAPI spec |

---

## Data Sources

- **NWS** — marine forecast/grid/weather
- **NOAA CO-OPS** — water temperature + tide predictions
- **NDBC** — buoy observations
- **Astronomy math** — sunrise/sunset + moon/solunar timing

---

## Troubleshooting

- **Port already in use**: `PORT=8080 python app.py`
- **No forecast data**: check your internet connection; NOAA/NWS/NDBC endpoints may be temporarily unavailable
- **Form POST returns 400**: CSRF token missing — refresh the page and try again
