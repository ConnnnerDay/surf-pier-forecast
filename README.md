# Surf & Pier Fishing Forecast

A self-hosted dashboard that generates a 24-hour surf and pier fishing outlook for **Wrightsville Beach & Carolina Beach, NC**.  All data comes from free public sources -- no API keys, no accounts, no subscriptions.

Once installed the dashboard runs as a background service that starts on boot. Open a browser, check the forecast, go fishing.

## What it shows

- **Fishability verdict** -- Fishable / Marginal / Not worth it based on wind and wave thresholds
- **Marine conditions** -- wind speed & direction, wave height, live water temperature (NOAA CO-OPS)
- **Tide schedule** -- high/low tide times and heights for the next 24 hours
- **Sunrise & sunset** -- computed from solar position math, no API needed
- **Moon phase & solunar rating** -- lunar illumination and a fishing-specific feeding activity rating (Excellent / Good / Fair / Poor)
- **Best fishing windows** -- tide changes overlapping dawn/dusk are flagged as Prime
- **Ranked species forecast** -- 15 species scored dynamically on water temperature, season, and solunar conditions
- **Bait recommendations** -- ranked by relevance to the top species
- **Bottom-rig templates** -- Carolina rig, double-dropper, pier structure rig, etc. with specs

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

Open **http://localhost:5757** in your browser.

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

The app fetches data from three free NOAA endpoints (no keys required):

| Data | Source | Update frequency |
|------|--------|-----------------|
| Wind & wave forecast | [NDBC FZUS52.KILM](https://www.ndbc.noaa.gov/data/Forecasts/FZUS52.KILM.html) | Every few hours |
| Water temperature | [NOAA CO-OPS Station 8658163](https://tidesandcurrents.noaa.gov/stationhome.html?id=8658163) | Every 6 minutes |
| Tide predictions | NOAA CO-OPS Predictions API | Pre-computed |

Sunrise/sunset and moon phase are calculated with pure math (NOAA solar algorithm and synodic month), no external API.

The dashboard auto-refreshes its cache every 4 hours on page load. You can also click **Refresh Forecast** at any time.

## API endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | HTML dashboard |
| `/api/forecast` | GET | Current forecast as JSON |
| `/api/refresh` | POST | Regenerate forecast and redirect to dashboard |

## Project structure

```
surf-pier-forecast/
  app.py                  # Flask app + all forecast logic
  requirements.txt        # Python dependencies (Flask, requests)
  install.sh              # One-command setup script
  surf-forecast.service   # systemd unit file template
  templates/
    index.html            # Dashboard template
    error.html            # Error page template
  static/
    style.css             # Dashboard styles
  data/
    forecast.json         # Cached forecast (auto-generated)
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

# Force a fresh forecast (delete cache)
rm data/forecast.json && sudo systemctl restart surf-forecast
```
