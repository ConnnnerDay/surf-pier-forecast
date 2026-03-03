# Surf & Pier Fishing Forecast

A self-hosted dashboard that generates a 24-hour surf and pier fishing outlook for **100+ coastal locations** across the US East Coast and Gulf.  All data comes from free public sources -- no API keys, no accounts, no subscriptions.

On first visit users pick their location by zip code or by browsing the full list. Once installed the dashboard runs as a background service that starts on boot. Open a browser, check the forecast, go fishing.

## What it shows

- **Fishability verdict** -- Fishable / Marginal / Not worth it based on wind and wave thresholds
- **Marine conditions** -- wind speed & direction, wave height, live water temperature (NOAA CO-OPS)
- **Tide schedule** -- high/low tide times, heights, and an interactive tide chart
- **Sunrise & sunset** -- computed from solar position math, no API needed
- **Moon phase & solunar rating** -- lunar illumination and a fishing-specific feeding activity rating (Excellent / Good / Fair / Poor)
- **Best fishing windows** -- tide changes overlapping dawn/dusk are flagged as Prime, with a 24-hour activity timeline
- **Ranked species forecast** -- species scored dynamically on water temperature, season, solunar conditions, and user profile
- **Bait & rig recommendations** -- ranked by relevance to the top species, with full rig diagrams and knot instructions
- **3-day outlook** -- upcoming conditions so you can plan ahead
- **Fishing log** -- track your catches locally with stats and personal bests
- **Personalized profiles** -- set your fishing style (surf, pier, inshore) and bait preferences for tailored results

## Quick start (one command)

Requires **Python 3.9+** and a Linux system with systemd (Ubuntu, Debian, Raspberry Pi OS, etc.).

```bash
git clone https://github.com/ConnnnerDay/surf-pier-forecast.git
cd surf-pier-forecast
./install.sh
```

That's it. The install script:

1. Creates a Python virtual environment and installs dependencies
2. Installs a systemd service that starts on boot
3. Starts the dashboard immediately

Open **http://localhost:5757** in your browser. You'll be prompted to pick your fishing location on first visit.

To access from your phone or another device on the same Wi-Fi, use your machine's local IP (the install script prints it):

```
http://192.168.x.x:5757
```

## Manual setup

If you prefer to set things up by hand or aren't on a systemd-based Linux system.

### 1. Clone and install

```bash
git clone https://github.com/ConnnnerDay/surf-pier-forecast.git
cd surf-pier-forecast
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run (foreground)

```bash
python app.py
```

Dashboard is at **http://localhost:5757**.  Press Ctrl+C to stop.

### 3. Run as a service (always-on)

The repo includes a systemd unit file template. Install it:

```bash
# Fill in your username and project path
sed -e "s|REPLACE_USER|$(whoami)|g" \
    -e "s|REPLACE_DIR|$(pwd)|g" \
    surf-forecast.service > /tmp/surf-forecast.service

sudo cp /tmp/surf-forecast.service /etc/systemd/system/surf-forecast.service
sudo systemctl daemon-reload
sudo systemctl enable surf-forecast.service
sudo systemctl start surf-forecast.service
```

The dashboard now starts automatically on every boot. Check status:

```bash
sudo systemctl status surf-forecast
```

View logs:

```bash
sudo journalctl -u surf-forecast -f
```

### 4. Change the port

Set the `PORT` environment variable before running:

```bash
PORT=8080 python app.py
```

Or for the systemd service, edit `/etc/systemd/system/surf-forecast.service` and add under `[Service]`:

```ini
Environment=PORT=8080
```

Then `sudo systemctl daemon-reload && sudo systemctl restart surf-forecast`.

## Running on a Raspberry Pi

This project is lightweight and runs well on a Raspberry Pi (any model with network access). A Pi on your home network makes a great always-on fishing dashboard.

```bash
# On a fresh Raspberry Pi OS install:
sudo apt update && sudo apt install -y python3 python3-venv git
git clone https://github.com/ConnnnerDay/surf-pier-forecast.git
cd surf-pier-forecast
./install.sh
```

Access from any device on your network at `http://<pi-ip>:5757`.

## How it works

The app fetches data from free NOAA endpoints (no keys required), selecting the correct stations for each location:

| Data | Source | Update frequency |
|------|--------|-----------------|
| Wind & wave forecast | NWS Marine Zone Forecast | Every few hours |
| Water temperature | NOAA CO-OPS Tides & Currents | Every 6 minutes |
| Tide predictions | NOAA CO-OPS Predictions API | Pre-computed |
| Buoy observations | NDBC Real-time Data | Hourly |

Sunrise/sunset and moon phase are calculated with pure math (NOAA solar algorithm and synodic month), no external API.

Each location in the database has its own NWS zone, NOAA station, and NDBC buoy IDs so all data is location-specific.

The dashboard auto-refreshes its cache every 4 hours on page load. You can also click **Refresh Forecast** at any time.

Account/tier scaffolding is built in: users have `free` or `paid` flags, with feature gates (saved logs, alerts, extended outlook, and max saved locations) enforced in backend logic.

## API endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | HTML dashboard (redirects to setup if no location set) |
| `/setup` | GET | Location picker (zip search + browse) |
| `/profile` | GET | Fishing profile setup |
| `/f/<location_id>` | GET | Shareable forecast link for a specific location |
| `/api/forecast` | GET | Legacy forecast JSON (supports `location_id` and `force_refresh=true`) |
| `/api/v1/forecast` | GET | Versioned forecast envelope (`ok/data/error/meta`) |
| `/api/v1/profile` | GET/POST | Versioned profile/preferences API |
| `/api/v1/log` | GET/POST | Versioned catch log + stats API |
| `/api/v1/log/<entry_id>` | DELETE | Delete catch log entry |
| `/api/openapi.json` | GET | OpenAPI 3.0 spec (also at `/api/v1/openapi.json`) |
| `/api/refresh` | POST | Regenerate forecast and redirect to dashboard |

## Project structure

```
surf-pier-forecast/
  app.py                  # Flask application factory & entry point
  locations.py            # 100+ location database with station IDs
  regulations.py          # Fishing regulations by species and state
  requirements.txt        # Python dependencies (Flask, requests)
  install.sh              # One-command setup script
  surf-forecast.service   # systemd unit file template
  domain/
    forecast.py           # Forecast assembly, conditions analysis
    species.py            # Species database, scoring logic, bait/rig data
  services/
    astro.py              # Sunrise/sunset, moon phase, solunar times
    ndbc.py               # NDBC buoy observations
    noaa.py               # NOAA CO-OPS water temp & tides
    nws.py                # NWS marine zone forecast parsing
  storage/
    cache.py              # DB-first forecast cache with JSON fallback
    sqlite.py             # SQLite data-access layer (users/profiles/locations/forecasts/catch_log)
    db.py                 # Backwards-compatible alias to sqlite.py
  web/
    auth.py               # Login, register, logout, account routes
    api.py                # JSON API routes (preferences, log, forecast)
    views.py              # Dashboard, setup, profile page routes
    helpers.py            # Shared session/location helpers
  templates/
    index.html            # Forecast dashboard
    setup.html            # Location picker
    profile.html          # Fishing profile setup
    login.html            # Login page
    register.html         # Registration page
    account.html          # Account settings
    error.html            # Error page
  static/
    style.css             # Dashboard styles
    images/rigs/          # Rig diagram SVGs
  tests/
    test_app.py           # App factory & route tests
    test_cache.py         # Forecast caching tests
    test_forecast.py      # Conditions & seasonal data tests
    test_species.py       # Species scoring & ranking tests
  data/
    forecast_*.json       # Legacy fallback cache files (auto-generated)
    app.db                # SQLite database (auto-generated)
```

## Useful commands

```bash
# Check if service is running
sudo systemctl status surf-forecast

# Restart after code changes
sudo systemctl restart surf-forecast

# Stop the service
sudo systemctl stop surf-forecast

# Disable auto-start on boot
sudo systemctl disable surf-forecast

# View live logs
sudo journalctl -u surf-forecast -f

# Run SQLite schema + migration
python migrate_sqlite.py

# Force a fresh forecast (clear DB cache for one location using sqlite3)
sqlite3 data/app.db "DELETE FROM forecasts WHERE location_id = 'wrightsville-beach-nc';"
```
