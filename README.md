# Surf & Pier Fishing Forecast for Wrightsville and Carolina Beach

This project is a self‑contained Python application that generates and serves a 24‑hour surf and pier fishing outlook for **Wrightsville Beach and Carolina Beach, North Carolina**.  It combines official marine conditions with seasonal fishing patterns to rank species, recommend natural baits and bottom rigs, classify fishability, and present the results on a simple web dashboard.

## Features

* **Real‑time forecast generation** – fetches the latest National Weather Service marine forecast for the local zones and parses wind and wave ranges for the next 24 hours.
* **Species ranking** – identifies up to ten species most likely to bite based on winter seasonality, official North Carolina species profiles and current conditions.
* **Rig and bait recommendations** – suggests natural bait (no lures) and bottom‑rig setups with hook and sinker sizes, including when live bait offers an advantage.
* **Fishability verdict** – classifies conditions as *Fishable*, *Marginal* or *Not worth it* using reasonable thresholds for sustained wind speed and wave height.
* **Web dashboard** – renders an HTML page at the root URL with a manual **Refresh** button.  If forecast generation fails, the last successful forecast is cached and served with a banner showing when it was generated.
* **JSON API** – endpoints to view the current forecast (`/api/forecast`) and trigger a refresh (`/api/refresh`).

## Getting started

These instructions assume Python 3.9 or later is installed.  A virtual environment is recommended to avoid polluting the global Python installation.

### 1. Clone or copy the project

Copy the repository contents into a working directory.  The important files are:

* `app.py` – Flask application and forecast logic.
* `templates/` – Jinja2 templates for the web UI.
* `static/` – CSS for styling the dashboard.
* `data/forecast.json` – cached forecast data (created on first run).
* `requirements.txt` – Python dependencies.

### 2. Create and activate a virtual environment (optional but recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

Install the required Python packages using `pip`:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Run the server

Launch the Flask application using the provided script.  The server binds to `0.0.0.0` on port **5757** by default so that it is reachable on the local machine and across your network.

```bash
python app.py
```

Navigate to `http://localhost:5757/` in your browser.  The dashboard will display the current forecast.  Click **Refresh Forecast** to regenerate the report on demand.  If the refresh fails (for example due to a network error) the cached forecast stored in `data/forecast.json` will be served with a banner indicating the timestamp of the cached report.

### Optional: Create a systemd service

On Linux systems you can run the server continuously in the background using systemd.  Create a unit file at `/etc/systemd/system/surf_forecast.service` with contents similar to:

```ini
[Unit]
Description=Surf & Pier Fishing Forecast Service
After=network.target

[Service]
WorkingDirectory=/path/to/your/project
ExecStart=/path/to/your/python -m flask --app app run --host 0.0.0.0 --port 5757
Restart=on-failure
User=www-data
Environment=FLASK_ENV=production

[Install]
WantedBy=multi-user.target
```

Then reload systemd and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable surf_forecast.service
sudo systemctl start surf_forecast.service
```

Adjust the paths and user to match your environment.  This step is optional; running via `python app.py` is sufficient for local use.

## Notes

* The application uses only public sources and requires no API keys or accounts.
* By default the server stores the cached forecast in `data/forecast.json`.  Deleting this file forces the app to generate a new forecast on the next request.
* The first forecast build may take several seconds while it fetches the marine forecast and assembles the report.

Enjoy tight lines and successful outings!
