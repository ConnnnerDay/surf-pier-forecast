"""
Surf and Pier Fishing Forecast Application
----------------------------------------

Flask app that generates a 24-hour surf and pier fishing outlook for
Wrightsville Beach and Carolina Beach, NC.  Fetches marine conditions from
the NWS API (zone AMZ158) and water temperature from NOAA CO-OPS, then
dynamically determines which species are likely biting based on season and
water temperature.  Rig recommendations are matched to the active species.

Endpoints:
* ``/``              -- HTML dashboard
* ``/api/forecast``  -- Current forecast as JSON
* ``/api/refresh``   -- POST to regenerate forecast

No API keys required.  Data cached to ``data/forecast.json``.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from zoneinfo import ZoneInfo


# Set up Flask app
app = Flask(__name__)
app.config["SECRET_KEY"] = "replace_this_with_a_secure_key"

# Path to the cached forecast JSON
CACHE_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(CACHE_DIR, "forecast.json")


# ---------------------------------------------------------------------------
# Marine conditions -- multiple sources with automatic fallback
# Wind and waves will NEVER show "Unknown" thanks to layered fallbacks.
# ---------------------------------------------------------------------------

# AMZ158 = Coastal waters from Surf City to Cape Fear NC out 20 NM
NWS_MARINE_ZONE = "AMZ158"
NWS_FORECAST_URL = (
    f"https://api.weather.gov/zones/forecast/{NWS_MARINE_ZONE}/forecast"
)

# NDBC buoys near Wrightsville Beach (tried in order)
NDBC_STATIONS = [
    ("41110", "Masonboro Inlet"),
    ("41037", "Wrightsville Beach Offshore"),
]

# NOAA CO-OPS station 8658163 (Wrightsville Beach) -- same station used for
# water temperature.  Also provides wind speed/direction/gusts.
COOPS_WIND_URL = (
    "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    "?date=latest&station={station}"
    "&product=wind&units=english"
    "&time_zone=lst_ldt&format=json"
)

# Historical monthly averages for the Wrightsville Beach area.
# Used as the absolute last resort so wind/waves are NEVER "Unknown".
# Wind in knots (sustained low - gust high), waves in feet (low - high).
MONTHLY_AVG_WIND: Dict[int, Tuple[float, float]] = {
    1: (8, 15), 2: (8, 16), 3: (9, 16), 4: (8, 15), 5: (7, 13), 6: (6, 12),
    7: (5, 11), 8: (5, 11), 9: (7, 14), 10: (7, 14), 11: (7, 14), 12: (8, 15),
}
MONTHLY_AVG_WAVES: Dict[int, Tuple[float, float]] = {
    1: (2, 4), 2: (2, 4), 3: (2, 4), 4: (1, 3), 5: (1, 3), 6: (1, 2),
    7: (1, 2), 8: (1, 2), 9: (2, 4), 10: (2, 4), 11: (2, 4), 12: (2, 4),
}
MONTHLY_AVG_WIND_DIR: Dict[int, str] = {
    1: "NW", 2: "NW", 3: "SW", 4: "SW", 5: "SW", 6: "SW",
    7: "SW", 8: "SW", 9: "NE", 10: "NE", 11: "NW", 12: "NW",
}

_MS_TO_KNOTS = 1.94384
_M_TO_FEET = 3.28084
_MPH_TO_KNOTS = 0.868976

# Map compass degrees to abbreviations
_DEG_TO_DIR = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def _deg_to_compass(deg: float) -> str:
    """Convert wind direction in degrees to a compass abbreviation."""
    idx = round(deg / 22.5) % 16
    return _DEG_TO_DIR[idx]


# -- Source 1: NWS zone forecast API ----------------------------------------

def _try_nws_forecast() -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]], Optional[str]]:
    """NWS marine zone forecast -- provides 24-hour forecast ranges."""
    headers = {
        "User-Agent": "(SurfPierForecast, github.com/ConnnnerDay/surf-pier-forecast)",
        "Accept": "application/ld+json",
    }
    response = requests.get(NWS_FORECAST_URL, headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()
    periods = data["properties"]["periods"]
    return parse_conditions(periods)


# -- Source 2: NDBC buoy real-time observations ------------------------------

def _try_ndbc_station(station_id: str) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]], Optional[str]]:
    """Fetch real-time wind/wave observations from a single NDBC buoy."""
    url = f"https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"
    resp = requests.get(url, headers={"User-Agent": "SurfPierForecast/1.0"}, timeout=15)
    resp.raise_for_status()

    lines = resp.text.strip().split("\n")
    if len(lines) < 3:
        return None, None, None

    header = lines[0].replace("#", "").split()
    col = {name: idx for idx, name in enumerate(header)}

    wind_range = None
    wave_range = None
    wind_dir = None

    for line in lines[2:12]:  # Check up to 10 recent observations
        fields = line.split()
        if len(fields) < len(header):
            continue

        _MISSING = {"MM", "99.0", "99.00", "999", "999.0"}

        wspd_raw = fields[col["WSPD"]] if "WSPD" in col else "MM"
        gst_raw = fields[col["GST"]] if "GST" in col else "MM"
        wdir_raw = fields[col["WDIR"]] if "WDIR" in col else "MM"
        wvht_raw = fields[col["WVHT"]] if "WVHT" in col else "MM"

        if wind_range is None and wspd_raw not in _MISSING:
            wspd_kt = float(wspd_raw) * _MS_TO_KNOTS
            gst_kt = float(gst_raw) * _MS_TO_KNOTS if gst_raw not in _MISSING else wspd_kt
            wind_range = (round(wspd_kt, 1), round(max(wspd_kt, gst_kt), 1))

        if wind_dir is None and wdir_raw not in _MISSING:
            wind_dir = _deg_to_compass(float(wdir_raw))

        if wave_range is None and wvht_raw not in _MISSING:
            wvht_ft = float(wvht_raw) * _M_TO_FEET
            wave_range = (round(wvht_ft, 1), round(wvht_ft, 1))

        if wind_range and wave_range and wind_dir:
            break

    return wind_range, wave_range, wind_dir


# -- Source 3: NOAA CO-OPS wind data (same station as water temp) -----------

def _try_coops_wind() -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]], Optional[str]]:
    """Fetch wind from NOAA CO-OPS station 8658163 (Wrightsville Beach).

    This is the same station we use for water temperature, so if water temp
    loads successfully this source is very likely to work too.
    Returns wind data only (no wave data from this source).
    """
    url = COOPS_WIND_URL.format(station=WATER_TEMP_STATION)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    entry = data.get("data", [{}])[0]
    speed = entry.get("s")   # speed in knots
    gust = entry.get("g")    # gust in knots
    direction = entry.get("d")  # direction string like "SW"

    if speed is None:
        return None, None, None

    speed_f = float(speed)
    gust_f = float(gust) if gust and gust != "0.00" else speed_f
    wind_range = (round(speed_f, 1), round(max(speed_f, gust_f), 1))
    wind_dir = direction if direction else None

    return wind_range, None, wind_dir


# -- Source 4: NWS gridpoint forecast (land point, has wind) ----------------

def _try_nws_gridpoint() -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]], Optional[str]]:
    """NWS grid forecast for the nearest land point to Wrightsville Beach.

    Provides wind speed and direction from the standard forecast.  No wave
    data (land point), but fills the wind gap if marine sources are down.
    """
    headers = {
        "User-Agent": "(SurfPierForecast, github.com/ConnnnerDay/surf-pier-forecast)",
        "Accept": "application/ld+json",
    }
    # First get the gridpoint info
    pts = requests.get(
        "https://api.weather.gov/points/34.2104,-77.7964",
        headers=headers, timeout=10,
    )
    pts.raise_for_status()
    forecast_url = pts.json()["properties"]["forecast"]

    # Then get the forecast
    fc = requests.get(forecast_url, headers=headers, timeout=10)
    fc.raise_for_status()
    periods = fc.json()["properties"]["periods"]

    wind_ranges: List[Tuple[float, float]] = []
    wind_dirs: List[str] = []

    for period in periods[:3]:
        # windSpeed is like "10 mph" or "5 to 10 mph"
        ws = period.get("windSpeed", "")
        wd = period.get("windDirection", "")

        m = re.search(r"(\d+)(?:\s*to\s*(\d+))?\s*mph", ws, re.IGNORECASE)
        if m:
            low = float(m.group(1)) * _MPH_TO_KNOTS
            high = float(m.group(2)) * _MPH_TO_KNOTS if m.group(2) else low
            wind_ranges.append((round(low, 1), round(high, 1)))

        if wd:
            wind_dirs.append(wd)

    wind_range = None
    if wind_ranges:
        wind_range = (
            min(w[0] for w in wind_ranges),
            max(w[1] for w in wind_ranges),
        )

    wind_dir = wind_dirs[0] if wind_dirs else None
    return wind_range, None, wind_dir


# -- Source 5: Seasonal averages (ALWAYS succeeds) --------------------------

def _seasonal_averages(month: int) -> Tuple[Tuple[float, float], Tuple[float, float], str]:
    """Historical monthly averages -- the last resort. Never fails."""
    return (
        MONTHLY_AVG_WIND[month],
        MONTHLY_AVG_WAVES[month],
        MONTHLY_AVG_WIND_DIR[month],
    )


# -- Combined fetcher -------------------------------------------------------

def get_marine_conditions(month: int) -> Tuple[Tuple[float, float], Tuple[float, float], str]:
    """Get marine conditions, trying every source until we have full data.

    Returns (wind_range, wave_range, wind_dir).  Guaranteed to never return
    None for any field -- seasonal averages fill any remaining gaps.
    """
    wind_range: Optional[Tuple[float, float]] = None
    wave_range: Optional[Tuple[float, float]] = None
    wind_dir: Optional[str] = None

    sources = [
        ("NWS zone forecast", _try_nws_forecast),
    ]
    for station_id, station_name in NDBC_STATIONS:
        sources.append((f"NDBC {station_name}", lambda sid=station_id: _try_ndbc_station(sid)))
    sources.append(("NOAA CO-OPS wind", _try_coops_wind))
    sources.append(("NWS gridpoint forecast", _try_nws_gridpoint))

    for name, fetcher in sources:
        # Stop once we have both wind and waves
        if wind_range is not None and wave_range is not None and wind_dir is not None:
            break
        try:
            w, s, d = fetcher()
            if wind_range is None and w is not None:
                wind_range = w
                print(f"Wind from {name}: {w}")
            if wave_range is None and s is not None:
                wave_range = s
                print(f"Waves from {name}: {s}")
            if wind_dir is None and d is not None:
                wind_dir = d
        except Exception as exc:
            print(f"{name} unavailable: {exc}")

    # Fill any remaining gaps with seasonal averages
    avg_wind, avg_waves, avg_dir = _seasonal_averages(month)
    if wind_range is None:
        wind_range = avg_wind
        print(f"Wind from seasonal avg: {avg_wind}")
    if wave_range is None:
        wave_range = avg_waves
        print(f"Waves from seasonal avg: {avg_waves}")
    if wind_dir is None:
        wind_dir = avg_dir

    return wind_range, wave_range, wind_dir


def parse_conditions(
    periods: List[Dict[str, Any]],
) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]], Optional[str]]:
    """Extract wind and wave ranges from NWS marine forecast periods.

    Examines the first 3 periods (~24 hours) and regex-parses wind speed (kt)
    and sea height (ft) from the ``detailedForecast`` text.
    """
    wind_ranges: List[Tuple[float, float]] = []
    wave_ranges: List[Tuple[float, float]] = []
    wind_directions: List[str] = []

    for period in periods[:3]:
        text = period.get("detailedForecast", "")

        # Wind direction -- handle both abbreviated (SW, NE) and spelled out
        # (Southwest, Northeast) forms that the NWS API may return.
        dir_match = re.search(
            r"(north(?:east|west)?|south(?:east|west)?|east|west|"
            r"NE|NW|SE|SW|N|E|S|W|VARIABLE)\s+wind",
            text, re.IGNORECASE,
        )
        if dir_match:
            _DIR_MAP = {
                "north": "N", "northeast": "NE", "northwest": "NW",
                "south": "S", "southeast": "SE", "southwest": "SW",
                "east": "E", "west": "W", "variable": "VARIABLE",
            }
            raw = dir_match.group(1)
            wind_directions.append(_DIR_MAP.get(raw.lower(), raw.upper()))

        # Wind speed -- match "10 to 15 kt", "10 to 15 knots",
        # "around 10 kt", "around 10 knots", etc.
        wind_match = re.search(
            r"(\d+)(?:\s*to\s*(\d+))?\s*(?:kt|knots?)",
            text, re.IGNORECASE,
        )
        if wind_match:
            low = float(wind_match.group(1))
            high = float(wind_match.group(2)) if wind_match.group(2) else low
            wind_ranges.append((low, high))

        # Sea/wave height -- match "seas 2 to 3 ft", "seas 2 to 3 feet",
        # "seas around 2 feet", "waves 1 to 2 ft", etc.
        sea_match = re.search(
            r"(?:seas?|waves?)\s*(?:around\s+)?(\d+)(?:\s*to\s*(\d+))?\s*(?:ft|feet|foot)",
            text, re.IGNORECASE,
        )
        if sea_match:
            low = float(sea_match.group(1))
            high = float(sea_match.group(2)) if sea_match.group(2) else low
            wave_ranges.append((low, high))

    wind_dir = wind_directions[0] if wind_directions else None

    if wind_ranges:
        wind_range: Optional[Tuple[float, float]] = (
            min(w[0] for w in wind_ranges),
            max(w[1] for w in wind_ranges),
        )
    else:
        wind_range = None

    if wave_ranges:
        wave_range: Optional[Tuple[float, float]] = (
            min(s[0] for s in wave_ranges),
            max(s[1] for s in wave_ranges),
        )
    else:
        wave_range = None

    return wind_range, wave_range, wind_dir


def classify_conditions(wind_range: Optional[Tuple[float, float]], wave_range: Optional[Tuple[float, float]]) -> str:
    """Classify fishability based on wind and wave thresholds.

    The policy uses the following rules:

    * Fishable -- maximum sustained wind < 15 kt **and** maximum sea height < 3 ft.
    * Marginal -- maximum sustained wind <= 20 kt **and** maximum sea height <= 5 ft.
    * Not worth it -- winds > 20 kt **or** seas > 5 ft (small craft advisory
      conditions).
    """
    if wind_range is None or wave_range is None:
        return "Unknown"
    wind_max = wind_range[1]
    wave_max = wave_range[1]
    if wind_max < 15 and wave_max < 3:
        return "Fishable"
    elif wind_max <= 20 and wave_max <= 5:
        return "Marginal"
    else:
        return "Not worth it"


# ---------------------------------------------------------------------------
# NOAA water temperature (free, no API key)
# ---------------------------------------------------------------------------

# Wrightsville Beach NOAA CO-OPS station
WATER_TEMP_STATION = "8658163"
WATER_TEMP_URL = (
    "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    "?date=latest&station={station}"
    "&product=water_temperature&units=english"
    "&time_zone=lst_ldt&format=json"
)

# Historical average water temperatures (F) for Wrightsville Beach, NC by month.
# Used as fallback when the live NOAA reading is unavailable.
MONTHLY_AVG_WATER_TEMP_F = {
    1: 50, 2: 50, 3: 54, 4: 62, 5: 70, 6: 78,
    7: 82, 8: 83, 9: 80, 10: 72, 11: 62, 12: 54,
}


def fetch_water_temperature() -> Optional[float]:
    """Fetch the latest water temperature (F) from NOAA CO-OPS.

    Uses the free Tides & Currents API for station 8658163
    (Wrightsville Beach, NC).  Returns None on any failure.
    """
    try:
        url = WATER_TEMP_URL.format(station=WATER_TEMP_STATION)
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        reading = data.get("data", [{}])[0].get("v")
        if reading is not None:
            return float(reading)
    except Exception:
        pass
    return None


def get_water_temp(month: int) -> Tuple[float, bool]:
    """Return (water_temp_f, is_live).

    Tries the live NOAA reading first.  Falls back to the historical
    monthly average when the API is unreachable.
    """
    live = fetch_water_temperature()
    if live is not None:
        return live, True
    return float(MONTHLY_AVG_WATER_TEMP_F[month]), False


# ---------------------------------------------------------------------------
# Sunrise / sunset (pure math, no API)
# ---------------------------------------------------------------------------

# Wrightsville Beach coordinates
_LAT = 34.2104
_LNG = -77.7964


def _sun_times(dt: datetime) -> Tuple[datetime, datetime]:
    """Compute approximate sunrise and sunset for Wrightsville Beach.

    Uses the simplified NOAA algorithm based on the day-of-year, latitude,
    and an approximate equation of time.  Returns (sunrise, sunset) as
    timezone-aware datetimes in America/New_York.  Accuracy is within a few
    minutes -- good enough for fishing planning.
    """
    tz = ZoneInfo("America/New_York")
    # Day of year (1-365)
    n = dt.timetuple().tm_yday

    # Fractional year in radians
    gamma = 2 * math.pi / 365 * (n - 1)

    # Equation of time (minutes)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )

    # Solar declination (radians)
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )

    lat_rad = math.radians(_LAT)

    # Hour angle at sunrise/sunset (degrees)
    cos_ha = (
        math.cos(math.radians(90.833)) / (math.cos(lat_rad) * math.cos(decl))
        - math.tan(lat_rad) * math.tan(decl)
    )
    # Clamp for polar regions (shouldn't happen at 34°N)
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha = math.degrees(math.acos(cos_ha))

    # Sunrise and sunset in minutes from midnight UTC
    sunrise_utc = 720 - 4 * (_LNG + ha) - eqtime
    sunset_utc = 720 - 4 * (_LNG - ha) - eqtime

    base = datetime(dt.year, dt.month, dt.day, tzinfo=ZoneInfo("UTC"))
    sunrise = base + timedelta(minutes=sunrise_utc)
    sunset = base + timedelta(minutes=sunset_utc)

    return sunrise.astimezone(tz), sunset.astimezone(tz)


# ---------------------------------------------------------------------------
# Species database -- each entry carries scoring variables instead of a
# hard-coded rank.  The actual ranking is computed at forecast time based
# on the current month and water temperature.
# ---------------------------------------------------------------------------

SPECIES_DB: List[Dict[str, Any]] = [
    {
        "name": "Red drum (puppy drum)",
        "temp_min": 45, "temp_max": 85, "temp_ideal_low": 55, "temp_ideal_high": 75,
        "peak_months": [3, 4, 5, 9, 10, 11],
        "good_months": [1, 2, 6, 7, 8, 12],
        "bait": "Cut menhaden or mullet strips; fresh shrimp; live finger mullet when available",
        "rig": "Fish finder rig with sliding egg sinker",
        "hook_size": "2/0-5/0 circle hook",
        "sinker": "2-4 oz egg sinker",
        "explanation_cold": "Winter red drum congregate in deeper marsh channels and holes, foraging along the bottom for shrimp, crabs and small fish.",
        "explanation_warm": "Red drum actively feed in the surf zone and around inlets, chasing mullet, menhaden and crabs in warmer water.",
    },
    {
        "name": "Speckled trout (spotted seatrout)",
        "temp_min": 45, "temp_max": 82, "temp_ideal_low": 58, "temp_ideal_high": 75,
        "peak_months": [3, 4, 5, 10, 11],
        "good_months": [1, 2, 6, 9, 12],
        "bait": "Live shrimp (most productive); finger mullet; small menhaden",
        "rig": "Popping-cork or fishfinder rig on light leader",
        "hook_size": "1/0-2/0 circle hook",
        "sinker": "1-2 oz",
        "explanation_cold": "Speckled trout hold in deeper holes and backwater creeks; live shrimp or finger mullet entice sluggish winter fish.",
        "explanation_warm": "Speckled trout are aggressively feeding on shrimp and small baitfish in the shallows and around grass flats.",
    },
    {
        "name": "Black drum",
        "temp_min": 48, "temp_max": 85, "temp_ideal_low": 55, "temp_ideal_high": 78,
        "peak_months": [2, 3, 4, 10, 11],
        "good_months": [1, 5, 9, 12],
        "bait": "Cut shrimp, clams, blood worms, cut mullet, menhaden or crab pieces",
        "rig": "Hi-lo rig or fish finder rig",
        "hook_size": "1/0-3/0 circle hook",
        "sinker": "2-4 oz pyramid or bank sinker",
        "explanation_cold": "Black drum are bottom feeders that locate prey with chin barbels; they prefer mollusks, shrimp and crab pieces in cooler water.",
        "explanation_warm": "Black drum move through inlets and along structure, actively rooting for crabs, clams and shrimp in warming water.",
    },
    {
        "name": "Sheepshead",
        "temp_min": 50, "temp_max": 80, "temp_ideal_low": 55, "temp_ideal_high": 72,
        "peak_months": [1, 2, 3, 12],
        "good_months": [4, 11],
        "bait": "Live fiddler crabs; sand fleas; small pieces of shrimp",
        "rig": "Knocker rig with short fluorocarbon leader",
        "hook_size": "1/0-3/0 J-style or circle hook",
        "sinker": "1-3 oz egg sinker",
        "explanation_cold": "Sheepshead feed around pilings and rock structures, nibbling barnacles and crustaceans; bites are subtle and require fishing straight down.",
        "explanation_warm": "Sheepshead stack up around nearshore structure and pilings, picking at barnacles and crabs before moving offshore for spawning.",
    },
    {
        "name": "Tautog (blackfish)",
        "temp_min": 42, "temp_max": 72, "temp_ideal_low": 48, "temp_ideal_high": 62,
        "peak_months": [1, 2, 3, 11, 12],
        "good_months": [4, 10],
        "bait": "Pieces of fresh shrimp, sand fleas, fiddler or rock crabs, clams",
        "rig": "Knocker rig with heavy fluorocarbon leader",
        "hook_size": "#6-#2 strong hook",
        "sinker": "2-4 oz bank or egg sinker",
        "explanation_cold": "Tautog cling to jetties and rock piles in winter, feeding on crustaceans; they require strong tackle and small, strong hooks.",
        "explanation_warm": "Tautog become less active as water warms and move to deeper structure; early spring offers a window before they thin out.",
    },
    {
        "name": "Black sea bass",
        "temp_min": 46, "temp_max": 78, "temp_ideal_low": 52, "temp_ideal_high": 68,
        "peak_months": [1, 2, 3, 11, 12],
        "good_months": [4, 10],
        "bait": "Strips of squid or cut fish; shrimp",
        "rig": "Hi-lo rig on braided line",
        "hook_size": "2/0-3/0 circle hook",
        "sinker": "3-4 oz pyramid or bank sinker",
        "explanation_cold": "Inshore black sea bass inhabit wrecks and hard bottom, feeding on crabs, shrimp and small fish; bottom-fish with squid or cut bait.",
        "explanation_warm": "Black sea bass are abundant over nearshore reefs and wrecks, aggressively hitting squid strips and cut bait.",
    },
    {
        "name": "Bluefish",
        "temp_min": 50, "temp_max": 84, "temp_ideal_low": 60, "temp_ideal_high": 78,
        "peak_months": [4, 5, 10, 11],
        "good_months": [3, 6, 9, 12],
        "bait": "Cut menhaden or mullet; small fish pieces",
        "rig": "Fish finder rig with steel leader",
        "hook_size": "3/0-5/0 J-hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Smaller bluefish (snappers/tailors) roam nearshore waters and readily hit cut bait; wire or heavy leaders are essential.",
        "explanation_warm": "Bluefish schools are actively chasing baitfish along the surf line and around piers; cut menhaden and mullet draw explosive strikes.",
    },
    {
        "name": "Whiting (sea mullet, kingfish)",
        "temp_min": 48, "temp_max": 82, "temp_ideal_low": 58, "temp_ideal_high": 74,
        "peak_months": [3, 4, 5, 10, 11],
        "good_months": [2, 6, 9, 12],
        "bait": "Fresh shrimp, mole crabs (sand fleas), bloodworms, squid",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#2 circle hook",
        "sinker": "1-3 oz pyramid sinker",
        "explanation_cold": "Whiting patrol sandy bottoms along beaches and piers; they feed on shrimp, mole crabs and worms in the winter surf.",
        "explanation_warm": "Whiting are schooling up along the beach and biting aggressively on shrimp and sand fleas in the wash zone.",
    },
    {
        "name": "Northern puffer (blowfish)",
        "temp_min": 45, "temp_max": 78, "temp_ideal_low": 50, "temp_ideal_high": 68,
        "peak_months": [1, 2, 3, 11, 12],
        "good_months": [4, 10],
        "bait": "Small pieces of shrimp, bloodworms or squid",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#4 baitholder or circle hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "These small, delicious fish are common in late fall and winter; they nibble shrimp and squid pieces on small hooks.",
        "explanation_warm": "Northern puffers move inshore with warming water, readily biting small pieces of shrimp or squid near structure.",
    },
    {
        "name": "Striped bass (rockfish)",
        "temp_min": 40, "temp_max": 70, "temp_ideal_low": 48, "temp_ideal_high": 62,
        "peak_months": [1, 2, 3, 11, 12],
        "good_months": [4, 10],
        "bait": "Cut menhaden, mullet or shad; live mullet or eels",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "5/0-7/0 circle hook",
        "sinker": "3-5 oz pyramid or bank sinker",
        "explanation_cold": "Striped bass are found around piers, jetties and surf troughs; winter surf anglers use cut or live bait on heavy tackle.",
        "explanation_warm": "Striped bass push through inlets chasing schools of mullet and menhaden; they are most active at dawn and dusk.",
    },
    {
        "name": "Flounder (summer flounder)",
        "temp_min": 55, "temp_max": 82, "temp_ideal_low": 62, "temp_ideal_high": 76,
        "peak_months": [4, 5, 9, 10],
        "good_months": [3, 6, 7, 8, 11],
        "bait": "Live finger mullet; live minnows; fresh shrimp under a bucktail jig",
        "rig": "Fish finder rig with 24-36 in fluorocarbon leader",
        "hook_size": "2/0-4/0 circle or wide-gap hook",
        "sinker": "1-3 oz egg sinker",
        "explanation_cold": "Flounder are mostly offshore in cold water; stragglers around inlets may bite live mullet drifted slowly along the bottom.",
        "explanation_warm": "Flounder are ambushing baitfish in the surf, around inlets and near pier pilings; live finger mullet is the top producer.",
    },
    {
        "name": "Spanish mackerel",
        "temp_min": 65, "temp_max": 88, "temp_ideal_low": 72, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Shiny spoons; live baitfish; small plugs; fresh shrimp on a long-shank hook",
        "rig": "Float rig with wire leader, free-lined or under float",
        "hook_size": "#1-2/0 long-shank hook",
        "sinker": "None or 1/2 oz split shot",
        "explanation_cold": "Spanish mackerel have migrated south; they are not present in nearshore NC waters during cold months.",
        "explanation_warm": "Spanish mackerel are blitzing baitfish just behind the breakers and around piers; they hit flashy spoons and small baits.",
    },
    {
        "name": "Pompano",
        "temp_min": 60, "temp_max": 85, "temp_ideal_low": 68, "temp_ideal_high": 78,
        "peak_months": [4, 5, 10, 11],
        "good_months": [3, 6, 9],
        "bait": "Sand fleas (mole crabs); fresh shrimp; Fishbites",
        "rig": "Pompano rig with float beads above hooks",
        "hook_size": "#2-#1 circle hook",
        "sinker": "2-3 oz pyramid sinker",
        "explanation_cold": "Pompano are scarce inshore during cold months; occasional catches occur on warmer days near the surf zone.",
        "explanation_warm": "Pompano are running the surf line feeding on sand fleas and small crustaceans; target the troughs and holes along the beach.",
    },
    {
        "name": "Spot",
        "temp_min": 50, "temp_max": 80, "temp_ideal_low": 58, "temp_ideal_high": 72,
        "peak_months": [9, 10, 11],
        "good_months": [3, 4, 5, 8, 12],
        "bait": "Bloodworms; small pieces of shrimp; Fishbites",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#4 circle or bait hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "Spot are mostly offshore or in deeper channels during winter; occasional catches from piers on warmer days.",
        "explanation_warm": "Spot are schooling along the beach and around piers, biting aggressively on bloodworms and shrimp pieces.",
    },
    {
        "name": "Cobia",
        "temp_min": 68, "temp_max": 88, "temp_ideal_low": 72, "temp_ideal_high": 84,
        "peak_months": [5, 6, 7],
        "good_months": [4, 8, 9],
        "bait": "Live eels; live menhaden; large live shrimp",
        "rig": "Fish finder rig, heavy tackle, or free-lined live bait",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "2-4 oz egg sinker or none for free-lining",
        "explanation_cold": "Cobia have migrated south and are not present in NC waters during cold months.",
        "explanation_warm": "Cobia are cruising near the surface around piers, buoys and structure; sight-cast live eels or large baits to visible fish.",
    },
    # --- Additional species for the Wrightsville / Carolina Beach area ---
    {
        "name": "Atlantic croaker",
        "temp_min": 50, "temp_max": 84, "temp_ideal_low": 62, "temp_ideal_high": 78,
        "peak_months": [9, 10, 11],
        "good_months": [4, 5, 8, 12],
        "bait": "Fresh shrimp pieces; bloodworms; squid strips; Fishbites",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "1-3 oz pyramid sinker",
        "explanation_cold": "Croaker move to deeper channels and offshore in winter; occasional catches from piers on mild days.",
        "explanation_warm": "Croaker are schooling along the surf and around piers, making their signature croaking sound; they hit shrimp and worms aggressively.",
    },
    {
        "name": "Gray trout (weakfish)",
        "temp_min": 50, "temp_max": 82, "temp_ideal_low": 58, "temp_ideal_high": 75,
        "peak_months": [4, 5, 10, 11],
        "good_months": [3, 6, 9, 12],
        "bait": "Live shrimp; small live mullet; cut bait strips",
        "rig": "Fish finder rig with light fluorocarbon leader",
        "hook_size": "1/0-3/0 circle hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Gray trout hold in deeper holes near inlets; they feed slowly and prefer live shrimp drifted near the bottom.",
        "explanation_warm": "Gray trout are feeding actively around inlets and along the beach, hitting live shrimp and small baitfish near structure.",
    },
    {
        "name": "King mackerel (kingfish)",
        "temp_min": 68, "temp_max": 88, "temp_ideal_low": 74, "temp_ideal_high": 84,
        "peak_months": [5, 6, 7, 8],
        "good_months": [4, 9, 10],
        "bait": "Live cigar minnows; live menhaden; live blue runners",
        "rig": "King mackerel stinger rig with wire leader and trailing treble",
        "hook_size": "4/0-7/0 treble or J-hook with stinger",
        "sinker": "None or 1 oz egg for slow-trolling",
        "explanation_cold": "King mackerel have migrated south and are not available in NC nearshore waters during cold months.",
        "explanation_warm": "Kings are cruising near piers and along the beach chasing baitfish schools; slow-troll or float live baits on wire leader.",
    },
    {
        "name": "False albacore (little tunny)",
        "temp_min": 64, "temp_max": 84, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [10, 11],
        "good_months": [5, 9, 12],
        "bait": "Live cigar minnows; small live menhaden; metal jigs",
        "rig": "Float rig with fluorocarbon leader, free-lined",
        "hook_size": "1/0-3/0 circle or J-hook",
        "sinker": "None",
        "explanation_cold": "False albacore are offshore or have migrated; not targeted inshore during cold months.",
        "explanation_warm": "False albacore are blitzing baitfish in dramatic surface feeds; cast live baits or jigs into the breaking fish.",
    },
    {
        "name": "Triggerfish (gray)",
        "temp_min": 65, "temp_max": 85, "temp_ideal_low": 72, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small pieces of squid; cut shrimp; sand fleas; fiddler crabs",
        "rig": "Knocker rig with small strong hooks",
        "hook_size": "#4-#1 strong short-shank hook",
        "sinker": "1-2 oz egg or bank sinker",
        "explanation_cold": "Triggerfish move to deeper offshore waters in winter and are not available inshore.",
        "explanation_warm": "Triggerfish stack up around pier pilings, jetties and hard structure; they steal bait expertly so use small, strong hooks.",
    },
    {
        "name": "Spadefish (Atlantic)",
        "temp_min": 64, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8],
        "good_months": [4, 9],
        "bait": "Small pieces of clam; jellyfish pieces; cannonball jellyfish strips",
        "rig": "Knocker rig with light fluorocarbon leader and small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "Small split shot or none",
        "explanation_cold": "Spadefish are offshore and unavailable inshore during cold months.",
        "explanation_warm": "Spadefish school around pier pilings and buoys in large numbers; they feed on jellyfish and small invertebrates near the surface.",
    },
    {
        "name": "Tarpon",
        "temp_min": 72, "temp_max": 90, "temp_ideal_low": 78, "temp_ideal_high": 86,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Live menhaden; live mullet; large live shrimp",
        "rig": "Fish finder rig, heavy tackle, or free-lined live bait",
        "hook_size": "6/0-9/0 circle hook",
        "sinker": "2-4 oz egg sinker or none",
        "explanation_cold": "Tarpon are a tropical species not present in NC waters during cold months.",
        "explanation_warm": "Tarpon are cruising near inlets and along the beach; they hit large live baits and put up spectacular aerial fights.",
    },
    {
        "name": "Pigfish",
        "temp_min": 60, "temp_max": 85, "temp_ideal_low": 70, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8],
        "good_months": [4, 9],
        "bait": "Small pieces of shrimp; squid bits; bloodworms",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#2 circle hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "Pigfish are offshore or in deeper channels during cold months; not commonly caught inshore.",
        "explanation_warm": "Pigfish are abundant around piers and in the surf; they make excellent live bait for flounder and are fun to catch on light tackle.",
    },
    {
        "name": "Pinfish",
        "temp_min": 55, "temp_max": 85, "temp_ideal_low": 65, "temp_ideal_high": 80,
        "peak_months": [4, 5, 6, 7, 8, 9],
        "good_months": [3, 10],
        "bait": "Small pieces of shrimp; bread balls; squid bits",
        "rig": "Hi-lo rig with very small hooks",
        "hook_size": "#8-#4 bait hook",
        "sinker": "1/2-1 oz split shot or pyramid",
        "explanation_cold": "Pinfish move to deeper water during cold months and are scarce inshore.",
        "explanation_warm": "Pinfish are everywhere around piers, docks and structure; excellent live bait when caught on sabiki rigs or small hooks.",
    },
    # --- Sharks ---
    {
        "name": "Blacktip shark",
        "temp_min": 68, "temp_max": 90, "temp_ideal_low": 75, "temp_ideal_high": 85,
        "peak_months": [6, 7, 8],
        "good_months": [5, 9, 10],
        "bait": "Cut menhaden or mullet; fresh bluefish chunks; live bait on heavy tackle",
        "rig": "Fish finder rig with wire leader",
        "hook_size": "7/0-10/0 circle hook",
        "sinker": "4-8 oz pyramid sinker",
        "explanation_cold": "Blacktip sharks have migrated south and are not present in NC nearshore waters during cold months.",
        "explanation_warm": "Blacktip sharks are cruising the surf zone and around piers chasing baitfish schools; they are one of the most common sharks caught from shore.",
    },
    {
        "name": "Spinner shark",
        "temp_min": 68, "temp_max": 88, "temp_ideal_low": 74, "temp_ideal_high": 84,
        "peak_months": [6, 7, 8],
        "good_months": [5, 9],
        "bait": "Cut menhaden; fresh mullet chunks; live bluefish",
        "rig": "Fish finder rig with wire leader",
        "hook_size": "7/0-10/0 circle hook",
        "sinker": "4-8 oz pyramid sinker",
        "explanation_cold": "Spinner sharks migrate south for winter and are absent from NC waters.",
        "explanation_warm": "Spinner sharks are feeding in the surf zone, often leaping and spinning out of the water while chasing baitfish.",
    },
    {
        "name": "Atlantic sharpnose shark",
        "temp_min": 65, "temp_max": 88, "temp_ideal_low": 72, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut fish; fresh shrimp; squid; any cut bait",
        "rig": "Fish finder rig with wire leader",
        "hook_size": "3/0-5/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Atlantic sharpnose sharks move offshore or south during cold months.",
        "explanation_warm": "Sharpnose sharks are the most common small shark caught from piers and the surf; they readily hit almost any cut bait.",
    },
    {
        "name": "Bull shark",
        "temp_min": 68, "temp_max": 90, "temp_ideal_low": 76, "temp_ideal_high": 86,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Large cut menhaden; stingray chunks; live bluefish; bonito heads",
        "rig": "Shark rig with heavy wire leader",
        "hook_size": "10/0-16/0 circle hook",
        "sinker": "6-10 oz pyramid sinker",
        "explanation_cold": "Bull sharks move to warmer southern waters during winter months.",
        "explanation_warm": "Bull sharks patrol inlets, piers and the surf zone; they are powerful fighters requiring very heavy tackle and wire leader.",
    },
    {
        "name": "Sandbar shark",
        "temp_min": 65, "temp_max": 85, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8],
        "good_months": [5, 9, 10],
        "bait": "Large cut menhaden; cut bluefish; fresh mullet chunks",
        "rig": "Fish finder rig with wire leader",
        "hook_size": "8/0-12/0 circle hook",
        "sinker": "5-8 oz pyramid sinker",
        "explanation_cold": "Sandbar sharks migrate to deeper offshore waters or south during cold months.",
        "explanation_warm": "Sandbar sharks are one of the most abundant large sharks nearshore in NC; target them from piers or the surf with heavy cut bait.",
    },
    {
        "name": "Bonnethead shark",
        "temp_min": 70, "temp_max": 90, "temp_ideal_low": 76, "temp_ideal_high": 86,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Live shrimp; cut shrimp; small crabs; cut fish",
        "rig": "Fish finder rig with light wire or heavy fluorocarbon leader",
        "hook_size": "2/0-4/0 circle hook",
        "sinker": "1-3 oz egg sinker",
        "explanation_cold": "Bonnethead sharks migrate south and are not present in NC during cold months.",
        "explanation_warm": "Bonnetheads are the smallest hammerhead species; they cruise grass flats, inlets and the surf feeding on shrimp and crabs.",
    },
    {
        "name": "Lemon shark",
        "temp_min": 70, "temp_max": 88, "temp_ideal_low": 76, "temp_ideal_high": 84,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Large cut menhaden; live mullet; cut stingray; bonito chunks",
        "rig": "Fish finder rig with wire leader",
        "hook_size": "8/0-12/0 circle hook",
        "sinker": "4-8 oz pyramid sinker",
        "explanation_cold": "Lemon sharks are in warmer southern waters during winter and not found in NC.",
        "explanation_warm": "Lemon sharks are found near inlets, docks and shallow flats; they are strong fighters that prefer live or large cut baits.",
    },
    {
        "name": "Dusky shark",
        "temp_min": 62, "temp_max": 82, "temp_ideal_low": 68, "temp_ideal_high": 78,
        "peak_months": [4, 5, 10, 11],
        "good_months": [3, 6, 9],
        "bait": "Large cut menhaden; bluefish chunks; bonito; any large oily cut bait",
        "rig": "Shark rig with heavy wire leader",
        "hook_size": "12/0-16/0 circle hook",
        "sinker": "6-10 oz pyramid sinker",
        "explanation_cold": "Dusky sharks are migratory and pass through NC waters mainly during seasonal transitions.",
        "explanation_warm": "Dusky sharks migrate through NC nearshore waters in spring and fall following baitfish; they are large, powerful fish requiring the heaviest tackle.",
    },
    # --- Rays ---
    {
        "name": "Southern stingray",
        "temp_min": 60, "temp_max": 88, "temp_ideal_low": 70, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; squid; cut fish; sand fleas",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "3/0-6/0 circle hook",
        "sinker": "3-5 oz pyramid sinker",
        "explanation_cold": "Southern stingrays move to deeper water or migrate south during cold months.",
        "explanation_warm": "Southern stingrays are common in the surf and around piers; they are powerful fighters often caught incidentally while bottom fishing.",
    },
    {
        "name": "Cownose ray",
        "temp_min": 62, "temp_max": 86, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [5, 6, 9, 10],
        "good_months": [4, 7, 8],
        "bait": "Cut shrimp; clam pieces; crab; cut bait",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "4/0-6/0 circle hook",
        "sinker": "3-5 oz pyramid sinker",
        "explanation_cold": "Cownose rays migrate south in large schools during fall and are absent from NC in winter.",
        "explanation_warm": "Cownose rays travel in large schools through the surf and inlets; they are strong fighters often hooked while bottom fishing for other species.",
    },
    # --- Jacks & relatives ---
    {
        "name": "Jack crevalle",
        "temp_min": 68, "temp_max": 88, "temp_ideal_low": 74, "temp_ideal_high": 84,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Live menhaden; live mullet; large shrimp; topwater plugs",
        "rig": "Fish finder rig with heavy leader or free-lined live bait",
        "hook_size": "3/0-6/0 circle or J-hook",
        "sinker": "1-3 oz egg sinker or none",
        "explanation_cold": "Jack crevalle move to warmer southern waters during winter months.",
        "explanation_warm": "Jack crevalle are aggressive predators that crash baitfish schools in the surf, inlets and around piers; they put up a brutal fight on any tackle.",
    },
    {
        "name": "Blue runner (hardtail)",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Small jigs; live shrimp; cut bait; sabiki rigs",
        "rig": "Hi-lo rig or sabiki with light tackle",
        "hook_size": "#2-2/0 hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "Blue runners are offshore or have migrated south during cold months.",
        "explanation_warm": "Blue runners school around piers and nearshore structure; they are excellent live bait for king mackerel and great fun on light tackle.",
    },
    {
        "name": "Lookdown",
        "temp_min": 68, "temp_max": 85, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8],
        "good_months": [5, 9],
        "bait": "Small pieces of shrimp; tiny jigs; sand fleas",
        "rig": "Hi-lo rig with small hooks and light leader",
        "hook_size": "#4-#1 hook",
        "sinker": "1/2-1 oz split shot",
        "explanation_cold": "Lookdowns move to warmer waters during cold months and are not present inshore.",
        "explanation_warm": "Lookdowns school around pier pilings and structure; their flat silver bodies make them unmistakable and they bite small baits near the bottom.",
    },
    {
        "name": "Greater amberjack",
        "temp_min": 65, "temp_max": 85, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [4, 5, 6, 9, 10],
        "good_months": [3, 7, 8, 11],
        "bait": "Live blue runners; live menhaden; large jigs; live cigar minnows",
        "rig": "Fish finder rig, heavy tackle, or vertical jig",
        "hook_size": "6/0-9/0 circle hook",
        "sinker": "4-8 oz bank sinker or jig head",
        "explanation_cold": "Amberjack move to deeper offshore structure during winter but some remain on nearshore wrecks.",
        "explanation_warm": "Greater amberjack are found on nearshore wrecks and reefs; they are one of the hardest-fighting fish in NC waters and require heavy tackle.",
    },
    {
        "name": "Almaco jack",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live baitfish; cut fish strips; vertical jigs",
        "rig": "Fish finder rig with heavy leader or vertical jig",
        "hook_size": "4/0-7/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Almaco jacks move to deeper offshore waters during cold months.",
        "explanation_warm": "Almaco jacks are found on nearshore wrecks and ledges; they fight hard and are excellent table fare.",
    },
    {
        "name": "Banded rudderfish",
        "temp_min": 65, "temp_max": 82, "temp_ideal_low": 70, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Small live baitfish; live shrimp; small jigs",
        "rig": "Float rig with light leader or free-lined",
        "hook_size": "1/0-3/0 circle hook",
        "sinker": "None or small split shot",
        "explanation_cold": "Banded rudderfish move offshore during cold months and are not available inshore.",
        "explanation_warm": "Juvenile amberjack (banded rudderfish) school around piers, buoys and floating structure; they are scrappy fighters on light tackle.",
    },
    {
        "name": "Permit",
        "temp_min": 72, "temp_max": 88, "temp_ideal_low": 78, "temp_ideal_high": 86,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Live crabs; sand fleas; live shrimp",
        "rig": "Fish finder rig with fluorocarbon leader",
        "hook_size": "1/0-3/0 circle hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Permit are a tropical species not typically found in NC during cold months.",
        "explanation_warm": "Permit occasionally visit NC waters during peak summer; they feed on crabs and crustaceans and are a prized catch when encountered.",
    },
    # --- Snappers ---
    {
        "name": "Red snapper",
        "temp_min": 58, "temp_max": 85, "temp_ideal_low": 65, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10, 11],
        "bait": "Cut squid; cut menhaden; live cigar minnows; live baitfish",
        "rig": "Hi-lo rig or Carolina rig with heavy leader",
        "hook_size": "4/0-7/0 circle hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Red snapper remain on deeper offshore structure during cold months and are less accessible.",
        "explanation_warm": "Red snapper are on nearshore and offshore wrecks and reefs; NC has a growing population and they hit cut bait and live baits aggressively.",
    },
    {
        "name": "Vermilion snapper (beeliner)",
        "temp_min": 58, "temp_max": 82, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9, 10],
        "good_months": [4, 11],
        "bait": "Cut squid strips; small pieces of cut fish; chicken rigs with multiple hooks",
        "rig": "Hi-lo rig (chicken rig) with small hooks",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Vermilion snapper stay on deeper structure and are less active during cold months.",
        "explanation_warm": "Vermilion snapper (beeliners) school over hard bottom and wrecks; they are aggressive biters and one of the best-eating reef fish.",
    },
    {
        "name": "Mangrove snapper (gray snapper)",
        "temp_min": 68, "temp_max": 88, "temp_ideal_low": 74, "temp_ideal_high": 84,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Live shrimp; small live baitfish; cut shrimp; small crabs",
        "rig": "Knocker rig with fluorocarbon leader",
        "hook_size": "1/0-3/0 circle hook",
        "sinker": "1-2 oz egg or split shot",
        "explanation_cold": "Mangrove snapper are in deeper or more southern waters during cold months.",
        "explanation_warm": "Mangrove snapper stack up around pier pilings, docks, jetties and inlets; they are line-shy and require light fluorocarbon leader.",
    },
    {
        "name": "Lane snapper",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut squid; small pieces of shrimp; cut fish strips",
        "rig": "Hi-lo rig with light leader",
        "hook_size": "#1-2/0 circle hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Lane snapper are on deeper offshore reefs and not commonly encountered inshore during winter.",
        "explanation_warm": "Lane snapper occasionally show up on nearshore reefs and around structure; they are colorful, good-eating fish that bite readily.",
    },
    # --- Groupers ---
    {
        "name": "Gag grouper",
        "temp_min": 58, "temp_max": 82, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [4, 5, 6, 9, 10, 11],
        "good_months": [3, 7, 8],
        "bait": "Live menhaden; live mullet; large cut bait; live blue runners",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Gag grouper move to deeper offshore ledges during winter; some remain on nearshore structure in mild winters.",
        "explanation_warm": "Gag grouper are on nearshore wrecks and reefs; they are ambush predators that require heavy tackle to keep out of structure.",
    },
    {
        "name": "Red grouper",
        "temp_min": 60, "temp_max": 82, "temp_ideal_low": 68, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live baitfish; cut squid; cut fish; live shrimp",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "4/0-7/0 circle hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Red grouper stay on deeper structure during cold months and are less accessible from nearshore.",
        "explanation_warm": "Red grouper inhabit nearshore reefs and wrecks; they are bottom ambush predators that hit live and cut baits on or near the bottom.",
    },
    {
        "name": "Scamp grouper",
        "temp_min": 60, "temp_max": 80, "temp_ideal_low": 68, "temp_ideal_high": 76,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live baitfish; squid strips; cut fish",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "4/0-6/0 circle hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Scamp grouper remain on deeper offshore structure during cold months.",
        "explanation_warm": "Scamp are considered the best-eating grouper; they are found on offshore ledges and wrecks and hit live baits and squid strips.",
    },
    {
        "name": "Black grouper",
        "temp_min": 65, "temp_max": 85, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Large live baitfish; live blue runners; live mullet",
        "rig": "Fish finder rig with very heavy leader",
        "hook_size": "6/0-9/0 circle hook",
        "sinker": "6-10 oz bank sinker",
        "explanation_cold": "Black grouper are on deeper offshore structure and rarely encountered inshore during winter.",
        "explanation_warm": "Black grouper are the largest grouper in NC waters; they inhabit wrecks and reefs and require the heaviest tackle to land.",
    },
    # --- Offshore pelagics (accessible nearshore in NC) ---
    {
        "name": "Mahi-mahi (dolphinfish)",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live baitfish; ballyhoo; small mahi trolling lures; cut fish strips",
        "rig": "Float rig with heavy fluorocarbon leader or trolling rig",
        "hook_size": "4/0-7/0 J-hook or circle hook",
        "sinker": "None or trolling weight",
        "explanation_cold": "Mahi-mahi follow warm Gulf Stream water and are not present in NC nearshore during winter.",
        "explanation_warm": "Mahi-mahi follow the Gulf Stream close to NC in summer; they school around floating debris, weedlines and temperature breaks.",
    },
    {
        "name": "Wahoo",
        "temp_min": 70, "temp_max": 84, "temp_ideal_low": 74, "temp_ideal_high": 80,
        "peak_months": [5, 6, 10, 11],
        "good_months": [4, 7, 8, 9],
        "bait": "High-speed trolling lures; live baitfish; ballyhoo rigged for trolling",
        "rig": "Float rig with wire leader or high-speed trolling rig",
        "hook_size": "6/0-9/0 J-hook or trolling hook",
        "sinker": "Trolling weight or none",
        "explanation_cold": "Wahoo follow warm water and are occasionally found near the Gulf Stream edge in late fall and winter.",
        "explanation_warm": "Wahoo are one of the fastest fish in the ocean; they are found along the Gulf Stream edge and temperature breaks near NC.",
    },
    {
        "name": "Blackfin tuna",
        "temp_min": 70, "temp_max": 84, "temp_ideal_low": 74, "temp_ideal_high": 80,
        "peak_months": [5, 6, 10, 11],
        "good_months": [4, 7, 8, 9, 12],
        "bait": "Live baitfish; trolling feathers; cedar plugs; chunk bait",
        "rig": "Float rig with fluorocarbon leader or trolling rig",
        "hook_size": "2/0-5/0 circle or J-hook",
        "sinker": "None or trolling weight",
        "explanation_cold": "Blackfin tuna are near the Gulf Stream and occasionally accessible in late fall and early winter.",
        "explanation_warm": "Blackfin tuna school along the Gulf Stream edge and over offshore structure; they hit trolled lures, live baits and chunk bait.",
    },
    {
        "name": "Yellowfin tuna",
        "temp_min": 70, "temp_max": 84, "temp_ideal_low": 74, "temp_ideal_high": 80,
        "peak_months": [5, 6, 11, 12],
        "good_months": [4, 7, 8, 9, 10, 1],
        "bait": "Live baitfish; trolling spreader bars; cedar plugs; chunk bait",
        "rig": "Float rig with heavy fluorocarbon leader or trolling rig",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "None or trolling weight",
        "explanation_cold": "Yellowfin tuna are available along the Gulf Stream edge year-round; winter trips target them at the shelf break.",
        "explanation_warm": "Yellowfin tuna are one of NC's premier offshore targets; they feed along temperature breaks, over structure and in blue water.",
    },
    {
        "name": "Atlantic bonito",
        "temp_min": 60, "temp_max": 78, "temp_ideal_low": 66, "temp_ideal_high": 74,
        "peak_months": [10, 11, 12],
        "good_months": [3, 4, 5, 9],
        "bait": "Small metal jigs; live small baitfish; trolling feathers; cut bait strips",
        "rig": "Float rig with fluorocarbon leader or casting jig",
        "hook_size": "1/0-3/0 J-hook or jig hook",
        "sinker": "None or jig weight",
        "explanation_cold": "Atlantic bonito pass through NC waters during fall and spring migrations and may be present in cooler months.",
        "explanation_warm": "Atlantic bonito school nearshore and around piers during migration; they are fast, aggressive fish that hit small jigs and live baits.",
    },
    {
        "name": "Sailfish",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Live ballyhoo; live baitfish; trolling rigged ballyhoo; teasers",
        "rig": "Float rig with heavy fluorocarbon leader or trolling rig",
        "hook_size": "6/0-8/0 circle hook",
        "sinker": "None or trolling weight",
        "explanation_cold": "Sailfish are in warmer waters to the south and rarely encountered off NC in winter.",
        "explanation_warm": "Sailfish are caught off NC when the Gulf Stream pushes close to shore; they are the premier billfish target with spectacular aerial displays.",
    },
    {
        "name": "Blue marlin",
        "temp_min": 74, "temp_max": 88, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [6, 7, 8],
        "good_months": [5, 9],
        "bait": "Large trolling lures; rigged ballyhoo; live baitfish",
        "rig": "Heavy trolling rig with wind-on leader",
        "hook_size": "9/0-12/0 J-hook or circle hook",
        "sinker": "None or trolling weight",
        "explanation_cold": "Blue marlin are in tropical waters during winter and are not present off NC.",
        "explanation_warm": "Blue marlin are the ultimate offshore trophy off NC; the Gulf Stream provides world-class fishing from May through September.",
    },
    {
        "name": "White marlin",
        "temp_min": 70, "temp_max": 84, "temp_ideal_low": 74, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small trolling lures; rigged ballyhoo; live baitfish; skirted baits",
        "rig": "Float rig or light trolling rig with fluorocarbon leader",
        "hook_size": "5/0-7/0 circle hook",
        "sinker": "None or trolling weight",
        "explanation_cold": "White marlin migrate south during winter and are not available off NC.",
        "explanation_warm": "White marlin are found along the Gulf Stream edge and over offshore canyon structure; they are acrobatic fighters on lighter tackle.",
    },
    # --- Other gamefish ---
    {
        "name": "Tripletail",
        "temp_min": 70, "temp_max": 88, "temp_ideal_low": 76, "temp_ideal_high": 84,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Live shrimp; small live crabs; small live baitfish",
        "rig": "Fish finder rig with fluorocarbon leader or free-lined",
        "hook_size": "2/0-4/0 circle hook",
        "sinker": "Small split shot or none",
        "explanation_cold": "Tripletail are in warmer southern waters during winter months.",
        "explanation_warm": "Tripletail lay on their sides near buoys, crab pot floats and floating debris; sight-cast live shrimp or small crabs to visible fish.",
    },
    {
        "name": "Ladyfish",
        "temp_min": 68, "temp_max": 88, "temp_ideal_low": 74, "temp_ideal_high": 84,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small jigs; live shrimp; small cut bait; spoons",
        "rig": "Float rig or free-lined with light tackle",
        "hook_size": "#1-2/0 J-hook",
        "sinker": "None or small split shot",
        "explanation_cold": "Ladyfish move to warmer waters during cold months and are absent from NC.",
        "explanation_warm": "Ladyfish (skipjack) are fast, acrobatic fish that school in inlets and along the surf; they hit small lures and live shrimp aggressively.",
    },
    {
        "name": "Great barracuda",
        "temp_min": 70, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Live baitfish; flashy spoons or plugs; tube lures",
        "rig": "Float rig with heavy wire leader",
        "hook_size": "4/0-7/0 J-hook or treble",
        "sinker": "None",
        "explanation_cold": "Barracuda are in warmer tropical waters during winter and rarely found off NC.",
        "explanation_warm": "Great barracuda occasionally show up around piers and nearshore structure in NC during warm months; they are explosive strikers.",
    },
    {
        "name": "Southern flounder",
        "temp_min": 50, "temp_max": 80, "temp_ideal_low": 60, "temp_ideal_high": 74,
        "peak_months": [9, 10, 11],
        "good_months": [3, 4, 5, 8],
        "bait": "Live finger mullet; live minnows; live shrimp; fresh cut mullet strips",
        "rig": "Fish finder rig with 24-36 in fluorocarbon leader",
        "hook_size": "2/0-4/0 circle or wide-gap hook",
        "sinker": "1-3 oz egg sinker",
        "explanation_cold": "Southern flounder move offshore to spawn in late fall and are scarce inshore during winter.",
        "explanation_warm": "Southern flounder are the primary flounder species in NC inshore waters; they ambush prey around inlets, docks and creek mouths.",
    },
    # --- Bottom fish & panfish ---
    {
        "name": "White grunt",
        "temp_min": 62, "temp_max": 84, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut squid; shrimp pieces; cut fish strips",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "White grunt are on deeper offshore reefs during cold months.",
        "explanation_warm": "White grunt school over nearshore hard bottom and reefs; they are reliable biters and excellent eating when pan-fried.",
    },
    {
        "name": "Red porgy",
        "temp_min": 55, "temp_max": 78, "temp_ideal_low": 62, "temp_ideal_high": 72,
        "peak_months": [3, 4, 5, 10, 11],
        "good_months": [2, 6, 9, 12],
        "bait": "Cut squid; shrimp pieces; small cut fish; clam strips",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Red porgy are on nearshore reefs year-round and are accessible during cooler months over hard bottom.",
        "explanation_warm": "Red porgy are found on nearshore and offshore reefs and hard bottom; they school over structure and readily hit cut bait.",
    },
    {
        "name": "Scup (porgy)",
        "temp_min": 50, "temp_max": 75, "temp_ideal_low": 58, "temp_ideal_high": 68,
        "peak_months": [10, 11, 12],
        "good_months": [1, 2, 3, 4, 9],
        "bait": "Squid strips; shrimp pieces; clam; bloodworms",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#2 circle hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Scup migrate nearshore during fall and winter, schooling over structure and hard bottom.",
        "explanation_warm": "Scup are transitioning to and from offshore; spring and fall offer the best nearshore action over reefs and wrecks.",
    },
    {
        "name": "Sea robin",
        "temp_min": 45, "temp_max": 75, "temp_ideal_low": 52, "temp_ideal_high": 65,
        "peak_months": [11, 12, 1, 2, 3],
        "good_months": [4, 10],
        "bait": "Squid strips; cut shrimp; bloodworms; cut fish",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Sea robins are common winter bottom fish around piers and in the surf; they crawl along the bottom using modified pectoral fins.",
        "explanation_warm": "Sea robins are moving offshore as water warms; occasional catches from piers and the surf in spring and fall.",
    },
    {
        "name": "Oyster toadfish",
        "temp_min": 50, "temp_max": 85, "temp_ideal_low": 62, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; cut fish; squid; crabs",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "#1-3/0 hook",
        "sinker": "1-3 oz sinker",
        "explanation_cold": "Toadfish hunker down in structure and are less active during cold months.",
        "explanation_warm": "Oyster toadfish lurk around pier pilings, rocks and oyster beds; they have a painful bite and loud grunting call during spawning season.",
    },
    {
        "name": "Hogfish",
        "temp_min": 65, "temp_max": 82, "temp_ideal_low": 72, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live shrimp; small crabs; fiddler crabs; sand fleas",
        "rig": "Knocker rig with light fluorocarbon leader",
        "hook_size": "#1-2/0 circle hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Hogfish remain on deeper offshore reefs during cold months.",
        "explanation_warm": "Hogfish are found on nearshore reefs and hard bottom; they are a prized catch with excellent, sweet-tasting meat.",
    },
    {
        "name": "Planehead filefish",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small shrimp pieces; tiny squid bits; clam",
        "rig": "Knocker rig with very small hooks",
        "hook_size": "#8-#4 small strong hook",
        "sinker": "Small split shot",
        "explanation_cold": "Filefish move to deeper water during cold months and are scarce inshore.",
        "explanation_warm": "Planehead filefish are common bait stealers around pier pilings and structure; they pick at baits with their tiny mouths.",
    },
    # --- Seasonal migrants & other ---
    {
        "name": "Hickory shad",
        "temp_min": 48, "temp_max": 68, "temp_ideal_low": 54, "temp_ideal_high": 64,
        "peak_months": [3, 4],
        "good_months": [2, 5],
        "bait": "Small shad darts; tiny spoons; small jigs tipped with shrimp",
        "rig": "Tandem shad dart rig",
        "hook_size": "#4-#1 shad dart or jig",
        "sinker": "1/4-1/2 oz jig head",
        "explanation_cold": "Hickory shad are staging offshore before their spring spawning run up coastal rivers.",
        "explanation_warm": "Hickory shad make massive spring runs up NC rivers; they are caught from bridges, piers and banks on small shad darts and jigs.",
    },
    {
        "name": "American shad",
        "temp_min": 48, "temp_max": 68, "temp_ideal_low": 54, "temp_ideal_high": 62,
        "peak_months": [2, 3, 4],
        "good_months": [1, 5],
        "bait": "Small shad darts; tiny spoons; small bright jigs",
        "rig": "Tandem shad dart rig",
        "hook_size": "#6-#2 shad dart",
        "sinker": "1/4-1/2 oz jig head",
        "explanation_cold": "American shad are beginning to enter NC rivers for their spring spawning run during late winter.",
        "explanation_warm": "American shad run up NC rivers in huge numbers during spring; the Cape Fear and Neuse Rivers are top destinations.",
    },
    {
        "name": "Striped mullet",
        "temp_min": 55, "temp_max": 85, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [10, 11, 12],
        "good_months": [4, 5, 6, 9],
        "bait": "Tiny bits of bread dough; oatmeal; very small hooks with dough bait; cast net",
        "rig": "Hi-lo rig with tiny hooks or cast net",
        "hook_size": "#10-#6 small bait hook",
        "sinker": "Small split shot",
        "explanation_cold": "Mullet school in inlets and deeper channels during fall migration; the fall mullet run is a major event for bait and food.",
        "explanation_warm": "Striped mullet are everywhere in the surf, inlets and ICW; they are the primary bait source and also caught for food during the fall run.",
    },
    {
        "name": "Ribbonfish (Atlantic cutlassfish)",
        "temp_min": 60, "temp_max": 82, "temp_ideal_low": 68, "temp_ideal_high": 78,
        "peak_months": [9, 10, 11],
        "good_months": [5, 6, 7, 8],
        "bait": "Small cut fish strips; shiny jigs; small live baitfish; cut shrimp",
        "rig": "Float rig with wire leader or double-dropper with wire",
        "hook_size": "#1-2/0 J-hook with wire leader",
        "sinker": "Small split shot or 1 oz",
        "explanation_cold": "Ribbonfish move to deeper water during cold months but may be present around warm-water discharges.",
        "explanation_warm": "Ribbonfish (cutlassfish) are common at piers at night; their long, silvery bodies and sharp teeth make them unmistakable.",
    },
    {
        "name": "Hardhead catfish (sea catfish)",
        "temp_min": 60, "temp_max": 90, "temp_ideal_low": 72, "temp_ideal_high": 84,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; cut fish; squid; any bait (not picky)",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "1/0-3/0 circle hook",
        "sinker": "1-3 oz pyramid sinker",
        "explanation_cold": "Hardhead catfish move to deeper channels during cold months.",
        "explanation_warm": "Hardhead catfish are one of the most common catches from piers and the surf; handle with care as their dorsal and pectoral spines are venomous.",
    },
    {
        "name": "Gafftopsail catfish",
        "temp_min": 65, "temp_max": 88, "temp_ideal_low": 74, "temp_ideal_high": 84,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; cut fish; squid; live shrimp",
        "rig": "Fish finder rig or double-dropper bottom rig",
        "hook_size": "1/0-4/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Gafftopsail catfish move offshore and to deeper water during cold months.",
        "explanation_warm": "Gafftopsail catfish are common around piers and in the surf; they fight harder than hardheads and have long, sail-like dorsal and pectoral fins.",
    },
    {
        "name": "Atlantic needlefish",
        "temp_min": 62, "temp_max": 86, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Small live baitfish; tiny pieces of cut fish; small shiny lures",
        "rig": "Float rig with light leader and small hooks",
        "hook_size": "#4-#1 long-shank hook",
        "sinker": "None or tiny split shot",
        "explanation_cold": "Needlefish move to deeper or warmer waters during cold months.",
        "explanation_warm": "Atlantic needlefish are surface predators commonly seen around piers and docks; they slash through schools of small baitfish.",
    },
    {
        "name": "Lizardfish",
        "temp_min": 60, "temp_max": 85, "temp_ideal_low": 68, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; cut fish strips; small live baitfish",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "#2-2/0 hook",
        "sinker": "1-3 oz pyramid sinker",
        "explanation_cold": "Lizardfish are in deeper water during cold months and rarely caught inshore.",
        "explanation_warm": "Lizardfish are toothy bottom ambush predators common in the surf and around piers; they aggressively hit cut bait and small live baits.",
    },
    # --- Additional sharks ---
    {
        "name": "Smooth dogfish",
        "temp_min": 50, "temp_max": 82, "temp_ideal_low": 58, "temp_ideal_high": 74,
        "peak_months": [3, 4, 5, 10, 11],
        "good_months": [2, 6, 9, 12],
        "bait": "Cut shrimp; cut fish; squid; clam; any cut bait",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "3/0-6/0 circle hook",
        "sinker": "3-5 oz pyramid sinker",
        "explanation_cold": "Smooth dogfish are less common inshore during the coldest months but may be present in deeper channels.",
        "explanation_warm": "Smooth dogfish are one of the most common sharks caught from piers and the surf; they pull hard and eat almost any cut bait on the bottom.",
    },
    {
        "name": "Spiny dogfish",
        "temp_min": 38, "temp_max": 62, "temp_ideal_low": 44, "temp_ideal_high": 56,
        "peak_months": [12, 1, 2, 3],
        "good_months": [4, 11],
        "bait": "Cut fish; cut squid; shrimp; any oily cut bait",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "2/0-4/0 circle hook",
        "sinker": "3-5 oz pyramid sinker",
        "explanation_cold": "Spiny dogfish are abundant inshore during winter, schooling along the bottom and readily hitting cut bait; handle carefully due to venomous dorsal spines.",
        "explanation_warm": "Spiny dogfish migrate to cooler northern or deeper waters during warm months and are absent from NC inshore waters.",
    },
    {
        "name": "Sand tiger shark",
        "temp_min": 62, "temp_max": 82, "temp_ideal_low": 68, "temp_ideal_high": 78,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Large cut menhaden; live bluefish; fresh mullet chunks; bonito",
        "rig": "Fish finder rig with wire leader",
        "hook_size": "8/0-12/0 circle hook",
        "sinker": "6-10 oz pyramid sinker",
        "explanation_cold": "Sand tiger sharks migrate offshore or south during cold months.",
        "explanation_warm": "Sand tiger sharks are found around wrecks and nearshore structure; they occasionally cruise near piers and are powerful but docile fighters.",
    },
    {
        "name": "Tiger shark",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Large fresh cut bait; stingray pieces; whole menhaden; bonito; large live bait",
        "rig": "Shark rig with heavy wire leader and stand-up tackle",
        "hook_size": "14/0-20/0 circle hook",
        "sinker": "8-16 oz pyramid sinker",
        "explanation_cold": "Tiger sharks are in warmer waters to the south and not present in NC nearshore during winter.",
        "explanation_warm": "Tiger sharks are the apex predator in NC waters; they patrol the surf zone and near piers eating anything. Landing one from shore is a major event.",
    },
    {
        "name": "Scalloped hammerhead shark",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Large cut menhaden; live bluefish; stingray pieces; large fresh cut bait",
        "rig": "Shark rig with heavy wire leader",
        "hook_size": "10/0-16/0 circle hook",
        "sinker": "6-10 oz pyramid sinker",
        "explanation_cold": "Hammerhead sharks have migrated south and are not present in NC during cold months.",
        "explanation_warm": "Scalloped hammerheads cruise the surf zone and near piers hunting stingrays and other prey; they are powerful fighters requiring the heaviest tackle.",
    },
    {
        "name": "Shortfin mako shark",
        "temp_min": 62, "temp_max": 82, "temp_ideal_low": 68, "temp_ideal_high": 76,
        "peak_months": [5, 6, 7],
        "good_months": [4, 8, 9, 10],
        "bait": "Large live baitfish; whole menhaden or bluefish; chunk bait",
        "rig": "Fish finder rig with heavy wire leader or trolling rig",
        "hook_size": "8/0-12/0 circle hook",
        "sinker": "6-10 oz or trolling weight",
        "explanation_cold": "Mako sharks are offshore or in warmer waters during winter months.",
        "explanation_warm": "Shortfin makos are the fastest sharks in the ocean; they are found offshore and occasionally near the beach chasing bluefish and mackerel.",
    },
    {
        "name": "Thresher shark",
        "temp_min": 58, "temp_max": 78, "temp_ideal_low": 64, "temp_ideal_high": 74,
        "peak_months": [4, 5, 10, 11],
        "good_months": [3, 6, 9],
        "bait": "Live menhaden; live bluefish; large cut bait; chunk menhaden",
        "rig": "Fish finder rig with wire leader",
        "hook_size": "8/0-12/0 circle hook",
        "sinker": "6-10 oz pyramid sinker",
        "explanation_cold": "Thresher sharks pass through NC waters during seasonal migrations and are less common in the coldest months.",
        "explanation_warm": "Thresher sharks use their incredibly long tails to stun baitfish; they pass through NC in spring and fall migrations and are prized catches.",
    },
    # --- Skates & additional rays ---
    {
        "name": "Clearnose skate",
        "temp_min": 42, "temp_max": 72, "temp_ideal_low": 48, "temp_ideal_high": 64,
        "peak_months": [11, 12, 1, 2, 3],
        "good_months": [4, 10],
        "bait": "Cut shrimp; cut squid; clam; cut fish; bloodworms",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "1/0-4/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Clearnose skates are very common bottom catches from piers during winter; they are flat, diamond-shaped rays with translucent nose patches.",
        "explanation_warm": "Clearnose skates move to deeper, cooler water as temperatures rise and are uncommon inshore during summer.",
    },
    {
        "name": "Atlantic stingray",
        "temp_min": 58, "temp_max": 88, "temp_ideal_low": 68, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; squid; cut fish",
        "rig": "Fish finder rig",
        "hook_size": "1/0-3/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Atlantic stingrays are in deeper water during cold months and less likely to be encountered.",
        "explanation_warm": "Atlantic stingrays are the small, very common stingrays that bury in the sand in the surf zone; shuffle your feet to avoid stepping on them.",
    },
    {
        "name": "Bluntnose stingray",
        "temp_min": 60, "temp_max": 85, "temp_ideal_low": 68, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; squid strips; cut clam; cut fish",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "2/0-5/0 circle hook",
        "sinker": "3-5 oz pyramid sinker",
        "explanation_cold": "Bluntnose stingrays are in deeper water or have migrated during cold months.",
        "explanation_warm": "Bluntnose stingrays are mid-size rays common in the surf and around piers; they are frequently caught incidentally while bottom fishing.",
    },
    {
        "name": "Butterfly ray",
        "temp_min": 62, "temp_max": 84, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; squid; clam; cut fish",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "3/0-6/0 circle hook",
        "sinker": "3-5 oz pyramid sinker",
        "explanation_cold": "Butterfly rays move offshore during cold months.",
        "explanation_warm": "Butterfly rays are broad, flat rays that inhabit sandy bottoms in the surf zone; they are strong fighters frequently hooked while bottom fishing.",
    },
    {
        "name": "Eagle ray (spotted)",
        "temp_min": 65, "temp_max": 86, "temp_ideal_low": 72, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; clam; crab pieces; cut fish",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "4/0-7/0 circle hook",
        "sinker": "4-6 oz pyramid sinker",
        "explanation_cold": "Spotted eagle rays migrate to warmer waters during winter.",
        "explanation_warm": "Spotted eagle rays are spectacular rays that swim in groups near the surface; occasionally hooked from piers, they are powerful fighters.",
    },
    # --- Panfish, bottom fish & bait species ---
    {
        "name": "Silver perch",
        "temp_min": 55, "temp_max": 82, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [4, 5, 6, 9, 10],
        "good_months": [3, 7, 8, 11],
        "bait": "Small pieces of shrimp; bloodworms; squid bits; Fishbites",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#2 circle or bait hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "Silver perch move to deeper channels during cold months and are uncommon inshore.",
        "explanation_warm": "Silver perch are common panfish caught from piers and the surf; they school in good numbers and bite small baits readily.",
    },
    {
        "name": "Sand perch",
        "temp_min": 58, "temp_max": 82, "temp_ideal_low": 66, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Small pieces of shrimp; squid strips; cut fish; bloodworms",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "1-3 oz bank sinker",
        "explanation_cold": "Sand perch are in deeper water during cold months and seldom caught inshore.",
        "explanation_warm": "Sand perch are colorful, aggressive little bottom fish found over hard bottom and around structure; they readily hit small cut baits.",
    },
    {
        "name": "Tomtate grunt",
        "temp_min": 62, "temp_max": 84, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Small squid strips; shrimp pieces; cut fish bits",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "1-3 oz bank sinker",
        "explanation_cold": "Tomtate grunt move to deeper reefs during cold months.",
        "explanation_warm": "Tomtate are small, abundant grunts that school around pier pilings and nearshore structure; they are reliable biters and useful as live bait.",
    },
    {
        "name": "Bermuda chub (sea chub)",
        "temp_min": 65, "temp_max": 85, "temp_ideal_low": 72, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small pieces of shrimp; bread; algae; tiny cut baits; sand fleas",
        "rig": "Knocker rig with small hooks and light fluorocarbon leader",
        "hook_size": "#4-#1 circle hook",
        "sinker": "Small split shot or 1 oz egg",
        "explanation_cold": "Bermuda chub move to deeper water or are absent inshore during cold months.",
        "explanation_warm": "Bermuda chub (sea chub) school around pier pilings in large numbers, feeding on algae and small invertebrates; they fight hard for their size.",
    },
    {
        "name": "Butterfish",
        "temp_min": 48, "temp_max": 72, "temp_ideal_low": 55, "temp_ideal_high": 65,
        "peak_months": [10, 11, 12],
        "good_months": [1, 2, 3, 4, 9],
        "bait": "Small pieces of shrimp; squid bits; plankton-imitating tiny baits",
        "rig": "Hi-lo rig with very small hooks or sabiki",
        "hook_size": "#8-#4 small bait hook",
        "sinker": "1-2 oz bank sinker",
        "explanation_cold": "Butterfish are common inshore during cooler months, schooling around piers and structure in large numbers.",
        "explanation_warm": "Butterfish move to deeper, cooler water during warm months and are uncommon inshore.",
    },
    {
        "name": "Harvestfish",
        "temp_min": 50, "temp_max": 75, "temp_ideal_low": 58, "temp_ideal_high": 68,
        "peak_months": [10, 11, 4, 5],
        "good_months": [3, 9, 12],
        "bait": "Tiny pieces of shrimp; jellyfish bits; plankton; very small baits",
        "rig": "Hi-lo rig with very small hooks or sabiki",
        "hook_size": "#8-#4 small bait hook",
        "sinker": "1-2 oz bank sinker",
        "explanation_cold": "Harvestfish are present inshore during cooler transitional months, often associated with jellyfish.",
        "explanation_warm": "Harvestfish move offshore during the warmest months; they are most common in spring and fall when jellyfish are abundant.",
    },
    {
        "name": "Sand seatrout (white trout)",
        "temp_min": 55, "temp_max": 85, "temp_ideal_low": 65, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live shrimp; small cut fish; squid strips; cut shrimp",
        "rig": "Fish finder rig with light fluorocarbon leader",
        "hook_size": "1/0-3/0 circle hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Sand seatrout move to deeper channels and are less common inshore during cold months.",
        "explanation_warm": "Sand seatrout are smaller cousins of speckled trout, common from piers and in the surf; they school in good numbers and hit shrimp and cut bait.",
    },
    {
        "name": "Star drum",
        "temp_min": 58, "temp_max": 84, "temp_ideal_low": 66, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Shrimp pieces; bloodworms; squid; sand fleas",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "1-3 oz pyramid sinker",
        "explanation_cold": "Star drum move to deeper water during cold months and are uncommon inshore.",
        "explanation_warm": "Star drum are small members of the drum family caught from the surf and piers; they resemble spot and croaker and bite similar baits.",
    },
    {
        "name": "Banded drum",
        "temp_min": 60, "temp_max": 85, "temp_ideal_low": 68, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; sand fleas; bloodworms; small cut fish",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Banded drum move to deeper water during cold months.",
        "explanation_warm": "Banded drum are distinctly marked members of the drum family found in the surf and around piers; they bite shrimp and sand fleas on bottom rigs.",
    },
    # --- Bait species (commonly targeted from piers) ---
    {
        "name": "Atlantic menhaden (bunker)",
        "temp_min": 50, "temp_max": 85, "temp_ideal_low": 60, "temp_ideal_high": 78,
        "peak_months": [4, 5, 6, 9, 10, 11],
        "good_months": [3, 7, 8, 12],
        "bait": "Sabiki rigs; tiny gold hooks; cast net (most effective)",
        "rig": "Sabiki rig or gold-hook bait rig",
        "hook_size": "#10-#6 sabiki or gold hook",
        "sinker": "1-2 oz weight below sabiki",
        "explanation_cold": "Menhaden are offshore in large schools during the coldest months; some remain in deeper inshore channels.",
        "explanation_warm": "Atlantic menhaden are THE most important bait fish in NC; massive schools move along the beach and through inlets. Cast net or sabiki rig them for bait.",
    },
    {
        "name": "Round scad (cigar minnow)",
        "temp_min": 65, "temp_max": 85, "temp_ideal_low": 72, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Sabiki rigs; tiny jigs; small pieces of shrimp",
        "rig": "Sabiki rig with small weight",
        "hook_size": "#10-#6 sabiki hook",
        "sinker": "1-2 oz weight below sabiki",
        "explanation_cold": "Round scad are offshore or have migrated south during cold months.",
        "explanation_warm": "Cigar minnows school around piers and nearshore structure; they are the premier live bait for king mackerel and other pelagics.",
    },
    {
        "name": "Ballyhoo (balao)",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Sabiki rigs; tiny pieces of shrimp; chum; small gold hooks",
        "rig": "Sabiki rig or small gold-hook bait rig",
        "hook_size": "#12-#8 sabiki or small hook",
        "sinker": "1/2-1 oz weight below sabiki",
        "explanation_cold": "Ballyhoo move to warmer waters during cold months and are not present inshore.",
        "explanation_warm": "Ballyhoo are needlefish-like bait fish that school near the surface around piers; they are top trolling bait for billfish and kingfish.",
    },
    {
        "name": "Atlantic thread herring (greenback)",
        "temp_min": 65, "temp_max": 86, "temp_ideal_low": 72, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Sabiki rigs; cast net; tiny gold hooks; chum",
        "rig": "Sabiki rig or cast net",
        "hook_size": "#10-#6 sabiki hook",
        "sinker": "1-2 oz weight below sabiki",
        "explanation_cold": "Thread herring migrate south or offshore during cold months.",
        "explanation_warm": "Atlantic thread herring (greenbacks) are excellent live bait schooling around piers; they are oily and attract king mackerel, tarpon and cobia.",
    },
    {
        "name": "Bigeye scad",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Sabiki rigs; tiny jigs; small pieces of shrimp",
        "rig": "Sabiki rig with small weight",
        "hook_size": "#10-#6 sabiki hook",
        "sinker": "1-2 oz weight below sabiki",
        "explanation_cold": "Bigeye scad are offshore or have migrated during cold months.",
        "explanation_warm": "Bigeye scad are large-eyed bait fish that school around piers and lights at night; they are hardy live baits for larger gamefish.",
    },
    {
        "name": "Spanish sardine",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Sabiki rigs; cast net; tiny gold hooks; chum",
        "rig": "Sabiki rig or cast net",
        "hook_size": "#10-#6 sabiki hook",
        "sinker": "1-2 oz weight below sabiki",
        "explanation_cold": "Spanish sardines are in warmer waters to the south during cold months.",
        "explanation_warm": "Spanish sardines school in large numbers around piers and nearshore structure; they are excellent live and cut bait for pelagics.",
    },
    # --- Additional jacks & pelagics ---
    {
        "name": "African pompano",
        "temp_min": 68, "temp_max": 84, "temp_ideal_low": 74, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Live baitfish; large jigs; live crabs; cut bait strips",
        "rig": "Fish finder rig with heavy fluorocarbon leader or vertical jig",
        "hook_size": "4/0-7/0 circle hook or jig hook",
        "sinker": "4-8 oz bank sinker or jig head",
        "explanation_cold": "African pompano are on deeper offshore structure during cold months.",
        "explanation_warm": "African pompano are found on nearshore wrecks and reefs; juveniles have long, trailing filaments and adults are powerful, deep-bodied fighters.",
    },
    {
        "name": "Bar jack",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Small live baitfish; small jigs; cut bait; live shrimp",
        "rig": "Float rig or free-lined with light tackle",
        "hook_size": "1/0-3/0 circle or J-hook",
        "sinker": "None or small split shot",
        "explanation_cold": "Bar jacks are in warmer tropical waters and not present in NC during cold months.",
        "explanation_warm": "Bar jacks occasionally appear around NC piers and nearshore structure during peak summer when water is warmest.",
    },
    {
        "name": "Rainbow runner",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Live baitfish; small jigs; trolling feathers; cut fish strips",
        "rig": "Float rig with fluorocarbon leader or trolling rig",
        "hook_size": "2/0-5/0 circle or J-hook",
        "sinker": "None or trolling weight",
        "explanation_cold": "Rainbow runners are in warmer offshore waters and not available near NC in cold months.",
        "explanation_warm": "Rainbow runners are colorful, fast-swimming jacks found around offshore structure and occasionally near piers; they are strong fighters on light tackle.",
    },
    {
        "name": "Skipjack tuna",
        "temp_min": 68, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small jigs; trolling feathers; cedar plugs; live baitfish",
        "rig": "Float rig with fluorocarbon leader or trolling rig",
        "hook_size": "2/0-5/0 J-hook or circle hook",
        "sinker": "None or trolling weight",
        "explanation_cold": "Skipjack tuna are offshore in warm Gulf Stream water and not accessible nearshore in winter.",
        "explanation_warm": "Skipjack tuna school in large numbers near the Gulf Stream edge; they are fast, hard-fighting fish useful as bait for marlin and sharks.",
    },
    # --- Additional snappers ---
    {
        "name": "Yellowtail snapper",
        "temp_min": 70, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Small pieces of shrimp; cut fish; small jigs; live pilchards",
        "rig": "Float rig with fluorocarbon leader or free-lined",
        "hook_size": "#1-2/0 circle hook",
        "sinker": "None or small split shot",
        "explanation_cold": "Yellowtail snapper are a tropical species not present in NC during cold months.",
        "explanation_warm": "Yellowtail snapper occasionally appear around NC nearshore reefs and structure during peak warm months; they are wary and require light tackle.",
    },
    {
        "name": "Mutton snapper",
        "temp_min": 70, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 84,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Live shrimp; live crabs; cut fish; live baitfish",
        "rig": "Fish finder rig with fluorocarbon leader",
        "hook_size": "2/0-5/0 circle hook",
        "sinker": "2-4 oz egg sinker",
        "explanation_cold": "Mutton snapper are in warmer southern waters during cold months.",
        "explanation_warm": "Mutton snapper occasionally visit NC reefs and wrecks during peak summer; they are hard-fighting, excellent-eating snapper.",
    },
    # --- Additional reef & wrasse species ---
    {
        "name": "Black snapper (black sea bass juvenile)",
        "temp_min": 55, "temp_max": 80, "temp_ideal_low": 62, "temp_ideal_high": 74,
        "peak_months": [4, 5, 9, 10, 11],
        "good_months": [3, 6, 12],
        "bait": "Tiny squid strips; small shrimp pieces; bloodworms",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#2 circle hook",
        "sinker": "1-3 oz bank sinker",
        "explanation_cold": "Juvenile black sea bass are common around pier pilings and jetties during cooler months, feeding on small crustaceans.",
        "explanation_warm": "Juvenile black sea bass school around structure in good numbers; they are fun to catch on light tackle and make excellent cut bait.",
    },
    {
        "name": "Slippery dick (wrasse)",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small shrimp pieces; tiny squid bits; sand fleas",
        "rig": "Knocker rig with very small hooks",
        "hook_size": "#8-#4 small hook",
        "sinker": "Small split shot",
        "explanation_cold": "Slippery dick wrasse are inactive or on deeper reefs during cold months.",
        "explanation_warm": "Slippery dick are small, colorful wrasses common around pier pilings and rocky structure; they are aggressive bait stealers.",
    },
    {
        "name": "Cunner (bergall)",
        "temp_min": 40, "temp_max": 68, "temp_ideal_low": 46, "temp_ideal_high": 60,
        "peak_months": [11, 12, 1, 2, 3],
        "good_months": [4, 10],
        "bait": "Small shrimp pieces; clam; bloodworms; sandworms",
        "rig": "Knocker rig with small hooks",
        "hook_size": "#6-#2 small strong hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Cunner are small wrasse-like fish that cling to structure year-round; they are one of the few species that consistently bite in cold water.",
        "explanation_warm": "Cunner become less active in warmer water and are replaced by other structure species.",
    },
    # --- Additional miscellaneous species ---
    {
        "name": "Remora (sharksucker)",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut fish; shrimp; squid (usually caught incidentally)",
        "rig": "Any bottom or float rig (incidental catch)",
        "hook_size": "1/0-4/0 hook",
        "sinker": "Varies",
        "explanation_cold": "Remoras follow large marine animals to warmer waters during winter.",
        "explanation_warm": "Remoras (sharksuckers) are caught incidentally from piers, usually detached from sharks, rays or sea turtles; they have a suction disc on their head.",
    },
    {
        "name": "Atlantic bumper",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small pieces of shrimp; tiny jigs; sabiki rigs",
        "rig": "Sabiki rig or double-dropper with small hooks",
        "hook_size": "#6-#2 small hook",
        "sinker": "1/2-1 oz",
        "explanation_cold": "Atlantic bumpers move to warmer waters during cold months.",
        "explanation_warm": "Atlantic bumpers are small, silvery fish that school in large numbers around pier pilings; they are common incidental catches and useful as bait.",
    },
    {
        "name": "Windowpane flounder",
        "temp_min": 42, "temp_max": 70, "temp_ideal_low": 48, "temp_ideal_high": 62,
        "peak_months": [11, 12, 1, 2, 3],
        "good_months": [4, 10],
        "bait": "Small shrimp pieces; squid strips; bloodworms; small minnows",
        "rig": "Fish finder rig with light fluorocarbon leader",
        "hook_size": "#4-#1 circle or wide-gap hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Windowpane flounder are present inshore during cooler months; they are thin, nearly translucent flatfish found on sandy bottom.",
        "explanation_warm": "Windowpane flounder move to deeper, cooler water during warm months and are uncommon inshore.",
    },
    {
        "name": "Spottail pinfish",
        "temp_min": 58, "temp_max": 84, "temp_ideal_low": 68, "temp_ideal_high": 80,
        "peak_months": [4, 5, 6, 7, 8, 9],
        "good_months": [3, 10],
        "bait": "Small shrimp pieces; bread; squid bits; sand fleas",
        "rig": "Hi-lo rig with very small hooks",
        "hook_size": "#8-#4 bait hook",
        "sinker": "1/2-1 oz split shot or pyramid",
        "explanation_cold": "Spottail pinfish move to deeper water during cold months.",
        "explanation_warm": "Spottail pinfish are common around piers and structure; they are similar to regular pinfish but have a distinct dark spot near the tail.",
    },
    {
        "name": "Sheepshead minnow (killifish)",
        "temp_min": 50, "temp_max": 90, "temp_ideal_low": 65, "temp_ideal_high": 82,
        "peak_months": [4, 5, 6, 7, 8, 9, 10],
        "good_months": [3, 11],
        "bait": "Tiny pieces of bread; small bits of shrimp; cast net or minnow trap",
        "rig": "Minnow trap or cast net (too small for hook and line)",
        "hook_size": "#12-#8 micro hook",
        "sinker": "Tiny split shot",
        "explanation_cold": "Sheepshead minnows are less active during cold months but remain in shallow marshes and tidal pools.",
        "explanation_warm": "Sheepshead minnows (killifish) are tiny, hardy bait fish found in shallow water and tidal pools; they are excellent live bait for flounder.",
    },
    {
        "name": "Mojarra (yellowfin mojarra)",
        "temp_min": 65, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small pieces of shrimp; tiny squid bits; small worms",
        "rig": "Hi-lo rig with very small hooks",
        "hook_size": "#6-#2 small hook",
        "sinker": "1/2-1 oz split shot",
        "explanation_cold": "Mojarras move to deeper water or are absent during cold months.",
        "explanation_warm": "Mojarras are small, silvery fish common around piers and in the surf; they school in good numbers and are used as live bait for flounder and reds.",
    },
    {
        "name": "Blueline tilefish",
        "temp_min": 55, "temp_max": 75, "temp_ideal_low": 60, "temp_ideal_high": 70,
        "peak_months": [4, 5, 6, 9, 10, 11],
        "good_months": [3, 7, 8, 12],
        "bait": "Cut squid; cut fish; clam strips; live baitfish",
        "rig": "Hi-lo rig with heavy weight (deep water)",
        "hook_size": "4/0-7/0 circle hook",
        "sinker": "8-16 oz bank sinker",
        "explanation_cold": "Blueline tilefish remain on deep structure year-round; they are accessible in cooler months from deep-drop rigs.",
        "explanation_warm": "Blueline tilefish inhabit deep ledges and hard bottom in 200-600 ft; they are premium table fare caught on deep-drop rigs with cut squid.",
    },
    {
        "name": "Golden tilefish",
        "temp_min": 48, "temp_max": 68, "temp_ideal_low": 52, "temp_ideal_high": 62,
        "peak_months": [3, 4, 5, 10, 11],
        "good_months": [2, 6, 9, 12],
        "bait": "Cut squid; cut fish; clam; live crabs",
        "rig": "Deep-drop bottom rig with heavy weight and electric reel",
        "hook_size": "6/0-9/0 circle hook",
        "sinker": "2-5 lb bank sinker or deep-drop weight",
        "explanation_cold": "Golden tilefish are on deep bottom year-round and accessible via deep-drop rigs in winter.",
        "explanation_warm": "Golden tilefish live in burrows on the continental shelf edge in 500-1000+ ft; they are caught on deep-drop rigs and are outstanding table fare.",
    },
    {
        "name": "Snowy grouper",
        "temp_min": 48, "temp_max": 68, "temp_ideal_low": 52, "temp_ideal_high": 62,
        "peak_months": [4, 5, 6, 9, 10, 11],
        "good_months": [3, 7, 8, 12],
        "bait": "Cut squid; cut fish; live baitfish",
        "rig": "Deep-drop bottom rig with heavy weight",
        "hook_size": "6/0-9/0 circle hook",
        "sinker": "2-5 lb bank sinker",
        "explanation_cold": "Snowy grouper remain on deep structure year-round and are accessible via deep-drop rigs.",
        "explanation_warm": "Snowy grouper inhabit deep reefs and ledges in 300-600+ ft; they are caught on deep-drop rigs and are excellent table fare.",
    },
    {
        "name": "Wreckfish",
        "temp_min": 45, "temp_max": 62, "temp_ideal_low": 48, "temp_ideal_high": 58,
        "peak_months": [1, 2, 3, 12],
        "good_months": [4, 11],
        "bait": "Large cut squid; whole fish; large cut bait",
        "rig": "Deep-drop bottom rig with very heavy weight and electric reel",
        "hook_size": "9/0-14/0 circle hook",
        "sinker": "3-8 lb bank sinker",
        "explanation_cold": "Wreckfish are deep-water grouper-like fish found on deep structure in 1000+ ft; winter is peak season off NC.",
        "explanation_warm": "Wreckfish are in very deep, cold water year-round; they are less commonly targeted in summer but remain accessible via deep-drop rigs.",
    },
    {
        "name": "Tilefish (blueline juvenile/grey)",
        "temp_min": 55, "temp_max": 75, "temp_ideal_low": 60, "temp_ideal_high": 70,
        "peak_months": [5, 6, 9, 10],
        "good_months": [4, 7, 8, 11],
        "bait": "Cut squid strips; small cut fish; clam bits",
        "rig": "Hi-lo rig for deep water",
        "hook_size": "3/0-6/0 circle hook",
        "sinker": "6-12 oz bank sinker",
        "explanation_cold": "Grey tilefish are on moderate-depth structure and accessible year-round with deep rigs.",
        "explanation_warm": "Grey tilefish inhabit hard bottom and ledges in 150-400 ft; they are reliable biters on cut squid and useful for filling coolers.",
    },
    # --- Eels & oddities ---
    {
        "name": "American eel",
        "temp_min": 45, "temp_max": 80, "temp_ideal_low": 55, "temp_ideal_high": 72,
        "peak_months": [9, 10, 11],
        "good_months": [4, 5, 6, 7, 8],
        "bait": "Cut fish; shrimp; worms; chicken liver",
        "rig": "Fish finder rig or double-dropper bottom rig",
        "hook_size": "1/0-4/0 circle hook",
        "sinker": "1-3 oz egg sinker",
        "explanation_cold": "American eels are less active during cold months but can be found in deeper channels and around structure.",
        "explanation_warm": "American eels are caught around piers, docks and inlets, especially at night; they are slimy and hard to handle but make outstanding live bait for stripers and cobia.",
    },
    {
        "name": "Conger eel",
        "temp_min": 50, "temp_max": 78, "temp_ideal_low": 58, "temp_ideal_high": 70,
        "peak_months": [4, 5, 9, 10, 11],
        "good_months": [3, 6, 12],
        "bait": "Cut fish; squid; fresh menhaden; cut bluefish",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "3/0-7/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Conger eels are in deeper nearshore structure and wrecks during cold months; they are nocturnal bottom feeders.",
        "explanation_warm": "Conger eels inhabit wrecks and rocky structure; they are caught from piers at night and can grow very large. Handle with caution — they bite.",
    },
    # --- Flatfish ---
    {
        "name": "Gulf flounder",
        "temp_min": 55, "temp_max": 82, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [4, 5, 9, 10, 11],
        "good_months": [3, 6, 7, 8],
        "bait": "Live finger mullet; live shrimp; live minnows; cut mullet strips",
        "rig": "Fish finder rig with 24-36 in fluorocarbon leader",
        "hook_size": "2/0-4/0 circle or wide-gap hook",
        "sinker": "1-3 oz egg sinker",
        "explanation_cold": "Gulf flounder move offshore during the coldest months; occasional catches near inlets on warmer days.",
        "explanation_warm": "Gulf flounder are found alongside summer and southern flounder in the surf and around inlets; live finger mullet on the bottom is the top bait.",
    },
    {
        "name": "Hogchoker",
        "temp_min": 50, "temp_max": 82, "temp_ideal_low": 60, "temp_ideal_high": 76,
        "peak_months": [4, 5, 6, 7, 8, 9],
        "good_months": [3, 10],
        "bait": "Bloodworms; tiny shrimp pieces; small worms",
        "rig": "Hi-lo rig with very small hooks",
        "hook_size": "#8-#4 small bait hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "Hogchokers are in deeper channels and estuaries during cold months.",
        "explanation_warm": "Hogchokers are tiny sole-like flatfish common in estuaries and around piers; they are too small to eat but are frequently caught on small baits.",
    },
    # --- Additional porgies ---
    {
        "name": "Knobbed porgy",
        "temp_min": 60, "temp_max": 82, "temp_ideal_low": 68, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut squid; shrimp pieces; cut fish; clam strips",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Knobbed porgy are on deeper offshore structure during cold months.",
        "explanation_warm": "Knobbed porgy are found on nearshore and offshore hard bottom; they have a distinctive knobby forehead and readily hit cut bait.",
    },
    {
        "name": "Whitebone porgy",
        "temp_min": 60, "temp_max": 82, "temp_ideal_low": 68, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10, 11],
        "bait": "Cut squid; shrimp pieces; small cut fish; sand fleas",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Whitebone porgy remain on offshore reefs during cold months.",
        "explanation_warm": "Whitebone porgy are common on nearshore reefs and hard bottom off NC; they school over structure and are reliable bottom biters.",
    },
    {
        "name": "Jolthead porgy",
        "temp_min": 62, "temp_max": 84, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut squid; shrimp; clam; small crabs; sand fleas",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "#1-3/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Jolthead porgy are on deeper reefs and less accessible during cold months.",
        "explanation_warm": "Jolthead porgy are the largest porgy species in NC waters; they inhabit reefs and hard bottom and put up a good fight on bottom rigs.",
    },
    {
        "name": "Grass porgy",
        "temp_min": 62, "temp_max": 82, "temp_ideal_low": 70, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Small squid strips; tiny shrimp pieces; sand fleas",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Grass porgy move to deeper structure during cold months.",
        "explanation_warm": "Grass porgy are small, colorful porgies found over nearshore hard bottom and grass beds; they are aggressive biters on small cut baits.",
    },
    {
        "name": "Saucereye porgy",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut squid; shrimp pieces; small cut fish",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Saucereye porgy are on deeper offshore reefs during cold months.",
        "explanation_warm": "Saucereye porgy have distinctively large eyes and inhabit nearshore and offshore reefs; they school over hard bottom and hit cut bait readily.",
    },
    # --- Additional grunts ---
    {
        "name": "Blue-striped grunt",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut squid; shrimp pieces; cut fish bits; small live shrimp",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Blue-striped grunt are on deeper reefs or have moved south during cold months.",
        "explanation_warm": "Blue-striped grunt are colorful reef fish that school around pier pilings and nearshore structure in warm months; they are aggressive biters.",
    },
    {
        "name": "Sailor's choice grunt",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut squid; small shrimp pieces; cut fish; bloodworms",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Sailor's choice grunt are on deeper reefs during cold months.",
        "explanation_warm": "Sailor's choice are small grunts common around nearshore reefs and structure; they bite readily and make good cut bait for larger species.",
    },
    {
        "name": "Margate",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut squid; shrimp; cut fish; small crabs",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "#1-3/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Margate are on deeper offshore reefs during cold months.",
        "explanation_warm": "Margate are the largest grunt species; they inhabit nearshore reefs and occasionally piers, and are excellent table fare when caught.",
    },
    {
        "name": "Porkfish",
        "temp_min": 70, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Small pieces of shrimp; cut squid; tiny cut fish",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "1-3 oz bank sinker",
        "explanation_cold": "Porkfish are in warmer waters to the south during cold months.",
        "explanation_warm": "Porkfish are striking yellow-and-black striped grunts that occasionally appear around NC piers and reefs during peak summer.",
    },
    # --- Puffers, triggers & filefish ---
    {
        "name": "Bandtail puffer",
        "temp_min": 60, "temp_max": 84, "temp_ideal_low": 68, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Small pieces of shrimp; squid bits; cut clam",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#2 baitholder or circle hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "Bandtail puffers move to deeper water during cold months.",
        "explanation_warm": "Bandtail puffers are larger than northern puffers and common around piers and structure in warmer months; they have tasty meat behind the head.",
    },
    {
        "name": "Checkered puffer",
        "temp_min": 62, "temp_max": 84, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Shrimp pieces; squid; cut clam; sand fleas",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#2 baitholder hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "Checkered puffers are in deeper water during cold months.",
        "explanation_warm": "Checkered puffers are common around pier pilings and grass flats; they are notorious bait stealers with strong beaks that crush shells.",
    },
    {
        "name": "Striped burrfish (spiny boxfish)",
        "temp_min": 55, "temp_max": 80, "temp_ideal_low": 62, "temp_ideal_high": 74,
        "peak_months": [10, 11, 12, 3, 4],
        "good_months": [1, 2, 5, 9],
        "bait": "Cut shrimp; clam; squid; cut crab",
        "rig": "Hi-lo rig",
        "hook_size": "#4-#1 circle hook",
        "sinker": "1-3 oz pyramid sinker",
        "explanation_cold": "Striped burrfish are common inshore during cooler months, inflating into spiny balls when caught; they inhabit structure and grass beds.",
        "explanation_warm": "Striped burrfish move to deeper water during the warmest months; they are most common inshore during transitional seasons.",
    },
    {
        "name": "Scrawled cowfish",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small shrimp pieces; tiny squid bits; cut clam",
        "rig": "Knocker rig with very small hooks",
        "hook_size": "#6-#2 small hook",
        "sinker": "Small split shot or 1 oz egg",
        "explanation_cold": "Scrawled cowfish are in deeper water or warmer areas during cold months.",
        "explanation_warm": "Scrawled cowfish are quirky, box-shaped fish with horns; they are caught around pier pilings and structure and release a toxin when stressed.",
    },
    {
        "name": "Ocean triggerfish",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut squid; small shrimp; sand fleas; small crabs",
        "rig": "Knocker rig with small strong hooks",
        "hook_size": "#4-#1 strong short-shank hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Ocean triggerfish move to deeper offshore waters during cold months.",
        "explanation_warm": "Ocean triggerfish are found on nearshore reefs and around pier structure; they are smaller than gray triggers but equally adept at stealing bait.",
    },
    {
        "name": "Queen triggerfish",
        "temp_min": 70, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Cut squid; small crabs; sand fleas; shrimp pieces",
        "rig": "Knocker rig with small strong hooks",
        "hook_size": "#4-#1 strong short-shank hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Queen triggerfish are in warmer tropical waters during cold months.",
        "explanation_warm": "Queen triggerfish are the most colorful triggerfish, occasionally appearing on NC reefs and around structure during peak summer.",
    },
    {
        "name": "Scrawled filefish",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Tiny shrimp pieces; jellyfish; small squid bits",
        "rig": "Knocker rig with very small hooks",
        "hook_size": "#8-#4 small strong hook",
        "sinker": "Small split shot",
        "explanation_cold": "Scrawled filefish move offshore during cold months.",
        "explanation_warm": "Scrawled filefish are the largest filefish species, with blue scrawl markings; they drift near pier pilings and structure eating jellyfish and small invertebrates.",
    },
    {
        "name": "Orange filefish",
        "temp_min": 62, "temp_max": 82, "temp_ideal_low": 70, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Small shrimp pieces; tiny squid; cut clam",
        "rig": "Knocker rig with very small hooks",
        "hook_size": "#8-#4 small hook",
        "sinker": "Small split shot",
        "explanation_cold": "Orange filefish are on deeper structure during cold months.",
        "explanation_warm": "Orange filefish are medium-sized filefish found around piers and reefs; they pick at baits with tiny mouths and change color to match surroundings.",
    },
    # --- Additional sharks ---
    {
        "name": "Nurse shark",
        "temp_min": 70, "temp_max": 88, "temp_ideal_low": 76, "temp_ideal_high": 84,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Cut fish; squid; live crabs; shrimp; any bottom bait",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "3-6 oz pyramid sinker",
        "explanation_cold": "Nurse sharks are in warmer southern waters during cold months.",
        "explanation_warm": "Nurse sharks occasionally appear in NC during peak summer; they are docile bottom dwellers that rest under ledges and feed on crustaceans and small fish.",
    },
    {
        "name": "Finetooth shark",
        "temp_min": 68, "temp_max": 88, "temp_ideal_low": 74, "temp_ideal_high": 84,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut menhaden; cut mullet; live small baitfish; fresh cut fish",
        "rig": "Fish finder rig with wire leader",
        "hook_size": "4/0-7/0 circle hook",
        "sinker": "3-5 oz pyramid sinker",
        "explanation_cold": "Finetooth sharks migrate south during cold months.",
        "explanation_warm": "Finetooth sharks are fast, slender sharks common in the surf zone during summer; they aggressively chase baitfish and fight hard for their size.",
    },
    {
        "name": "Atlantic angel shark",
        "temp_min": 50, "temp_max": 72, "temp_ideal_low": 55, "temp_ideal_high": 66,
        "peak_months": [11, 12, 1, 2, 3],
        "good_months": [4, 10],
        "bait": "Cut fish; squid; shrimp; any bottom bait",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "3/0-6/0 circle hook",
        "sinker": "3-5 oz pyramid sinker",
        "explanation_cold": "Atlantic angel sharks are flat, ray-like sharks that bury in sand on the bottom; they are caught inshore during cooler months from piers and the surf.",
        "explanation_warm": "Angel sharks move to deeper, cooler water during warm months and are uncommon inshore.",
    },
    # --- Additional jacks ---
    {
        "name": "Yellow jack",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Live baitfish; small jigs; cut bait; live shrimp",
        "rig": "Float rig or free-lined with medium tackle",
        "hook_size": "1/0-4/0 circle or J-hook",
        "sinker": "None or small split shot",
        "explanation_cold": "Yellow jacks are in tropical waters and not present in NC during cold months.",
        "explanation_warm": "Yellow jacks occasionally visit NC waters during peak summer; they are beautiful golden fish that fight hard and school around structure.",
    },
    {
        "name": "Horse-eye jack",
        "temp_min": 70, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Live baitfish; jigs; cut bait strips; live shrimp",
        "rig": "Float rig or Carolina rig with medium-heavy tackle",
        "hook_size": "2/0-5/0 circle or J-hook",
        "sinker": "None or 1-2 oz egg sinker",
        "explanation_cold": "Horse-eye jacks are in warmer waters to the south during cold months.",
        "explanation_warm": "Horse-eye jacks are large-eyed jacks that occasionally school around NC piers and nearshore structure during warm months; they are strong fighters.",
    },
    {
        "name": "Palometa",
        "temp_min": 70, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Sand fleas; small shrimp; small jigs; Fishbites",
        "rig": "Hi-lo rig with small hooks or float rig",
        "hook_size": "#2-1/0 circle hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "Palometa are a tropical species not found in NC during cold months.",
        "explanation_warm": "Palometa are small, deep-bodied jacks related to permit; they occasionally appear in the NC surf zone during peak summer and hit sand fleas.",
    },
    # --- Additional pelagics ---
    {
        "name": "Cero mackerel",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Small shiny spoons; live baitfish; small plugs",
        "rig": "Float rig with wire leader",
        "hook_size": "#1-2/0 long-shank hook",
        "sinker": "None or small split shot",
        "explanation_cold": "Cero mackerel are in warmer tropical waters during cold months.",
        "explanation_warm": "Cero mackerel resemble Spanish mackerel but with spots arranged in rows; they occasionally show up around NC piers during peak summer.",
    },
    {
        "name": "Bigeye tuna",
        "temp_min": 66, "temp_max": 80, "temp_ideal_low": 70, "temp_ideal_high": 76,
        "peak_months": [5, 6, 11, 12],
        "good_months": [4, 7, 8, 9, 10, 1],
        "bait": "Live baitfish; trolling spreader bars; chunk bait; deep-drop jigs",
        "rig": "Heavy trolling rig or deep-drop rig with fluorocarbon leader",
        "hook_size": "7/0-10/0 circle hook",
        "sinker": "Deep-drop weight or trolling weight",
        "explanation_cold": "Bigeye tuna are along the Gulf Stream and shelf edge year-round; winter deep-dropping produces trophy fish.",
        "explanation_warm": "Bigeye tuna inhabit deep water along the Gulf Stream edge; they are caught trolling and deep-dropping and are among the best sashimi fish.",
    },
    {
        "name": "Albacore tuna",
        "temp_min": 58, "temp_max": 72, "temp_ideal_low": 62, "temp_ideal_high": 68,
        "peak_months": [10, 11, 12],
        "good_months": [1, 4, 5, 9],
        "bait": "Trolling feathers; cedar plugs; live baitfish; chunk bait",
        "rig": "Trolling rig with fluorocarbon leader",
        "hook_size": "3/0-6/0 circle or J-hook",
        "sinker": "Trolling weight or none",
        "explanation_cold": "Albacore tuna pass through NC offshore waters during fall and winter, preferring cooler water than other tunas.",
        "explanation_warm": "Albacore prefer cooler water and are most accessible off NC during fall and early winter along temperature breaks.",
    },
    {
        "name": "Frigate mackerel",
        "temp_min": 68, "temp_max": 84, "temp_ideal_low": 74, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small jigs; sabiki rigs; tiny feathers; live small baitfish",
        "rig": "Sabiki rig or light trolling rig",
        "hook_size": "#2-2/0 J-hook or jig hook",
        "sinker": "1-2 oz or trolling weight",
        "explanation_cold": "Frigate mackerel are offshore in warm water and not available nearshore during cold months.",
        "explanation_warm": "Frigate mackerel are small tunas that school nearshore and around piers; they are useful as cut or live bait for larger gamefish.",
    },
    # --- Reef & structure species ---
    {
        "name": "Sergeant major (damselfish)",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Tiny shrimp pieces; bread; small cut bait",
        "rig": "Knocker rig with very small hooks",
        "hook_size": "#8-#4 small hook",
        "sinker": "Small split shot",
        "explanation_cold": "Sergeant majors move offshore or south during cold months.",
        "explanation_warm": "Sergeant majors are bold, black-and-yellow striped damselfish that school around pier pilings; males aggressively guard purple egg patches on structure.",
    },
    {
        "name": "Rock hind",
        "temp_min": 62, "temp_max": 82, "temp_ideal_low": 70, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live baitfish; cut squid; cut fish; live shrimp",
        "rig": "Fish finder rig with fluorocarbon leader",
        "hook_size": "2/0-5/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Rock hind remain on deeper structure during cold months.",
        "explanation_warm": "Rock hind are small, spotted groupers found on nearshore reefs and hard bottom; they are ambush predators that hit live and cut baits.",
    },
    {
        "name": "Red hind",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Live baitfish; cut squid; cut fish strips; live shrimp",
        "rig": "Fish finder rig with fluorocarbon leader",
        "hook_size": "2/0-5/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Red hind are on deeper offshore reefs during cold months.",
        "explanation_warm": "Red hind are beautifully red-spotted small groupers found on nearshore and offshore reefs; they are aggressive biters and good eating.",
    },
    {
        "name": "Yellowedge grouper",
        "temp_min": 50, "temp_max": 72, "temp_ideal_low": 55, "temp_ideal_high": 65,
        "peak_months": [4, 5, 6, 9, 10, 11],
        "good_months": [3, 7, 8, 12],
        "bait": "Cut squid; cut fish; live baitfish",
        "rig": "Deep-drop bottom rig with heavy weight",
        "hook_size": "6/0-9/0 circle hook",
        "sinker": "2-5 lb bank sinker",
        "explanation_cold": "Yellowedge grouper remain on deep structure year-round; they are accessible via deep-drop rigs.",
        "explanation_warm": "Yellowedge grouper inhabit deep ledges and hard bottom in 300-600+ ft; they are premier deep-drop targets and outstanding table fare.",
    },
    {
        "name": "Coney",
        "temp_min": 70, "temp_max": 84, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Small live baitfish; cut squid; shrimp",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "#1-3/0 circle hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Coney are a tropical species not commonly found on NC reefs during cold months.",
        "explanation_warm": "Coney are small, colorful groupers that occasionally appear on NC nearshore reefs during peak summer; they come in multiple color phases.",
    },
    {
        "name": "Sand tilefish",
        "temp_min": 60, "temp_max": 80, "temp_ideal_low": 68, "temp_ideal_high": 76,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut squid; small cut fish; shrimp pieces",
        "rig": "Hi-lo rig",
        "hook_size": "#1-3/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Sand tilefish are on moderate-depth sandy bottom during cold months.",
        "explanation_warm": "Sand tilefish build burrows in sandy bottom near reefs in 100-300 ft; they are colorful fish that bite cut squid and shrimp on bottom rigs.",
    },
    {
        "name": "Spotted scorpionfish",
        "temp_min": 60, "temp_max": 82, "temp_ideal_low": 68, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; cut fish; squid; live small baitfish",
        "rig": "Fish finder rig or double-dropper bottom rig",
        "hook_size": "#1-3/0 circle hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Spotted scorpionfish are on deeper structure during cold months.",
        "explanation_warm": "Spotted scorpionfish are well-camouflaged ambush predators on reefs and hard bottom; handle with extreme care as dorsal spines are venomous.",
    },
    {
        "name": "Goosefish (monkfish)",
        "temp_min": 38, "temp_max": 62, "temp_ideal_low": 42, "temp_ideal_high": 55,
        "peak_months": [12, 1, 2, 3],
        "good_months": [4, 11],
        "bait": "Cut fish; whole small fish; squid; any large bait",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "4-8 oz pyramid sinker",
        "explanation_cold": "Goosefish are large, flat ambush predators that move inshore during winter; they use a lure-like appendage on their head to attract prey.",
        "explanation_warm": "Goosefish move to deeper, cooler water during warm months and are not available inshore.",
    },
    # --- Moray eels ---
    {
        "name": "Spotted moray eel",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut fish; squid; shrimp (usually incidental catch)",
        "rig": "Any bottom rig near structure (incidental catch)",
        "hook_size": "2/0-5/0 hook",
        "sinker": "2-4 oz",
        "explanation_cold": "Spotted morays retreat deeper into structure and are inactive during cold months.",
        "explanation_warm": "Spotted moray eels hide in pier pilings and rocky structure; they are caught incidentally on cut bait — handle with extreme caution as they bite aggressively.",
    },
    {
        "name": "Green moray eel",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut fish; squid; octopus; shrimp (usually incidental)",
        "rig": "Any bottom rig near structure (incidental catch)",
        "hook_size": "3/0-6/0 hook",
        "sinker": "2-4 oz",
        "explanation_cold": "Green morays are deep in structure crevices and inactive during cold months.",
        "explanation_warm": "Green moray eels are the largest morays in NC waters; they inhabit pier pilings, jetties and wrecks. Do not attempt to handle — they deliver severe bites.",
    },
    # --- Tropical visitors & other gamefish ---
    {
        "name": "Snook",
        "temp_min": 72, "temp_max": 90, "temp_ideal_low": 78, "temp_ideal_high": 86,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Live mullet; live shrimp; live menhaden; topwater plugs",
        "rig": "Fish finder rig with heavy fluorocarbon leader or free-lined",
        "hook_size": "2/0-5/0 circle hook",
        "sinker": "1-2 oz egg sinker or none",
        "explanation_cold": "Snook are a tropical species not found in NC during cold months.",
        "explanation_warm": "Snook are rare visitors to NC during unusually warm summers; they are prized gamefish that ambush bait around inlets, docks and structure.",
    },
    {
        "name": "Southern sennet",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small shiny jigs; small live baitfish; cut fish strips",
        "rig": "Float rig with light leader",
        "hook_size": "#1-2/0 J-hook",
        "sinker": "None or small split shot",
        "explanation_cold": "Southern sennet migrate south during cold months.",
        "explanation_warm": "Southern sennet are small, schooling barracuda relatives that swarm around pier pilings and nearshore structure; they hit small, flashy lures.",
    },
    {
        "name": "Greater soapfish",
        "temp_min": 65, "temp_max": 82, "temp_ideal_low": 72, "temp_ideal_high": 78,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut shrimp; small cut fish; squid bits",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "#1-3/0 circle hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Greater soapfish are on deeper reefs during cold months.",
        "explanation_warm": "Greater soapfish are small, grouper-like fish found on reefs and hard bottom; they produce a soapy skin mucus when handled — do not eat them.",
    },
    {
        "name": "Yellowmouth grouper",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live baitfish; cut squid; cut fish; live shrimp",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "3/0-6/0 circle hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Yellowmouth grouper remain on deeper offshore structure during cold months.",
        "explanation_warm": "Yellowmouth grouper are found on nearshore and offshore reefs; they have distinctive yellow coloring inside the mouth and hit live and cut baits.",
    },
    {
        "name": "Speckled hind",
        "temp_min": 55, "temp_max": 75, "temp_ideal_low": 60, "temp_ideal_high": 70,
        "peak_months": [4, 5, 6, 9, 10, 11],
        "good_months": [3, 7, 8, 12],
        "bait": "Cut squid; cut fish; live baitfish",
        "rig": "Deep-drop bottom rig with heavy weight",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "2-4 lb bank sinker",
        "explanation_cold": "Speckled hind are on deep structure year-round; they are a rare, protected species.",
        "explanation_warm": "Speckled hind are rare, beautifully spotted groupers found on deep reefs in 200-500+ ft; they are protected and must be released in federal waters.",
    },
    {
        "name": "Warsaw grouper",
        "temp_min": 50, "temp_max": 72, "temp_ideal_low": 55, "temp_ideal_high": 65,
        "peak_months": [4, 5, 10, 11],
        "good_months": [3, 6, 9, 12],
        "bait": "Large cut squid; whole fish; large live baitfish",
        "rig": "Deep-drop bottom rig with very heavy weight and electric reel",
        "hook_size": "8/0-12/0 circle hook",
        "sinker": "3-8 lb bank sinker",
        "explanation_cold": "Warsaw grouper inhabit deep ledges year-round and are accessible via deep-drop rigs.",
        "explanation_warm": "Warsaw grouper are the largest grouper caught off NC, living on deep ledges in 300-800+ ft; they can exceed 300 lbs and require the heaviest tackle.",
    },
    {
        "name": "Goliath grouper (jewfish)",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "PROTECTED SPECIES — catch and release only; large live baitfish if encountered incidentally",
        "rig": "Heavy tackle (usually incidental catch)",
        "hook_size": "8/0-14/0 circle hook",
        "sinker": "Heavy",
        "explanation_cold": "Goliath grouper are in warmer waters to the south during cold months.",
        "explanation_warm": "Goliath grouper are the largest Atlantic grouper, occasionally visiting NC wrecks and piers during summer. PROTECTED — must be immediately released.",
    },
    # --- Other bottom species ---
    {
        "name": "Black drum (large bull)",
        "temp_min": 55, "temp_max": 85, "temp_ideal_low": 62, "temp_ideal_high": 78,
        "peak_months": [2, 3, 4],
        "good_months": [1, 5, 11, 12],
        "bait": "Whole blue crabs; half crabs; large clam; cut mullet",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "4-6 oz pyramid sinker",
        "explanation_cold": "Large bull black drum stage near inlets during late winter before the spring spawning run.",
        "explanation_warm": "Bull black drum (40+ lbs) move through inlets and along structure during the spring spawn; whole blue crabs on heavy tackle are the standard bait.",
    },
    {
        "name": "Northern stargazer",
        "temp_min": 50, "temp_max": 78, "temp_ideal_low": 58, "temp_ideal_high": 70,
        "peak_months": [4, 5, 6, 9, 10],
        "good_months": [3, 7, 8, 11],
        "bait": "Usually caught incidentally on cut bait or live bait fished on the bottom",
        "rig": "Fish finder rig or double-dropper bottom rig",
        "hook_size": "1/0-4/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Northern stargazers bury in sandy bottom year-round but are less commonly caught in the coldest months.",
        "explanation_warm": "Northern stargazers bury in the sand with only their eyes exposed, ambushing prey from below; they can deliver an electric shock — handle with caution.",
    },
    {
        "name": "Atlantic sturgeon",
        "temp_min": 50, "temp_max": 78, "temp_ideal_low": 58, "temp_ideal_high": 70,
        "peak_months": [3, 4, 5, 9, 10, 11],
        "good_months": [2, 6],
        "bait": "PROTECTED SPECIES — no targeting allowed; occasionally hooked incidentally on cut bait",
        "rig": "N/A — protected species, must release immediately if hooked",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Atlantic sturgeon are in deeper river channels and nearshore waters during winter; they are federally protected.",
        "explanation_warm": "Atlantic sturgeon are ancient, armored fish that migrate through NC rivers and nearshore waters. ENDANGERED — must be immediately released if hooked.",
    },
    {
        "name": "Shortnose sturgeon",
        "temp_min": 46, "temp_max": 72, "temp_ideal_low": 52, "temp_ideal_high": 65,
        "peak_months": [2, 3, 4],
        "good_months": [1, 5, 11, 12],
        "bait": "PROTECTED SPECIES — no targeting allowed",
        "rig": "N/A — protected species, must release immediately if hooked",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Shortnose sturgeon inhabit deeper river pools during winter; they are federally protected.",
        "explanation_warm": "Shortnose sturgeon are smaller than Atlantic sturgeon and found in NC rivers. ENDANGERED — must be immediately released if hooked.",
    },
    # --- Tropical strays ---
    {
        "name": "Blue tang (surgeonfish)",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [8, 9],
        "good_months": [7, 10],
        "bait": "Rarely caught on hook and line; occasionally caught in cast nets",
        "rig": "N/A — not a typical hook-and-line target",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Blue tang are a tropical reef species not found in NC during cold months.",
        "explanation_warm": "Blue tang occasionally appear around NC pier pilings and jetties during late summer as tropical strays; they are herbivores with a sharp tail spine.",
    },
    {
        "name": "Gray angelfish",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Small shrimp pieces; sponge; tiny cut baits (rarely targeted)",
        "rig": "Knocker rig with very small hooks",
        "hook_size": "#6-#2 small hook",
        "sinker": "Small split shot",
        "explanation_cold": "Gray angelfish are in tropical waters during cold months.",
        "explanation_warm": "Gray angelfish are large, disc-shaped reef fish that occasionally visit NC pier pilings and jetties as tropical strays during warm months.",
    },
    {
        "name": "Spotfin butterflyfish",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [8, 9],
        "good_months": [7, 10],
        "bait": "Rarely caught; occasionally seen around pier pilings",
        "rig": "N/A — not a hook-and-line target",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Spotfin butterflyfish are tropical and not present in NC during cold months.",
        "explanation_warm": "Spotfin butterflyfish are beautiful tropical strays that appear around NC pier pilings in late summer; they feed on small invertebrates on structure.",
    },
    {
        "name": "Doctorfish (tang)",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [8, 9],
        "good_months": [7, 10],
        "bait": "Rarely caught on hook and line",
        "rig": "N/A — not a typical hook-and-line target",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Doctorfish are tropical and not present in NC during cold months.",
        "explanation_warm": "Doctorfish are tropical tangs that occasionally appear around NC structure as summer strays; they are herbivores with a scalpel-like tail spine.",
    },
    {
        "name": "Squirrelfish",
        "temp_min": 70, "temp_max": 84, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Small shrimp pieces; tiny squid bits; cut fish",
        "rig": "Knocker rig with small hooks",
        "hook_size": "#4-#1 small hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Squirrelfish are in deeper or tropical waters during cold months.",
        "explanation_warm": "Squirrelfish are red, big-eyed nocturnal reef fish found around pier pilings and structure; they are most active at night and caught on small cut baits.",
    },
    # --- Invasive & ecological awareness ---
    {
        "name": "Red lionfish (invasive)",
        "temp_min": 55, "temp_max": 82, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9, 10],
        "good_months": [4, 11],
        "bait": "Cut shrimp; squid; cut fish (usually caught incidentally or speared)",
        "rig": "Hi-lo rig (usually caught while reef fishing)",
        "hook_size": "#1-3/0 hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Lionfish remain on deeper reefs during cold months but survive NC winters on offshore structure.",
        "explanation_warm": "Red lionfish are an invasive Indo-Pacific species now established on NC reefs; they have venomous spines but are excellent eating once spines are removed.",
    },
    # --- Wrasses & parrotfish ---
    {
        "name": "Puddingwife (wrasse)",
        "temp_min": 68, "temp_max": 84, "temp_ideal_low": 74, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small shrimp; sand fleas; small crabs; tiny cut baits",
        "rig": "Knocker rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Puddingwife wrasse are on deeper reefs or in warmer waters during cold months.",
        "explanation_warm": "Puddingwife are colorful, medium-sized wrasses found around NC pier pilings and reefs; they feed on small crustaceans and are strong for their size.",
    },
    {
        "name": "Bluehead wrasse",
        "temp_min": 70, "temp_max": 84, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Tiny shrimp; small cut baits (rarely targeted)",
        "rig": "Knocker rig with very small hooks",
        "hook_size": "#8-#4 small hook",
        "sinker": "Small split shot",
        "explanation_cold": "Bluehead wrasse are tropical and not present in NC during cold months.",
        "explanation_warm": "Bluehead wrasse are small, colorful tropical wrasses that occasionally appear around NC pier pilings and reefs as summer strays.",
    },
    # --- Protected species (awareness) ---
    {
        "name": "Kemp's ridley sea turtle",
        "temp_min": 60, "temp_max": 85, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9, 10],
        "good_months": [4, 11],
        "bait": "PROTECTED SPECIES — do not target; may be hooked incidentally while fishing",
        "rig": "N/A — if hooked, cut line as close to hook as safely possible",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Kemp's ridley turtles migrate south during winter months.",
        "explanation_warm": "Kemp's ridley sea turtles are the most commonly hooked turtle from NC piers. ENDANGERED — if hooked, do NOT pull on line. Call pier staff or wildlife officials.",
    },
    # --- Additional drums & croakers ---
    {
        "name": "Southern kingfish (ground mullet)",
        "temp_min": 55, "temp_max": 85, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [4, 5, 6, 9, 10],
        "good_months": [3, 7, 8, 11],
        "bait": "Fresh shrimp; sand fleas; bloodworms; Fishbites",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "1-3 oz pyramid sinker",
        "explanation_cold": "Southern kingfish are in deeper water during the coldest months but move inshore earlier than northern kingfish.",
        "explanation_warm": "Southern kingfish (ground mullet) are a whiting species common in the surf; they are slightly larger than northern kingfish and prefer sandy bottom.",
    },
    {
        "name": "Gulf kingfish (gulf whiting)",
        "temp_min": 60, "temp_max": 86, "temp_ideal_low": 68, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Sand fleas; fresh shrimp; Fishbites; bloodworms",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "1-3 oz pyramid sinker",
        "explanation_cold": "Gulf kingfish are in deeper water during cold months.",
        "explanation_warm": "Gulf kingfish are the largest whiting species, occasionally exceeding 3 lbs; they prefer high-energy surf zones and hit sand fleas and shrimp.",
    },
    {
        "name": "Silver seatrout",
        "temp_min": 55, "temp_max": 84, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live shrimp; small cut fish; squid strips; cut shrimp",
        "rig": "Fish finder rig with light fluorocarbon leader",
        "hook_size": "1/0-3/0 circle hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Silver seatrout are in deeper channels during cold months.",
        "explanation_warm": "Silver seatrout are similar to sand seatrout but with a more silvery sheen; they school around piers and in the surf and hit live shrimp readily.",
    },
    # --- Sea robins ---
    {
        "name": "Striped sea robin",
        "temp_min": 42, "temp_max": 72, "temp_ideal_low": 48, "temp_ideal_high": 62,
        "peak_months": [11, 12, 1, 2, 3],
        "good_months": [4, 10],
        "bait": "Squid strips; cut shrimp; bloodworms; cut fish",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Striped sea robins are common winter bottom catches; they have large, wing-like pectoral fins and make grunting sounds when caught.",
        "explanation_warm": "Striped sea robins move offshore as water warms; they are most common inshore during fall through spring.",
    },
    {
        "name": "Northern sea robin",
        "temp_min": 40, "temp_max": 70, "temp_ideal_low": 46, "temp_ideal_high": 60,
        "peak_months": [11, 12, 1, 2, 3],
        "good_months": [4, 10],
        "bait": "Squid strips; cut shrimp; bloodworms; cut fish",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Northern sea robins are common winter bottom fish; they use finger-like pectoral fin rays to walk along the bottom and locate prey.",
        "explanation_warm": "Northern sea robins move to deeper, cooler water during warm months.",
    },
    # --- Herring & shad ---
    {
        "name": "Atlantic herring",
        "temp_min": 38, "temp_max": 60, "temp_ideal_low": 42, "temp_ideal_high": 54,
        "peak_months": [12, 1, 2, 3],
        "good_months": [4, 11],
        "bait": "Sabiki rigs; tiny jigs; small gold hooks",
        "rig": "Sabiki rig with small weight",
        "hook_size": "#10-#6 sabiki hook",
        "sinker": "1-2 oz weight below sabiki",
        "explanation_cold": "Atlantic herring school inshore during winter, providing bait for striped bass and bluefish; catch them on sabiki rigs for cut bait.",
        "explanation_warm": "Atlantic herring move to deeper, cooler waters during warm months.",
    },
    {
        "name": "Blueback herring",
        "temp_min": 45, "temp_max": 68, "temp_ideal_low": 52, "temp_ideal_high": 62,
        "peak_months": [3, 4, 5],
        "good_months": [2, 6],
        "bait": "Small shad darts; tiny spoons; sabiki rigs",
        "rig": "Tandem shad dart rig or sabiki rig",
        "hook_size": "#6-#2 shad dart or jig",
        "sinker": "1/4-1/2 oz jig head",
        "explanation_cold": "Blueback herring are offshore staging before their spring river runs.",
        "explanation_warm": "Blueback herring run up NC rivers alongside shad in spring; they hit small, bright darts and jigs and are excellent cut bait.",
    },
    {
        "name": "Alewife",
        "temp_min": 42, "temp_max": 65, "temp_ideal_low": 50, "temp_ideal_high": 60,
        "peak_months": [3, 4],
        "good_months": [2, 5],
        "bait": "Small shad darts; tiny spoons; sabiki rigs",
        "rig": "Tandem shad dart rig or sabiki rig",
        "hook_size": "#6-#2 shad dart or jig",
        "sinker": "1/4-1/2 oz jig head",
        "explanation_cold": "Alewife are offshore before their spring spawning run into rivers.",
        "explanation_warm": "Alewife are anadromous herring that run up NC rivers in spring for spawning; they hit small darts and make excellent cut and live bait.",
    },
    # --- Skates ---
    {
        "name": "Winter skate",
        "temp_min": 36, "temp_max": 60, "temp_ideal_low": 40, "temp_ideal_high": 54,
        "peak_months": [12, 1, 2, 3],
        "good_months": [4, 11],
        "bait": "Cut shrimp; cut squid; clam; cut fish",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "1/0-4/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Winter skates move inshore during the coldest months; they are larger than clearnose skates and common bottom catches from piers.",
        "explanation_warm": "Winter skates move to deeper, cooler water during warm months and are absent inshore.",
    },
    {
        "name": "Little skate",
        "temp_min": 36, "temp_max": 60, "temp_ideal_low": 40, "temp_ideal_high": 52,
        "peak_months": [12, 1, 2, 3],
        "good_months": [4, 11],
        "bait": "Cut shrimp; squid; clam; bloodworms",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Little skates are small, common bottom catches during winter months; they are the smallest skate species in NC waters.",
        "explanation_warm": "Little skates move to deeper, cooler water during warm months.",
    },
    # --- Snappers (additional) ---
    {
        "name": "Cubera snapper",
        "temp_min": 72, "temp_max": 88, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Large live crabs; live lobster; large live baitfish",
        "rig": "Fish finder rig with very heavy leader",
        "hook_size": "6/0-10/0 circle hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Cubera snapper are in tropical waters and not present in NC during cold months.",
        "explanation_warm": "Cubera snapper are the largest snapper species, rarely encountered on NC reefs during peak summer; they are immensely powerful around structure.",
    },
    {
        "name": "Schoolmaster snapper",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "Live shrimp; small live baitfish; cut shrimp; small jigs",
        "rig": "Knocker rig with fluorocarbon leader",
        "hook_size": "1/0-3/0 circle hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Schoolmaster snapper are tropical and not found in NC during cold months.",
        "explanation_warm": "Schoolmaster snapper are occasional tropical visitors to NC structure during warm summers; they have distinctive yellow fins and are wary biters.",
    },
    {
        "name": "Silk snapper",
        "temp_min": 55, "temp_max": 78, "temp_ideal_low": 60, "temp_ideal_high": 72,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10, 11],
        "bait": "Cut squid; cut fish strips; small live baitfish",
        "rig": "Deep-drop bottom rig with multiple hooks",
        "hook_size": "2/0-5/0 circle hook",
        "sinker": "8-16 oz bank sinker",
        "explanation_cold": "Silk snapper are on deep reefs year-round and accessible via deep-drop rigs.",
        "explanation_warm": "Silk snapper are deep-water snappers found on reefs in 300-600+ ft off NC; they are beautiful pink fish caught on deep-drop rigs.",
    },
    {
        "name": "Queen snapper",
        "temp_min": 48, "temp_max": 68, "temp_ideal_low": 52, "temp_ideal_high": 62,
        "peak_months": [4, 5, 6, 9, 10, 11],
        "good_months": [3, 7, 8, 12],
        "bait": "Cut squid; cut fish; electric reel with deep-drop rigs",
        "rig": "Deep-drop bottom rig with electric reel",
        "hook_size": "3/0-6/0 circle hook",
        "sinker": "2-5 lb bank sinker",
        "explanation_cold": "Queen snapper inhabit very deep water (600-1500+ ft) year-round off NC.",
        "explanation_warm": "Queen snapper are brilliant red deep-water fish caught in 600-1500+ ft; they are among the most prized deep-drop catches off NC.",
    },
    # --- Miscellaneous reef species ---
    {
        "name": "Gray snapper (juvenile)",
        "temp_min": 62, "temp_max": 86, "temp_ideal_low": 70, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small shrimp pieces; tiny cut fish; small live shrimp",
        "rig": "Knocker rig with light fluorocarbon leader",
        "hook_size": "#4-#1 circle hook",
        "sinker": "Small split shot or 1/2 oz egg",
        "explanation_cold": "Juvenile gray snapper move to deeper or warmer waters during cold months.",
        "explanation_warm": "Juvenile mangrove (gray) snapper swarm pier pilings in summer, stealing bait with precision; use light fluorocarbon and small hooks to catch them.",
    },
    {
        "name": "Yellowtail damselfish",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [8, 9],
        "good_months": [7, 10],
        "bait": "Rarely targeted; occasionally caught on tiny hooks",
        "rig": "Knocker rig with very small hooks",
        "hook_size": "#10-#6 micro hook",
        "sinker": "Tiny split shot",
        "explanation_cold": "Yellowtail damselfish are tropical and not present in NC during cold months.",
        "explanation_warm": "Yellowtail damselfish are small, bright tropical strays that appear around NC pier pilings in late summer; they aggressively defend small territories.",
    },
    {
        "name": "Creole wrasse",
        "temp_min": 68, "temp_max": 82, "temp_ideal_low": 74, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small squid strips; tiny shrimp; small jigs",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Creole wrasse are on deeper offshore reefs during cold months.",
        "explanation_warm": "Creole wrasse are purple reef fish that school above hard bottom and reefs; they are common on NC offshore structure.",
    },
    # --- Toadfish & sculpins ---
    {
        "name": "Leopard toadfish",
        "temp_min": 60, "temp_max": 85, "temp_ideal_low": 70, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; cut fish; squid; crabs",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "#1-3/0 hook",
        "sinker": "1-3 oz sinker",
        "explanation_cold": "Leopard toadfish are less active and deeper in structure during cold months.",
        "explanation_warm": "Leopard toadfish are smaller, spotted relatives of oyster toadfish found around structure; they produce loud grunting calls during spawning season.",
    },
    # --- Additional catfish ---
    {
        "name": "Channel catfish",
        "temp_min": 50, "temp_max": 85, "temp_ideal_low": 70, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; cut fish; chicken liver; stinkbait; nightcrawlers",
        "rig": "Fish finder rig or double-dropper bottom rig",
        "hook_size": "1/0-4/0 circle hook",
        "sinker": "1-3 oz egg sinker",
        "explanation_cold": "Channel catfish are in deeper holes during cold months and feed less actively.",
        "explanation_warm": "Channel catfish are primarily freshwater but enter brackish estuaries and inlets near the coast; they hit cut bait and stinkbaits aggressively.",
    },
    # --- Additional flatfish ---
    {
        "name": "Fringed flounder",
        "temp_min": 55, "temp_max": 80, "temp_ideal_low": 64, "temp_ideal_high": 76,
        "peak_months": [4, 5, 6, 9, 10],
        "good_months": [3, 7, 8, 11],
        "bait": "Live shrimp; small live minnows; cut mullet strips",
        "rig": "Fish finder rig with fluorocarbon leader",
        "hook_size": "#2-2/0 circle or wide-gap hook",
        "sinker": "1-2 oz egg sinker",
        "explanation_cold": "Fringed flounder are in deeper water during cold months.",
        "explanation_warm": "Fringed flounder are small flatfish found in estuaries and around structure; they are occasionally caught from piers while targeting other flounder species.",
    },
    {
        "name": "Blackcheek tonguefish",
        "temp_min": 55, "temp_max": 82, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Bloodworms; tiny shrimp pieces; small worms",
        "rig": "Hi-lo rig with very small hooks",
        "hook_size": "#8-#4 small bait hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "Blackcheek tonguefish are in deeper channels during cold months.",
        "explanation_warm": "Blackcheek tonguefish are tiny, tongue-shaped flatfish occasionally caught on small baits; they are too small to keep but are common in estuaries.",
    },
    # --- Freshwater/brackish crossovers ---
    {
        "name": "Largemouth bass",
        "temp_min": 50, "temp_max": 85, "temp_ideal_low": 65, "temp_ideal_high": 80,
        "peak_months": [4, 5, 6, 9, 10],
        "good_months": [3, 7, 8, 11],
        "bait": "Live shrimp; live minnows; plastic worms; topwater plugs",
        "rig": "Fish finder rig or Texas rig with worm hook",
        "hook_size": "1/0-4/0 wide-gap hook",
        "sinker": "1/4-1/2 oz bullet weight",
        "explanation_cold": "Largemouth bass are in deeper holes in brackish creeks during cold months.",
        "explanation_warm": "Largemouth bass enter brackish water in the ICW and tidal creeks near Wrightsville and Carolina Beach; they hit live shrimp and soft plastics.",
    },
    {
        "name": "Blue catfish",
        "temp_min": 45, "temp_max": 85, "temp_ideal_low": 70, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut menhaden; cut shad; chicken liver; large shrimp",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "3/0-7/0 circle hook",
        "sinker": "2-4 oz egg sinker",
        "explanation_cold": "Blue catfish are in deeper river holes during cold months but can still be caught.",
        "explanation_warm": "Blue catfish have expanded into tidal rivers near the coast and occasionally enter brackish water; they can grow very large and fight hard.",
    },
    {
        "name": "Striped bass (hybrid)",
        "temp_min": 50, "temp_max": 80, "temp_ideal_low": 60, "temp_ideal_high": 72,
        "peak_months": [3, 4, 5, 10, 11],
        "good_months": [2, 6, 9, 12],
        "bait": "Live shrimp; live minnows; cut menhaden; jigs",
        "rig": "Fish finder rig or free-lined live bait",
        "hook_size": "2/0-5/0 circle hook",
        "sinker": "1-3 oz egg sinker",
        "explanation_cold": "Hybrid striped bass are in deeper river holes and channels during cold months.",
        "explanation_warm": "Hybrid striped bass (wipers) are stocked in Cape Fear River tributaries and enter brackish water; they fight harder than pure stripers pound-for-pound.",
    },
    # --- Offshore bottom species ---
    {
        "name": "Tilefish (golden juvenile)",
        "temp_min": 50, "temp_max": 70, "temp_ideal_low": 54, "temp_ideal_high": 64,
        "peak_months": [4, 5, 10, 11],
        "good_months": [3, 6, 9, 12],
        "bait": "Cut squid; clam strips; cut fish",
        "rig": "Deep-drop bottom rig",
        "hook_size": "3/0-6/0 circle hook",
        "sinker": "1-3 lb bank sinker",
        "explanation_cold": "Juvenile golden tilefish are on moderate-depth structure and accessible year-round.",
        "explanation_warm": "Juvenile golden tilefish are found shallower than adults, on hard bottom and ledges in 200-400 ft; they hit cut squid and clam.",
    },
    {
        "name": "Barrelfish",
        "temp_min": 46, "temp_max": 64, "temp_ideal_low": 50, "temp_ideal_high": 58,
        "peak_months": [11, 12, 1, 2, 3],
        "good_months": [4, 10],
        "bait": "Cut squid; cut fish; whole small baitfish",
        "rig": "Deep-drop bottom rig with electric reel",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "3-8 lb bank sinker",
        "explanation_cold": "Barrelfish inhabit deep structure in 600-1200+ ft off NC; they are most commonly targeted during winter deep-drop trips.",
        "explanation_warm": "Barrelfish are deep-water species found well below the thermocline; they are caught on deep-drop rigs and have firm, white flesh.",
    },
    {
        "name": "Blackbelly rosefish",
        "temp_min": 45, "temp_max": 62, "temp_ideal_low": 48, "temp_ideal_high": 56,
        "peak_months": [10, 11, 12, 1, 2, 3],
        "good_months": [4, 9],
        "bait": "Cut squid; small cut fish; shrimp",
        "rig": "Deep-drop bottom rig",
        "hook_size": "2/0-5/0 circle hook",
        "sinker": "2-5 lb bank sinker",
        "explanation_cold": "Blackbelly rosefish are deep-water scorpionfish found in 500-1500 ft off NC; they are bright red with large eyes.",
        "explanation_warm": "Blackbelly rosefish remain in deep, cold water year-round; they are occasional catches on deep-drop trips targeting tilefish and snowy grouper.",
    },
    # --- Additional species for completeness ---
    {
        "name": "White perch",
        "temp_min": 45, "temp_max": 78, "temp_ideal_low": 55, "temp_ideal_high": 70,
        "peak_months": [3, 4, 5, 10, 11],
        "good_months": [2, 6, 9, 12],
        "bait": "Bloodworms; small shrimp; small minnows; tiny jigs",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#2 circle or bait hook",
        "sinker": "1-2 oz pyramid sinker",
        "explanation_cold": "White perch are in deeper brackish channels during cold months but can be caught on bloodworms.",
        "explanation_warm": "White perch are found in brackish tidal creeks and the ICW near the coast; they school in good numbers and bite small baits readily.",
    },
    {
        "name": "Yellow perch",
        "temp_min": 42, "temp_max": 72, "temp_ideal_low": 55, "temp_ideal_high": 68,
        "peak_months": [3, 4, 5, 10, 11],
        "good_months": [2, 6, 9, 12],
        "bait": "Bloodworms; small minnows; tiny jigs; crickets",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#2 bait hook",
        "sinker": "1/2-1 oz split shot",
        "explanation_cold": "Yellow perch are in deeper pools during cold months; they can be caught through slow presentations.",
        "explanation_warm": "Yellow perch occasionally enter brackish creeks near the coast; they are popular panfish that school and bite small live baits.",
    },
    {
        "name": "Spotted hake",
        "temp_min": 40, "temp_max": 65, "temp_ideal_low": 46, "temp_ideal_high": 58,
        "peak_months": [11, 12, 1, 2, 3],
        "good_months": [4, 10],
        "bait": "Cut squid; shrimp; cut fish; bloodworms",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Spotted hake are common winter bottom catches from piers; they have a slender body with a long chin barbel and are decent eating.",
        "explanation_warm": "Spotted hake move to deeper, cooler water during warm months and are uncommon inshore.",
    },
    {
        "name": "Red hake (ling)",
        "temp_min": 38, "temp_max": 62, "temp_ideal_low": 44, "temp_ideal_high": 56,
        "peak_months": [12, 1, 2, 3],
        "good_months": [4, 11],
        "bait": "Cut squid; shrimp; cut fish; clam",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Red hake move inshore during the coldest months; they are elongated bottom fish with a chin barbel and are good eating when fresh.",
        "explanation_warm": "Red hake move to deeper, cooler water during warm months.",
    },
    {
        "name": "Longhorn sculpin",
        "temp_min": 36, "temp_max": 58, "temp_ideal_low": 40, "temp_ideal_high": 52,
        "peak_months": [12, 1, 2, 3],
        "good_months": [4, 11],
        "bait": "Cut squid; shrimp; bloodworms; cut fish",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Longhorn sculpin are spiny bottom fish that move inshore during the coldest months; they have prominent head spines and are common winter bycatch.",
        "explanation_warm": "Longhorn sculpin move to deeper, cooler water during warm months and are not found inshore.",
    },
    {
        "name": "Pollock",
        "temp_min": 36, "temp_max": 55, "temp_ideal_low": 40, "temp_ideal_high": 50,
        "peak_months": [12, 1, 2],
        "good_months": [3, 11],
        "bait": "Cut squid; clam; shrimp; jigs; cut fish",
        "rig": "Hi-lo rig or jig",
        "hook_size": "2/0-5/0 circle or jig hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Pollock are rare visitors to NC waters during exceptionally cold winters; they are more common further north but occasionally appear.",
        "explanation_warm": "Pollock are a cold-water species not present in NC during warm months.",
    },
    {
        "name": "Atlantic cod",
        "temp_min": 34, "temp_max": 52, "temp_ideal_low": 38, "temp_ideal_high": 48,
        "peak_months": [1, 2],
        "good_months": [12, 3],
        "bait": "Cut squid; clam; cut fish; jigs",
        "rig": "Hi-lo rig or jig",
        "hook_size": "3/0-6/0 circle or jig hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Atlantic cod are extremely rare visitors to NC waters during the coldest winters; they are at the far southern edge of their range.",
        "explanation_warm": "Atlantic cod are a cold-water species not found in NC waters during warm months.",
    },
    {
        "name": "Bigeye (Priacanthus arenatus)",
        "temp_min": 65, "temp_max": 82, "temp_ideal_low": 72, "temp_ideal_high": 78,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small squid strips; shrimp; small cut fish",
        "rig": "Hi-lo rig",
        "hook_size": "#2-2/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Bigeye are on deeper offshore reefs during cold months.",
        "explanation_warm": "Bigeye are bright red, large-eyed reef fish found on nearshore and offshore structure; they are nocturnal and caught mostly at night on cut bait.",
    },
    {
        "name": "Short bigeye",
        "temp_min": 65, "temp_max": 82, "temp_ideal_low": 72, "temp_ideal_high": 78,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Small squid; shrimp; tiny cut fish",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Short bigeye are on deeper reefs during cold months.",
        "explanation_warm": "Short bigeye are small, red-orange reef fish found on offshore structure; they are nocturnal and caught on deep reef trips.",
    },
    {
        "name": "Ocean pout",
        "temp_min": 34, "temp_max": 52, "temp_ideal_low": 38, "temp_ideal_high": 48,
        "peak_months": [1, 2],
        "good_months": [12, 3],
        "bait": "Cut clam; bloodworms; shrimp; squid",
        "rig": "Hi-lo rig",
        "hook_size": "#1-3/0 circle hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Ocean pout are eel-like bottom fish at the far southern edge of their range in NC; extremely rare but occasionally caught in the coldest winters.",
        "explanation_warm": "Ocean pout are a cold-water species not found in NC during warm months.",
    },
    {
        "name": "Amberjack (juvenile, banded)",
        "temp_min": 65, "temp_max": 85, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Small live baitfish; small jigs; cut fish strips",
        "rig": "Float rig or free-lined with light tackle",
        "hook_size": "#1-2/0 circle hook",
        "sinker": "None or small split shot",
        "explanation_cold": "Juvenile amberjack move offshore during cold months.",
        "explanation_warm": "Juvenile amberjack with distinctive dark bands school under floating debris, sargassum mats and around pier ends; they are fun on light tackle.",
    },
    # --- Sargassum-associated species ---
    {
        "name": "Sargassumfish",
        "temp_min": 70, "temp_max": 86, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Tiny pieces of shrimp; micro jigs (rarely targeted)",
        "rig": "Ultra-light tackle with tiny hooks",
        "hook_size": "#10-#6 micro hook",
        "sinker": "None",
        "explanation_cold": "Sargassumfish are offshore in floating weed mats during cold months.",
        "explanation_warm": "Sargassumfish are bizarre, frogfish-like creatures that live in floating sargassum weed; they occasionally drift near piers with weed mats.",
    },
    # --- Additional warm-water visitors ---
    {
        "name": "Almaco jack (large adult)",
        "temp_min": 68, "temp_max": 84, "temp_ideal_low": 74, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live baitfish; large jigs; cut fish strips; live blue runners",
        "rig": "Fish finder rig with heavy leader or vertical jig",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Large almaco jacks are on deeper offshore structure during cold months.",
        "explanation_warm": "Large adult almaco jacks are found on offshore wrecks and ledges; they are among the hardest-fighting jacks and excellent table fare.",
    },
    {
        "name": "Lesser amberjack",
        "temp_min": 68, "temp_max": 84, "temp_ideal_low": 74, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Small live baitfish; small jigs; cut fish",
        "rig": "Fish finder rig with medium leader",
        "hook_size": "2/0-5/0 circle hook",
        "sinker": "3-6 oz bank sinker",
        "explanation_cold": "Lesser amberjack are on deeper offshore structure during cold months.",
        "explanation_warm": "Lesser amberjack are smaller than greater amberjack, found on offshore reefs and wrecks; they are good fighters often mistaken for juvenile greaters.",
    },
    # --- Additional snappers & groupers ---
    {
        "name": "Misty grouper",
        "temp_min": 48, "temp_max": 68, "temp_ideal_low": 52, "temp_ideal_high": 62,
        "peak_months": [4, 5, 10, 11],
        "good_months": [3, 6, 9, 12],
        "bait": "Large cut squid; whole small fish; large cut bait",
        "rig": "Deep-drop bottom rig with electric reel",
        "hook_size": "7/0-10/0 circle hook",
        "sinker": "3-8 lb bank sinker",
        "explanation_cold": "Misty grouper inhabit very deep structure (600-1500+ ft) year-round off NC.",
        "explanation_warm": "Misty grouper are rare, deep-water groupers found on deep ledges; they are occasionally caught on deep-drop trips targeting wreckfish.",
    },
    {
        "name": "Kitty Mitchell (yellowfin grouper)",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live baitfish; cut squid; cut fish",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "4/0-7/0 circle hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Yellowfin grouper are on deeper offshore reefs during cold months.",
        "explanation_warm": "Yellowfin grouper (kitty mitchells) are found on offshore reefs and wrecks; they are colorful groupers with yellow-trimmed fins.",
    },
    {
        "name": "Nassau grouper",
        "temp_min": 70, "temp_max": 84, "temp_ideal_low": 76, "temp_ideal_high": 82,
        "peak_months": [7, 8, 9],
        "good_months": [6, 10],
        "bait": "PROTECTED SPECIES — must be released if caught",
        "rig": "N/A — protected species",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Nassau grouper are in warmer tropical waters during cold months.",
        "explanation_warm": "Nassau grouper are critically endangered and rarely encountered on NC reefs. PROTECTED — must be immediately released if hooked.",
    },
    # --- Eelpout & wolffish ---
    {
        "name": "Atlantic wolffish",
        "temp_min": 32, "temp_max": 52, "temp_ideal_low": 36, "temp_ideal_high": 46,
        "peak_months": [1, 2],
        "good_months": [12, 3],
        "bait": "Cut clam; sea urchin; crab; large shrimp",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "4-8 oz bank sinker",
        "explanation_cold": "Atlantic wolffish are extremely rare visitors to NC — if one shows up, it's at the very southern extreme of their range during the coldest winters.",
        "explanation_warm": "Atlantic wolffish are a cold-water species never found in NC during warm months.",
    },
    # --- Drums (additional) ---
    {
        "name": "Northern kingfish",
        "temp_min": 50, "temp_max": 78, "temp_ideal_low": 58, "temp_ideal_high": 70,
        "peak_months": [3, 4, 5, 10, 11],
        "good_months": [2, 6, 9, 12],
        "bait": "Bloodworms; fresh shrimp; sand fleas; Fishbites",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#4-#1 circle hook",
        "sinker": "1-3 oz pyramid sinker",
        "explanation_cold": "Northern kingfish (the true 'whiting') are in deeper water during the coldest months but accessible in mild winters.",
        "explanation_warm": "Northern kingfish are the classic NC whiting, common in the surf during spring and fall; they prefer cooler water than their southern and gulf cousins.",
    },
    # --- Seahorses, pipefish & oddities (awareness) ---
    {
        "name": "Lined seahorse",
        "temp_min": 55, "temp_max": 82, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "NOT A TARGET SPECIES — occasionally found in cast nets or around pier pilings",
        "rig": "N/A — do not target; observe and release",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Lined seahorses are in deeper grass beds during cold months.",
        "explanation_warm": "Lined seahorses cling to pier pilings, sargassum and structure; they are occasionally brought up on lines. Handle gently and return to water.",
    },
    {
        "name": "Dusky pipefish",
        "temp_min": 55, "temp_max": 82, "temp_ideal_low": 65, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "NOT A TARGET SPECIES — occasionally caught in cast nets",
        "rig": "N/A — observe and release",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Dusky pipefish are in deeper grass beds during cold months.",
        "explanation_warm": "Dusky pipefish are elongated seahorse relatives found in grass beds and around pier pilings; males carry eggs in a brood pouch.",
    },
    # --- Batfish & flying gurnard ---
    {
        "name": "Shortnose batfish",
        "temp_min": 62, "temp_max": 82, "temp_ideal_low": 70, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; small cut fish (usually incidental)",
        "rig": "Hi-lo rig (incidental catch)",
        "hook_size": "#1-3/0 hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Shortnose batfish are in deeper water during cold months.",
        "explanation_warm": "Shortnose batfish are bizarre, flat bottom fish that 'walk' on modified fins; they are rare incidental catches from piers and the surf.",
    },
    {
        "name": "Flying gurnard",
        "temp_min": 65, "temp_max": 84, "temp_ideal_low": 72, "temp_ideal_high": 80,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut shrimp; small cut fish; squid (usually incidental)",
        "rig": "Hi-lo rig (incidental catch)",
        "hook_size": "#1-3/0 hook",
        "sinker": "2-4 oz pyramid sinker",
        "explanation_cold": "Flying gurnards move to deeper water during cold months.",
        "explanation_warm": "Flying gurnards are stunning bottom fish that spread enormous, wing-like pectoral fins when startled; they are rare, memorable catches from piers.",
    },
    # --- Wrasses (additional) ---
    {
        "name": "Yellowhead wrasse",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [8, 9],
        "good_months": [7, 10],
        "bait": "Tiny shrimp; small cut baits (rarely targeted)",
        "rig": "Knocker rig with very small hooks",
        "hook_size": "#8-#4 small hook",
        "sinker": "Small split shot",
        "explanation_cold": "Yellowhead wrasse are tropical and not present in NC during cold months.",
        "explanation_warm": "Yellowhead wrasse are colorful tropical wrasses that occasionally appear around NC structure as late-summer strays from the Gulf Stream.",
    },
    # --- Scorpionfish & stonefish ---
    {
        "name": "Barbfish (scorpionfish)",
        "temp_min": 60, "temp_max": 82, "temp_ideal_low": 68, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; cut fish; squid (usually incidental)",
        "rig": "Hi-lo rig or Carolina rig",
        "hook_size": "#1-3/0 hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Barbfish are on deeper structure during cold months.",
        "explanation_warm": "Barbfish are small scorpionfish found on hard bottom and reefs; like all scorpionfish, their dorsal spines are venomous — handle with extreme care.",
    },
    {
        "name": "Plumed scorpionfish",
        "temp_min": 62, "temp_max": 82, "temp_ideal_low": 70, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Cut shrimp; squid; small cut fish (usually incidental)",
        "rig": "Hi-lo rig",
        "hook_size": "#1-3/0 hook",
        "sinker": "2-4 oz bank sinker",
        "explanation_cold": "Plumed scorpionfish are on deeper reefs during cold months.",
        "explanation_warm": "Plumed scorpionfish have feathery head appendages for camouflage; they ambush prey on reefs and hard bottom. Venomous dorsal spines — do not handle.",
    },
    # --- Goby, blenny & small reef fish ---
    {
        "name": "Striped blenny",
        "temp_min": 58, "temp_max": 82, "temp_ideal_low": 66, "temp_ideal_high": 78,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Tiny shrimp bits; micro baits (rarely targeted)",
        "rig": "Ultra-light with very small hooks",
        "hook_size": "#10-#6 micro hook",
        "sinker": "Tiny split shot",
        "explanation_cold": "Striped blennies hunker down in structure crevices during cold months.",
        "explanation_warm": "Striped blennies are small, colorful fish found in pier pilings and shells; males have elaborate head crests during breeding season.",
    },
    {
        "name": "Feather blenny",
        "temp_min": 55, "temp_max": 80, "temp_ideal_low": 64, "temp_ideal_high": 76,
        "peak_months": [4, 5, 6, 7, 8, 9],
        "good_months": [3, 10],
        "bait": "Tiny shrimp; micro baits (rarely targeted)",
        "rig": "Ultra-light with very small hooks",
        "hook_size": "#10-#6 micro hook",
        "sinker": "Tiny split shot",
        "explanation_cold": "Feather blennies are in structure crevices during cold months.",
        "explanation_warm": "Feather blennies are abundant on pier pilings and oyster reefs; they have distinctive feathery head cirri and are fun micro-fishing targets.",
    },
    {
        "name": "Naked goby",
        "temp_min": 50, "temp_max": 82, "temp_ideal_low": 62, "temp_ideal_high": 78,
        "peak_months": [4, 5, 6, 7, 8, 9],
        "good_months": [3, 10],
        "bait": "Micro baits (not a hook-and-line target); minnow traps",
        "rig": "N/A — too small for conventional fishing",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Naked gobies shelter in oyster shells during cold months.",
        "explanation_warm": "Naked gobies are tiny, translucent fish living in oyster reefs around piers; they are a key food source for larger fish.",
    },
    # --- Ocean oddities ---
    {
        "name": "Smooth trunkfish",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [8, 9],
        "good_months": [7, 10],
        "bait": "Small shrimp; tiny cut baits (rarely caught)",
        "rig": "Knocker rig with small hooks",
        "hook_size": "#6-#2 small hook",
        "sinker": "Small split shot",
        "explanation_cold": "Smooth trunkfish are tropical and not found in NC during cold months.",
        "explanation_warm": "Smooth trunkfish are cube-shaped tropical strays that occasionally appear around NC pier pilings during peak summer; they secrete a mild toxin.",
    },
    {
        "name": "Honeycomb cowfish",
        "temp_min": 72, "temp_max": 86, "temp_ideal_low": 78, "temp_ideal_high": 84,
        "peak_months": [8, 9],
        "good_months": [7, 10],
        "bait": "Small shrimp; tiny cut baits (rarely caught)",
        "rig": "Knocker rig with small hooks",
        "hook_size": "#6-#2 small hook",
        "sinker": "Small split shot",
        "explanation_cold": "Honeycomb cowfish are tropical and not found in NC during cold months.",
        "explanation_warm": "Honeycomb cowfish are quirky, horned boxfish with honeycomb patterning; they are rare tropical strays around NC pier pilings in late summer.",
    },
    {
        "name": "Ocean sunfish (mola mola)",
        "temp_min": 55, "temp_max": 78, "temp_ideal_low": 62, "temp_ideal_high": 72,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "NOT A TARGET SPECIES — occasionally seen basking near piers and offshore",
        "rig": "N/A — observe only",
        "hook_size": "N/A",
        "sinker": "N/A",
        "explanation_cold": "Ocean sunfish are in warmer offshore waters during cold months.",
        "explanation_warm": "Ocean sunfish (mola mola) are the world's heaviest bony fish; they are occasionally spotted basking on the surface near piers and offshore.",
    },
    {
        "name": "Sharksucker (whitefin)",
        "temp_min": 68, "temp_max": 86, "temp_ideal_low": 74, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Cut fish; shrimp; squid (usually incidental)",
        "rig": "Any bottom or float rig (incidental catch)",
        "hook_size": "1/0-4/0 hook",
        "sinker": "Varies",
        "explanation_cold": "Whitefin sharksuckers follow host animals to warmer waters in winter.",
        "explanation_warm": "Whitefin sharksuckers are similar to common remoras but with a white-tipped dorsal fin; they detach from hosts near piers and are caught incidentally.",
    },
    # --- Freshwater/brackish additions ---
    {
        "name": "Redear sunfish (shellcracker)",
        "temp_min": 55, "temp_max": 85, "temp_ideal_low": 68, "temp_ideal_high": 80,
        "peak_months": [4, 5, 6, 7, 8],
        "good_months": [3, 9, 10],
        "bait": "Crickets; red worms; small pieces of shrimp",
        "rig": "Hi-lo rig with very small hooks",
        "hook_size": "#6-#2 bait hook",
        "sinker": "Small split shot",
        "explanation_cold": "Redear sunfish are in deeper holes in brackish creeks during cold months.",
        "explanation_warm": "Redear sunfish (shellcrackers) enter brackish tidal creeks near the coast; they crush snails and mussels with their pharyngeal teeth.",
    },
    {
        "name": "Bluegill",
        "temp_min": 50, "temp_max": 85, "temp_ideal_low": 65, "temp_ideal_high": 80,
        "peak_months": [4, 5, 6, 7, 8, 9],
        "good_months": [3, 10],
        "bait": "Crickets; red worms; bread; small shrimp pieces",
        "rig": "Hi-lo rig with very small hooks",
        "hook_size": "#8-#4 bait hook",
        "sinker": "Small split shot",
        "explanation_cold": "Bluegill are in deeper holes during cold months.",
        "explanation_warm": "Bluegill occasionally enter low-salinity tidal creeks near the ICW; they are aggressive panfish that hit small baits and are fun on ultralight tackle.",
    },
    {
        "name": "Warmouth",
        "temp_min": 55, "temp_max": 85, "temp_ideal_low": 68, "temp_ideal_high": 82,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Crickets; red worms; small minnows; small shrimp",
        "rig": "Hi-lo rig with small hooks",
        "hook_size": "#6-#2 bait hook",
        "sinker": "Small split shot",
        "explanation_cold": "Warmouth hunker in deep holes in brackish creeks during cold months.",
        "explanation_warm": "Warmouth are chunky sunfish that tolerate brackish water; they are found in tidal creeks near the coast and are aggressive feeders.",
    },
    {
        "name": "Flathead catfish",
        "temp_min": 50, "temp_max": 85, "temp_ideal_low": 72, "temp_ideal_high": 82,
        "peak_months": [6, 7, 8, 9],
        "good_months": [5, 10],
        "bait": "Live baitfish; live bluegill; large shrimp; cut bait",
        "rig": "Fish finder rig with heavy fluorocarbon leader",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "3-6 oz egg sinker",
        "explanation_cold": "Flathead catfish are in deep river holes during cold months.",
        "explanation_warm": "Flathead catfish are apex freshwater predators found in the Cape Fear River system; they prefer live bait and can exceed 50 lbs in NC.",
    },
    # --- Gar ---
    {
        "name": "Longnose gar",
        "temp_min": 55, "temp_max": 90, "temp_ideal_low": 72, "temp_ideal_high": 85,
        "peak_months": [5, 6, 7, 8, 9],
        "good_months": [4, 10],
        "bait": "Live minnows; cut fish; rope lures (entangle teeth)",
        "rig": "Float rig with wire leader or rope lure",
        "hook_size": "2/0-5/0 treble or rope lure",
        "sinker": "None or small split shot",
        "explanation_cold": "Longnose gar are sluggish in deep holes during cold months.",
        "explanation_warm": "Longnose gar enter brackish tidal rivers near the coast; these armored, prehistoric fish have needle-like snouts and are tough to hook conventionally.",
    },
]


# ---------------------------------------------------------------------------
# Seasonal explanation overrides -- species that behave differently during
# spring/fall transitions get specific text.  Species NOT listed here fall
# back to explanation_cold (winter) or explanation_warm (summer).
# ---------------------------------------------------------------------------

def _get_season(month: int) -> str:
    """Map month number to meteorological season name."""
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "fall"


SEASONAL_EXPLANATIONS: Dict[str, Dict[str, str]] = {
    "Red drum (puppy drum)": {
        "spring": "Red drum are pushing into the surf zone and inlets as water warms; they feed aggressively on shrimp, crabs and mullet during the spring transition.",
        "fall": "The fall red drum run is on — large schools move through inlets and along the beach, feeding heavily on mullet and menhaden before winter.",
    },
    "Speckled trout (spotted seatrout)": {
        "spring": "Speckled trout are moving onto grass flats and into creeks as water warms; the spring bite picks up fast on live shrimp under popping corks.",
        "fall": "Speckled trout are feeding heavily in creeks and along grass lines before cold weather; this is prime time for big gator trout.",
    },
    "Black drum": {
        "spring": "Black drum are spawning in inlets and around structure; large fish congregate and feed on crabs, clams and shrimp during the spring run.",
        "fall": "Black drum are stacking up around inlets and pilings, feeding aggressively before winter; cut crab and shrimp on bottom rigs are productive.",
    },
    "Sheepshead": {
        "spring": "Sheepshead are spawning around nearshore structure and pilings; this is peak season — fish straight down with fiddler crabs or sand fleas.",
        "fall": "Sheepshead are returning to pier pilings and jetties as water cools; they pick at barnacles and crabs around structure.",
    },
    "Flounder (summer flounder)": {
        "spring": "Summer flounder are migrating inshore through inlets; ambush them with live finger mullet drifted slowly along the bottom near structure.",
        "fall": "Flounder are staging at inlets for their fall migration offshore; this is prime time as they feed heavily before moving to deeper water.",
    },
    "Southern flounder": {
        "spring": "Southern flounder are moving into creeks and inshore waters as spring warms up; live finger mullet near creek mouths is the top producer.",
        "fall": "The fall flounder run is the best fishing of the year — southern flounder push through inlets and creeks heading offshore to spawn.",
    },
    "Bluefish": {
        "spring": "Bluefish are arriving from the south in big schools, feeding voraciously on everything; cut menhaden and metal jigs produce explosive strikes.",
        "fall": "Large bluefish (choppers) are running south through the surf and around piers; the fall run produces the biggest fish of the year.",
    },
    "Spanish mackerel": {
        "spring": "Spanish mackerel are just arriving as water hits the upper 60s; early fish are hungry and hit shiny spoons and live bait aggressively.",
        "fall": "Spanish mackerel are still around but thinning out as water cools; catch them before they migrate south for winter.",
    },
    "Pompano": {
        "spring": "Pompano are running the surf line in spring, feeding on sand fleas and small crustaceans; target the troughs with double-dropper rigs.",
        "fall": "The fall pompano run brings fish back through the surf zone; sand fleas and Fishbites in the wash zone are the ticket.",
    },
    "Spot": {
        "spring": "Spot are starting to move inshore and school along the beach; bloodworms and shrimp on small hooks produce steady catches.",
        "fall": "The fall spot run is a major NC fishing event — massive schools move through the surf and around piers, biting everything.",
    },
    "Atlantic croaker": {
        "spring": "Croaker are beginning to move inshore as water warms; fresh shrimp and bloodworms on bottom rigs catch early fish.",
        "fall": "Fall croaker runs bring big numbers to the surf and piers; they school up and bite aggressively on shrimp and worms.",
    },
    "Striped bass (rockfish)": {
        "spring": "Striped bass are feeding heavily before moving north for summer; target them at dawn and dusk with cut menhaden and live mullet.",
        "fall": "Striped bass are returning from the north and feeding in the surf and around inlets; the fall run offers the best inshore action.",
    },
    "Cobia": {
        "spring": "Cobia are arriving with the warming water; early fish show up around buoys, piers and channel markers — sight-cast live eels or menhaden.",
        "fall": "Late-season cobia are still cruising near structure before migrating south; fish near buoys and pier ends with live bait.",
    },
    "King mackerel (kingfish)": {
        "spring": "King mackerel are arriving from the south; early kings cruise near piers and along the beach chasing baitfish schools.",
        "fall": "The fall king mackerel run brings big fish close to shore and piers; slow-troll live baits on wire leader for smoker kings.",
    },
    "False albacore (little tunny)": {
        "spring": "False albacore are passing through during spring migration; look for surface blitzes and cast jigs or live baits into breaking fish.",
        "fall": "The fall false albacore blitz is legendary — massive schools chase bait to the surface near piers and along the beach.",
    },
    "Whiting (sea mullet, kingfish)": {
        "spring": "Whiting are moving into the surf as water warms; fresh shrimp and sand fleas on double-dropper rigs in the wash zone are deadly.",
        "fall": "Whiting are schooling up in the surf before moving to deeper water; bloodworms and shrimp produce fast action on light tackle.",
    },
    "Gray trout (weakfish)": {
        "spring": "Gray trout are moving inshore through inlets and along the beach; live shrimp on light tackle near structure is the best approach.",
        "fall": "Gray trout are feeding in inlets and along the beach before winter; target the fall run with live shrimp drifted near the bottom.",
    },
    "Tautog (blackfish)": {
        "spring": "Tautog are actively feeding around jetties and pilings as water warms in early spring; this is a brief but productive window.",
        "fall": "Tautog are moving back to nearshore structure as water cools; the fall bite around rock piles and jetties is excellent.",
    },
    "Hickory shad": {
        "spring": "The spring hickory shad run is one of NC's best seasonal events — fish stack up in rivers and at bridges, hammering small shad darts.",
        "fall": "Hickory shad are offshore and not available inshore during fall months.",
    },
    "American shad": {
        "spring": "American shad are making their massive spring spawning run up NC rivers; the Cape Fear and Neuse are packed with fish hitting small bright jigs.",
        "fall": "American shad are in the ocean and not available for inshore fishing during fall.",
    },
    "Striped mullet": {
        "spring": "Mullet are scattered in inlets and creeks; cast-net them for bait or target them on tiny hooks with bread dough.",
        "fall": "The fall mullet run is THE bait event of the year — huge schools push through inlets and along the beach. Stock your freezer.",
    },
    "Cownose ray": {
        "spring": "Cownose rays are beginning to arrive in large migrating schools; they move through the surf and inlets heading north.",
        "fall": "Massive schools of cownose rays migrate south through NC waters in fall; they are commonly hooked while bottom fishing.",
    },
    "Atlantic bonito": {
        "spring": "Atlantic bonito are passing through during spring migration; they school nearshore and hit small metal jigs and live baits.",
        "fall": "Fall is prime bonito season — they blitz bait nearshore and around piers, hitting jigs and small live baits at high speed.",
    },
    "Jack crevalle": {
        "spring": "Jack crevalle are arriving with warm water; early fish push bait in inlets and along the surf line.",
        "fall": "Jack crevalle are still feeding aggressively before migrating south; they crash baitfish schools in inlets and around piers.",
    },
    "Greater amberjack": {
        "spring": "Amberjack are moving onto nearshore wrecks and reefs as water warms; they hit live baits and heavy jigs with brute force.",
        "fall": "Amberjack are feeding aggressively on nearshore structure before winter; the fall bite on wrecks and reefs is excellent.",
    },
    "Gag grouper": {
        "spring": "Gag grouper are moving shallower onto nearshore wrecks and reefs; live bait on heavy tackle near structure is the play.",
        "fall": "Gag grouper are feeding heavily on nearshore reefs before moving to deeper spawning grounds; fall is prime nearshore grouper season.",
    },
    "Red snapper": {
        "spring": "Red snapper are becoming more active on nearshore wrecks as water warms; cut squid and live bait on bottom rigs produce bites.",
        "fall": "Red snapper are aggressive on nearshore structure during fall; they hit cut and live baits readily before winter slowdown.",
    },
    "Tripletail": {
        "spring": "Tripletail are just arriving near buoys and crab pot floats; sight-cast live shrimp to fish laying on their sides near the surface.",
        "fall": "Late-season tripletail are still found near floating structure before migrating south; they become less common as water cools.",
    },
    "Ribbonfish (Atlantic cutlassfish)": {
        "spring": "Ribbonfish are starting to show up around piers and lighted docks as water warms.",
        "fall": "Fall is peak ribbonfish season — they swarm pier lights at night, hitting small shiny jigs and cut bait strips.",
    },
    "Mahi-mahi (dolphinfish)": {
        "spring": "Early mahi are showing up along weedlines and temperature breaks as the Gulf Stream pushes warm water closer to shore.",
        "fall": "Late-season mahi are still available along the Gulf Stream edge; smaller schoolies are common around floating debris.",
    },
    "Black sea bass": {
        "spring": "Black sea bass are active on nearshore wrecks and hard bottom during spring; squid strips and cut bait on bottom rigs are productive.",
        "fall": "Black sea bass are feeding on nearshore reefs before moving inshore for winter; the fall bite over structure is strong.",
    },
    "Blacktip shark": {
        "spring": "Blacktip sharks are arriving with warming water; they begin patrolling the surf zone following schools of mullet and menhaden.",
        "fall": "Blacktip sharks are still feeding in the surf before migrating south; they follow the fall mullet run down the coast.",
    },
    "Smooth dogfish": {
        "spring": "Smooth dogfish are one of the first sharks to arrive inshore in spring; they school along the bottom feeding on crabs, shrimp and small fish.",
        "fall": "Smooth dogfish are feeding heavily before their fall migration; they are abundant from piers and in the surf on any cut bait.",
    },
    "Thresher shark": {
        "spring": "Thresher sharks pass through NC waters during their spring northward migration, following schools of menhaden and herring.",
        "fall": "Thresher sharks are migrating south through NC waters in fall; they are most commonly encountered during the seasonal transition.",
    },
    "Clearnose skate": {
        "spring": "Clearnose skates are still abundant inshore during early spring; they are common bottom catches from piers before moving deeper as water warms.",
        "fall": "Clearnose skates are moving back inshore as water cools; they become increasingly common from piers during the fall transition.",
    },
    "Silver perch": {
        "spring": "Silver perch are moving inshore as water warms; they school along the beach and around piers, biting small shrimp and worm baits.",
        "fall": "Silver perch are schooling up before heading to deeper water; the fall bite from piers and the surf is productive.",
    },
    "Sand seatrout (white trout)": {
        "spring": "Sand seatrout are moving inshore and schooling around structure as water warms; live shrimp and cut bait produce steady action.",
        "fall": "Sand seatrout are feeding actively before winter; they school in good numbers around piers and in the surf.",
    },
    "Atlantic menhaden (bunker)": {
        "spring": "Menhaden schools are pushing inshore and through inlets; stock up on bait with cast nets and sabiki rigs for the season ahead.",
        "fall": "The fall menhaden run brings massive schools along the beach and through inlets; this is the premier bait event — fill your freezer.",
    },
    "Butterfish": {
        "spring": "Butterfish are moving inshore as water cools in late spring; occasional catches from piers during the transition.",
        "fall": "Butterfish are arriving inshore in fall as water cools; they school around pier lights and structure in good numbers.",
    },
    "American eel": {
        "spring": "American eels are becoming more active as water warms; night fishing around piers and docks produces catches.",
        "fall": "Fall is peak eel season as they migrate toward the ocean to spawn; catch them at night around piers for excellent striper bait.",
    },
    "Gulf flounder": {
        "spring": "Gulf flounder are migrating inshore through inlets alongside summer flounder; live finger mullet near structure is the best approach.",
        "fall": "Gulf flounder are staging at inlets for their fall offshore migration; target them with live mullet in the troughs and near pilings.",
    },
    "Southern kingfish (ground mullet)": {
        "spring": "Southern kingfish are moving into the surf as water warms; they arrive slightly earlier than northern kingfish and hit sand fleas and shrimp.",
        "fall": "Southern kingfish are schooling in the surf before heading to deeper water; fall action is fast on shrimp and sand fleas.",
    },
    "Striped burrfish (spiny boxfish)": {
        "spring": "Striped burrfish are common inshore during spring; these spiny puffers inflate when caught and are frequently hooked on bottom baits.",
        "fall": "Striped burrfish are abundant inshore during fall on structure and grass beds; they are common incidental catches.",
    },
    "Atlantic herring": {
        "spring": "Atlantic herring are thinning out as water warms; catch remaining schools on sabiki rigs for striper bait before they leave.",
        "fall": "Atlantic herring are arriving inshore as water cools; sabiki rig them from piers for excellent striper and bluefish bait.",
    },
    "Blueback herring": {
        "spring": "Blueback herring are running up NC rivers for spawning alongside shad; they hit small, bright darts and are excellent bait.",
        "fall": "Blueback herring are offshore and not available inshore during fall.",
    },
    "Alewife": {
        "spring": "Alewife are making their spring spawning run up NC rivers; they hit small darts and jigs at bridges and dams.",
        "fall": "Alewife are offshore and not available inshore during fall.",
    },
    "White perch": {
        "spring": "White perch are moving into tidal creeks and brackish water as temperatures rise; bloodworms and small shrimp produce steady catches.",
        "fall": "White perch are feeding actively in brackish creeks before winter; they school in good numbers and bite small baits readily.",
    },
    "Spotted hake": {
        "spring": "Spotted hake are still present inshore during early spring; they will move deeper as water warms past the upper 50s.",
        "fall": "Spotted hake are moving inshore as water cools; they become increasingly common bottom catches from piers during late fall.",
    },
}


def _get_explanation(sp: Dict[str, Any], month: int, water_temp: float) -> str:
    """Pick the best seasonal explanation for a species.

    Checks for a season-specific override first (spring/fall for species with
    distinct transitional behaviour).  Falls back to the cold/warm explanation
    based on current water temperature.
    """
    season = _get_season(month)
    name = sp["name"]

    overrides = SEASONAL_EXPLANATIONS.get(name)
    if overrides and season in overrides:
        return overrides[season]

    # Default: cold/warm split based on water temperature
    is_cold = water_temp < 65
    return sp["explanation_cold"] if is_cold else sp["explanation_warm"]


# ---------------------------------------------------------------------------
# Dynamic rig recommendations -- built from active species
# ---------------------------------------------------------------------------

RIG_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "fishfinder": {
        "name": "Fish Finder Rig (Carolina Rig)",
        "description": (
            "The most versatile surf and pier rig. A sliding egg or "
            "barrel sinker on the main line above a barrel swivel, "
            "then 18-36 in of fluorocarbon leader to a circle hook. "
            "The sliding sinker lets fish pick up bait without feeling "
            "weight, making it ideal for drum, flounder, and most "
            "bottom species."
        ),
        "mainline": "20-30 lb braid with 40-50 lb shock leader",
        "leader": "18-36 in of 20-30 lb fluorocarbon",
        "image": "images/rigs/fishfinder.svg",
    },
    "hi-lo": {
        "name": "Hi-Lo Rig (Double Drop / Bottom Rig)",
        "description": (
            "Two hooks on dropper loops spaced 12-18 in apart along "
            "a mono leader, with a pyramid or bank sinker at the "
            "bottom. Lets you fish two baits at different depths. "
            "The standard pier and surf panfish rig for whiting, "
            "spot, croaker and sea bass."
        ),
        "mainline": "15-20 lb mono or braid",
        "leader": "30-40 lb mono with two dropper loops",
        "image": "images/rigs/hi-lo.svg",
    },
    "knocker": {
        "name": "Knocker Rig",
        "description": (
            "A sinker threaded directly onto the leader that rests "
            "right against the hook eye. Used for fishing tight to "
            "pier pilings, jetties and rocks where sheepshead, "
            "tautog and structure fish live. The short drop prevents "
            "snags and the direct contact lets you feel subtle bites."
        ),
        "mainline": "30-50 lb braid",
        "leader": "12-24 in of 30-50 lb fluorocarbon",
        "image": "images/rigs/knocker.svg",
    },
    "pompano": {
        "name": "Pompano Rig",
        "description": (
            "A modified hi-lo rig with small floats (beads or "
            "foam) above each hook to lift the bait off the bottom. "
            "Often includes bright beads or teaser blades. Designed "
            "for pompano, whiting, and permit in the surf zone wash. "
            "Fish it in the troughs between sandbars."
        ),
        "mainline": "15-20 lb mono or braid",
        "leader": "30-40 lb mono with two dropper loops and float beads",
        "image": "images/rigs/pompano.svg",
    },
    "float": {
        "name": "Float Rig (Bobber Rig)",
        "description": (
            "A hook suspended beneath a popping cork, cigar float, "
            "or balloon at a set depth. The leader hangs below the "
            "float with a circle or J-hook. Used for pelagics, "
            "mackerel, bluefish, and live-bait fishing from piers. "
            "Adjust depth to match where fish are feeding."
        ),
        "mainline": "15-30 lb mono or braid",
        "leader": "2-4 ft of wire or 30-50 lb fluorocarbon",
        "image": "images/rigs/float.svg",
    },
    "popping-cork": {
        "name": "Popping Cork Rig",
        "description": (
            "A concave-top cork float above 18-36 in of leader and "
            "a circle hook. Pop the rod tip to make the cork splash "
            "and gurgle, imitating feeding fish. The commotion draws "
            "in speckled trout, redfish and flounder. The standard "
            "inshore rig for live shrimp."
        ),
        "mainline": "15-20 lb braid",
        "leader": "18-36 in of 15-20 lb fluorocarbon",
        "image": "images/rigs/popping-cork.svg",
    },
    "kingfish-stinger": {
        "name": "King Mackerel Stinger Rig",
        "description": (
            "A live bait rig with a nose hook and a trailing treble "
            "stinger hook connected by wire. Suspended under a float "
            "or slow-trolled. Kings often slash at the tail of a "
            "baitfish — the stinger hook catches short strikes. "
            "Essential for pier king fishing."
        ),
        "mainline": "20-30 lb mono or braid",
        "leader": "#4-#7 wire with nose hook and trailing stinger treble",
        "image": "images/rigs/kingfish-stinger.svg",
    },
    "shark": {
        "name": "Shark Rig (Heavy Bottom Rig)",
        "description": (
            "A heavy-duty fish finder setup with 3-6 ft of coated "
            "wire or heavy cable leader to a large circle hook. "
            "A balloon or large float optional for suspend-fishing. "
            "Heavy pyramid sinker holds position in current. Pair "
            "with a sand spike and a fighting belt."
        ),
        "mainline": "50-80 lb braid with 80-100 lb shock leader",
        "leader": "3-6 ft of #9-#19 coated wire or 200+ lb cable",
        "image": "images/rigs/shark.svg",
    },
    "sabiki": {
        "name": "Sabiki Rig (Bait Catcher)",
        "description": (
            "A string of 4-8 tiny hooks dressed with flashy material "
            "(fish skin, tinsel, feathers) on short branches above a "
            "small weight. Jigged vertically to catch baitfish like "
            "menhaden, cigar minnows, herring and scad. Essential "
            "for stocking your bait bucket from the pier."
        ),
        "mainline": "10-15 lb mono or braid",
        "leader": "Pre-tied sabiki rig (size #6-#10 hooks)",
        "image": "images/rigs/sabiki.svg",
    },
    "deep-drop": {
        "name": "Deep Drop Rig",
        "description": (
            "A heavy bottom rig for fishing 200-1500+ ft depths "
            "targeting tilefish, snowy grouper, and other deep reef "
            "species. Uses 2-8 lb weights, electric or manual reels, "
            "and typically 2-3 circle hooks on dropper loops above "
            "the sinker. LED lights often added to attract fish."
        ),
        "mainline": "80-130 lb braid on electric reel",
        "leader": "100-200 lb mono with 2-3 dropper loops",
        "image": "images/rigs/deep-drop.svg",
    },
    "trolling": {
        "name": "Trolling Rig",
        "description": (
            "A lure or rigged bait towed behind a moving boat. "
            "Uses outriggers, planers, or downriggers to spread "
            "lines at different depths. Standard for offshore "
            "targeting of tuna, wahoo, marlin, and mahi-mahi."
        ),
        "mainline": "30-80 lb mono or braid",
        "leader": "6-15 ft of 60-200 lb fluorocarbon or wire",
        "image": "images/rigs/trolling.svg",
    },
    "tandem-jig": {
        "name": "Tandem Jig Rig (Shad Dart Rig)",
        "description": (
            "Two small jigs or shad darts tied in tandem on a light "
            "leader. Cast upstream and retrieved or jigged through "
            "current. The standard rig for shad and herring spring "
            "runs in NC rivers."
        ),
        "mainline": "6-10 lb mono or braid",
        "leader": "8-12 lb mono, 18 in between jigs",
        "image": "images/rigs/tandem-jig.svg",
    },
}


def _classify_rig(rig_text: str) -> str:
    """Map a species' rig description to a canonical rig category key."""
    text = rig_text.lower()
    if "n/a" in text or "observe" in text or "protected" in text:
        return ""
    if "deep-drop" in text or "deep drop" in text or "electric reel" in text:
        return "deep-drop"
    if "trolling" in text and "slow" not in text:
        return "trolling"
    if "sabiki" in text or "bait catcher" in text or "gold-hook bait" in text:
        return "sabiki"
    if "shad dart" in text or "tandem" in text:
        return "tandem-jig"
    if "popping" in text or "cork" in text:
        return "popping-cork"
    if "stinger" in text or ("king" in text and "wire" in text):
        return "kingfish-stinger"
    if ("shark" in text or "very heavy wire" in text
            or "stand-up" in text or "heavy wire leader and heavy" in text):
        return "shark"
    if "knocker" in text:
        return "knocker"
    if "pier" in text or "structure" in text or "vertical" in text:
        return "knocker"
    if "pompano" in text or "float bead" in text or ("floats above" in text):
        return "pompano"
    if "double-dropper" in text or "hi-lo" in text or "two-hook" in text:
        return "hi-lo"
    if "float" in text or "free-line" in text or "balloon" in text:
        return "float"
    if ("carolina" in text or "fishfinder" in text or "fish finder" in text
            or "sliding" in text):
        return "fishfinder"
    return "fishfinder"


def build_rig_recommendations(
    species_ranking: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build rig recommendations based on currently-active species.

    Groups active species by rig type and produces one recommendation
    per rig, ordered by the highest-ranked species that uses it.
    """
    rig_groups: Dict[str, List[Dict[str, Any]]] = {}
    rig_order: List[str] = []

    for sp in species_ranking:
        key = _classify_rig(sp["rig"])
        if key not in rig_groups:
            rig_groups[key] = []
            rig_order.append(key)
        rig_groups[key].append(sp)

    recommendations: List[Dict[str, Any]] = []
    for key in rig_order:
        group = rig_groups[key]
        category = RIG_CATEGORIES.get(key)
        if category is None:
            continue

        species_names = [sp["name"] for sp in group]
        hooks = list(dict.fromkeys(sp["hook_size"] for sp in group))
        sinkers = list(dict.fromkeys(sp["sinker"] for sp in group))

        recommendations.append({
            "name": category["name"],
            "description": category["description"],
            "mainline": category["mainline"],
            "leader": category["leader"],
            "hook": " or ".join(hooks[:3]),
            "sinker": " or ".join(sinkers[:3]),
            "targets": species_names,
            "image": category.get("image", ""),
        })

    return recommendations


# Natural baits with the species they target and seasonal availability.
# ``available_months`` controls when a bait is practical to obtain/use.
# ``notes_seasonal`` overrides the default ``notes`` during specific seasons.
# Baits out of season are demoted in the ranking so anglers see what they
# can actually get their hands on right now.
BAIT_DB: List[Dict[str, Any]] = [
    {
        "bait": "Live shrimp",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Top choice for speckled trout and versatile for many species; use under a popping cork or on bottom rigs.",
        "notes_seasonal": {
            "winter": "Live shrimp are scarce at bait shops in winter; frozen shrimp or Fishbites are a reliable substitute.",
        },
        "targets": ["Speckled trout", "Red drum", "Sheepshead", "Black drum"],
    },
    {
        "bait": "Cut mullet",
        "available_months": list(range(1, 13)),
        "notes": "Excellent for red drum and black drum; fresh cut strips release scent and stay on the hook.",
        "notes_seasonal": {
            "fall": "Fall mullet run makes fresh mullet abundant and free; stock up and freeze for year-round use.",
        },
        "targets": ["Red drum", "Black drum", "Bluefish", "Striped bass"],
    },
    {
        "bait": "Menhaden (live or cut)",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Prime bait for red drum, bluefish and striped bass; live menhaden offer a distinct advantage in calm conditions.",
        "notes_seasonal": {
            "winter": "Menhaden are offshore in winter; use frozen cut menhaden or substitute with cut mullet.",
            "fall": "Menhaden schools are thick along the beach during fall; cast-net your own for the freshest bait.",
        },
        "targets": ["Red drum", "Bluefish", "Striped bass", "Cobia"],
    },
    {
        "bait": "Sand fleas (mole crabs)",
        "available_months": [4, 5, 6, 7, 8, 9, 10],
        "notes": "Effective for whiting and pompano; dig in the swash zone for fresh fleas.",
        "notes_seasonal": {
            "winter": "Sand fleas are buried deep or absent in cold months; use Fishbites Sand Flea flavor as a substitute.",
            "spring": "Sand fleas are returning to the swash zone; dig at the water's edge as waves recede.",
        },
        "targets": ["Whiting", "Pompano", "Sheepshead"],
    },
    {
        "bait": "Squid strips",
        "available_months": list(range(1, 13)),
        "notes": "Durable on the hook; attract black sea bass, whiting and puffer fish. Available frozen year-round at any bait shop.",
        "targets": ["Black sea bass", "Whiting", "Northern puffer", "Triggerfish"],
    },
    {
        "bait": "Fiddler crabs",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Essential for sheepshead and tautog; use whole crabs on small strong hooks.",
        "notes_seasonal": {
            "winter": "Fiddler crabs are dormant in winter burrows; check bait shops or use fresh shrimp as a substitute.",
            "spring": "Fiddler crabs are emerging from winter burrows; trap them in marsh mud at low tide.",
        },
        "targets": ["Sheepshead", "Tautog", "Triggerfish"],
    },
    {
        "bait": "Bloodworms",
        "available_months": list(range(1, 13)),
        "notes": "Popular for whiting, black drum, spot and puffer fish; cut into small pieces for double-dropper rigs.",
        "notes_seasonal": {
            "winter": "Bloodworms are a top winter bait; their scent and movement attract cold-water bottom feeders when other baits are scarce.",
            "fall": "Bloodworms during the fall spot run are unbeatable; small pieces on #6 hooks catch spot after spot.",
        },
        "targets": ["Whiting", "Black drum", "Northern puffer", "Spot", "Atlantic croaker"],
    },
    {
        "bait": "Clams and crab pieces",
        "available_months": list(range(1, 13)),
        "notes": "Best for black drum; larger pieces stay on the hook and deter small pickers. Available year-round.",
        "targets": ["Black drum", "Tautog", "Sheepshead"],
    },
    {
        "bait": "Live finger mullet",
        "available_months": [4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Top producer for flounder and red drum; hook through the lips and drift slowly along the bottom.",
        "notes_seasonal": {
            "winter": "Live finger mullet are hard to find in winter; try frozen mullet strips or live shrimp as alternatives.",
            "fall": "Finger mullet are everywhere during the fall run; cast-net your own for the liveliest bait.",
        },
        "targets": ["Flounder", "Red drum", "Speckled trout"],
    },
    {
        "bait": "Fresh shrimp pieces",
        "available_months": list(range(1, 13)),
        "notes": "Cut shrimp on bottom rigs is a universal bait for smaller surf and pier species. Available frozen year-round.",
        "targets": ["Atlantic croaker", "Pinfish", "Pigfish", "Spot", "Gray trout"],
    },
    {
        "bait": "Live cigar minnows or threadfin",
        "available_months": [5, 6, 7, 8, 9, 10],
        "notes": "Prime bait for king mackerel and false albacore; rig on wire leader with stinger hook.",
        "notes_seasonal": {
            "winter": "Cigar minnows are not available inshore in winter; frozen are a poor substitute for kings.",
        },
        "targets": ["King mackerel", "False albacore", "Cobia"],
    },
    {
        "bait": "Large cut menhaden or bluefish chunks",
        "available_months": list(range(1, 13)),
        "notes": "Heavy shark baits; use fresh, bloody chunks on wire leader for maximum scent trail. Frozen works year-round.",
        "targets": ["Blacktip shark", "Bull shark", "Sandbar shark", "Spinner shark", "Dusky shark"],
    },
    {
        "bait": "Live blue runners",
        "available_months": [5, 6, 7, 8, 9, 10],
        "notes": "Top live bait for large gamefish; catch on sabiki rigs and fish on heavy tackle.",
        "notes_seasonal": {
            "winter": "Blue runners are not available inshore in winter months.",
        },
        "targets": ["Greater amberjack", "King mackerel", "Cobia", "Black grouper", "Gag grouper"],
    },
    {
        "bait": "Live menhaden (pogies)",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "The king of live baits in NC; cast-net schools and fish on circle hooks for almost any large gamefish.",
        "notes_seasonal": {
            "winter": "Live pogies are hard to find in winter; use frozen cut menhaden or live mullet as alternatives.",
            "fall": "Massive menhaden schools are along the beach in fall; cast-net more than you need and freeze the rest.",
        },
        "targets": ["Red drum", "Cobia", "Tarpon", "King mackerel", "Jack crevalle", "Gag grouper"],
    },
    {
        "bait": "Ballyhoo (rigged or live)",
        "available_months": [4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Premier offshore trolling bait; rig on wire or heavy fluorocarbon for billfish and pelagics.",
        "notes_seasonal": {
            "winter": "Frozen ballyhoo are available at offshore tackle shops year-round for winter Gulf Stream trips.",
        },
        "targets": ["Mahi-mahi", "Sailfish", "Wahoo", "White marlin", "Blue marlin"],
    },
    {
        "bait": "Shad darts and small jigs",
        "available_months": list(range(1, 13)),
        "notes": "Essential tackle for spring shad runs; fish tandem rigs in current near river mouths and bridges.",
        "notes_seasonal": {
            "spring": "This is THE time for shad darts; bright colors (pink, chartreuse, white) in 1/16-1/8 oz are the standard.",
            "fall": "Off-season for shad; save these for the spring river runs.",
        },
        "targets": ["Hickory shad", "American shad"],
    },
    {
        "bait": "Live crabs (blue crab, fiddler)",
        "available_months": [3, 4, 5, 6, 7, 8, 9, 10, 11],
        "notes": "Top bait for structure-oriented species; use whole small crabs or halved blue crabs on circle hooks.",
        "notes_seasonal": {
            "winter": "Crabs are dormant in cold months; use fresh shrimp or clam pieces as a substitute for structure species.",
        },
        "targets": ["Sheepshead", "Tautog", "Permit", "Triggerfish", "Bonnethead shark"],
    },
    {
        "bait": "Cut squid strips",
        "available_months": list(range(1, 13)),
        "notes": "Durable and versatile bottom fishing bait; stays on the hook well for reef and wreck species. Frozen year-round.",
        "targets": ["Red snapper", "Vermilion snapper", "Black sea bass", "Red porgy", "White grunt", "Scup"],
    },
    {
        "bait": "Cedar plugs and trolling feathers",
        "available_months": list(range(1, 13)),
        "notes": "Classic offshore trolling lures for tuna; troll at 6-8 knots in clean blue water. Tackle shop staple.",
        "targets": ["Blackfin tuna", "Yellowfin tuna", "Atlantic bonito"],
    },
    {
        "bait": "Fishbites (artificial strips)",
        "available_months": list(range(1, 13)),
        "notes": "Scented artificial bait strips that stay on the hook forever; great substitute when live/fresh bait is unavailable.",
        "notes_seasonal": {
            "winter": "Fishbites are the go-to winter bait when live options are scarce; Sand Flea and Shrimp flavors work best.",
        },
        "targets": ["Whiting", "Pompano", "Spot", "Atlantic croaker", "Black drum"],
    },
]


def _score_species(
    sp: Dict[str, Any],
    month: int,
    water_temp: float,
    wind_dir: Optional[str] = None,
    wind_range: Optional[Tuple[float, float]] = None,
    wave_range: Optional[Tuple[float, float]] = None,
    hour: int = 12,
) -> float:
    """Compute a bite-likelihood score for a species given current conditions.

    Score components (max ~100):
    - Temperature fit (0-50): how close current water temp is to the
      species' ideal range.
    - Seasonal fit (0-30): whether the current month is a peak, good,
      or off month.
    - Conditions modifier (-5 to +15): wind direction, wind speed,
      wave height, and time-of-day adjustments.
    - Presence penalty (-100): water temp outside survivable range.
    """
    score = 0.0

    if water_temp < sp["temp_min"] or water_temp > sp["temp_max"]:
        return -100.0

    ideal_low = sp["temp_ideal_low"]
    ideal_high = sp["temp_ideal_high"]
    if ideal_low <= water_temp <= ideal_high:
        score += 50.0
    elif water_temp < ideal_low:
        distance = ideal_low - water_temp
        temp_range = ideal_low - sp["temp_min"]
        score += max(0, 50.0 * (1 - distance / temp_range)) if temp_range > 0 else 25.0
    else:
        distance = water_temp - ideal_high
        temp_range = sp["temp_max"] - ideal_high
        score += max(0, 50.0 * (1 - distance / temp_range)) if temp_range > 0 else 25.0

    if month in sp["peak_months"]:
        score += 30.0
    elif month in sp["good_months"]:
        score += 15.0

    # --- Dynamic conditions modifiers ---
    score += _conditions_modifier(sp, wind_dir, wind_range, wave_range, hour)

    return score


# ---------------------------------------------------------------------------
# Conditions-based scoring modifiers
# ---------------------------------------------------------------------------
# These tables classify species by their preferred fishing conditions.
# Species not explicitly listed get no conditions bonus or penalty.
# ---------------------------------------------------------------------------

# Species that bite better on an incoming (onshore) wind -- the wind pushes
# bait and turbid water toward shore, stimulating feeding.
_ONSHORE_WIND_SPECIES: set = {
    "Red drum (puppy drum)", "Bluefish", "Pompano", "Whiting (sea mullet, kingfish)",
    "Spot", "Atlantic croaker", "Flounder (summer flounder)", "Southern flounder",
    "Gulf flounder", "Spanish mackerel", "Jack crevalle", "Cobia",
    "Southern kingfish (ground mullet)", "Gulf kingfish (gulf whiting)",
    "Blacktip shark", "Spinner shark", "Bull shark",
    "Striped bass (rockfish)", "Black drum",
}

# Species that prefer calmer conditions and/or offshore wind (clearer water).
_CALM_WATER_SPECIES: set = {
    "Sheepshead", "Tautog (blackfish)", "Triggerfish (gray)", "Spadefish (Atlantic)",
    "Mangrove snapper (gray snapper)", "Hogfish", "Bermuda chub (sea chub)",
    "Lookdown", "Sergeant major (damselfish)", "Planehead filefish",
    "Scrawled cowfish", "Ocean triggerfish", "Queen triggerfish",
    "Gray snapper (juvenile)", "Speckled trout (spotted seatrout)",
    "Tarpon", "Tripletail", "Permit", "Snook",
}

# Species that feed more actively in rougher surf.
_ROUGH_SURF_SPECIES: set = {
    "Red drum (puppy drum)", "Bluefish", "Striped bass (rockfish)",
    "Whiting (sea mullet, kingfish)", "Pompano", "Black drum",
    "Smooth dogfish", "Atlantic croaker", "Spot",
    "Southern kingfish (ground mullet)", "Gulf kingfish (gulf whiting)",
}

# Species that feed best in low-light conditions (dawn, dusk, night).
_LOW_LIGHT_SPECIES: set = {
    "Striped bass (rockfish)", "Speckled trout (spotted seatrout)",
    "Red drum (puppy drum)", "Cobia", "Tarpon", "Flounder (summer flounder)",
    "Southern flounder", "Gulf flounder", "Ribbonfish (Atlantic cutlassfish)",
    "Spotted moray eel", "Green moray eel", "American eel", "Conger eel",
    "Squirrelfish", "Bigeye (Priacanthus arenatus)", "Short bigeye",
    "Blacktip shark", "Bull shark", "Sandbar shark", "Lemon shark",
}

# Species that are more active during bright midday conditions.
_DAYTIME_SPECIES: set = {
    "Spanish mackerel", "King mackerel (kingfish)", "Cero mackerel",
    "False albacore (little tunny)", "Mahi-mahi (dolphinfish)",
    "Sergeant major (damselfish)", "Blue tang (surgeonfish)",
    "Spotfin butterflyfish", "Gray angelfish",
    "Bermuda chub (sea chub)", "Pinfish", "Pigfish",
}

# Compass directions grouped for onshore/offshore determination.
# For Wrightsville Beach, NC facing roughly ESE:
#   Onshore winds: S, SE, E, SSE, ESE, SSW
#   Offshore winds: N, NW, W, NNW, WNW, NNE
_ONSHORE_DIRS: set = {"S", "SE", "E", "SSE", "ESE", "SSW", "ENE"}
_OFFSHORE_DIRS: set = {"N", "NW", "W", "NNW", "WNW", "NNE", "NE"}


def _conditions_modifier(
    sp: Dict[str, Any],
    wind_dir: Optional[str],
    wind_range: Optional[Tuple[float, float]],
    wave_range: Optional[Tuple[float, float]],
    hour: int,
) -> float:
    """Compute a conditions-based score modifier for a species.

    Returns a value between roughly -5 and +15 based on how well current
    wind direction, wind speed, wave height, and time of day match the
    species' preferred conditions.
    """
    modifier = 0.0
    name = sp["name"]

    # --- Wind direction modifier (up to +5 / -3) ---
    if wind_dir:
        is_onshore = wind_dir in _ONSHORE_DIRS
        is_offshore = wind_dir in _OFFSHORE_DIRS

        if name in _ONSHORE_WIND_SPECIES:
            modifier += 5.0 if is_onshore else (-3.0 if is_offshore else 0.0)
        elif name in _CALM_WATER_SPECIES:
            modifier += 5.0 if is_offshore else (-3.0 if is_onshore else 0.0)

    # --- Wind speed modifier (up to +3 / -2) ---
    if wind_range:
        wind_avg = (wind_range[0] + wind_range[1]) / 2.0
        if name in _ROUGH_SURF_SPECIES:
            # Moderate wind (10-18 kt) stirs up bait -- bonus
            if 10 <= wind_avg <= 18:
                modifier += 3.0
            elif wind_avg < 5:
                modifier -= 2.0
        elif name in _CALM_WATER_SPECIES:
            # Calm conditions (< 8 kt) are ideal
            if wind_avg < 8:
                modifier += 3.0
            elif wind_avg > 15:
                modifier -= 2.0

    # --- Wave height modifier (up to +4 / -2) ---
    if wave_range:
        wave_avg = (wave_range[0] + wave_range[1]) / 2.0
        if name in _ROUGH_SURF_SPECIES:
            # Moderate surf (2-5 ft) concentrates bait in troughs
            if 2 <= wave_avg <= 5:
                modifier += 4.0
            elif wave_avg < 1:
                modifier -= 1.0
        elif name in _CALM_WATER_SPECIES:
            if wave_avg < 2:
                modifier += 4.0
            elif wave_avg > 4:
                modifier -= 2.0

    # --- Time of day modifier (up to +3 / -1) ---
    is_low_light = hour < 7 or hour > 18  # before 7am or after 6pm
    is_midday = 10 <= hour <= 15

    if name in _LOW_LIGHT_SPECIES:
        modifier += 3.0 if is_low_light else (-1.0 if is_midday else 0.0)
    elif name in _DAYTIME_SPECIES:
        modifier += 3.0 if is_midday else (-1.0 if is_low_light else 0.0)

    return modifier


# Minimum score to include a species in the forecast.
# This filters out species that technically survive but aren't really biting.
SPECIES_SCORE_THRESHOLD = 30


def build_species_ranking(
    month: int,
    water_temp: float,
    wind_dir: Optional[str] = None,
    wind_range: Optional[Tuple[float, float]] = None,
    wave_range: Optional[Tuple[float, float]] = None,
    hour: int = 12,
) -> List[Dict[str, Any]]:
    """Dynamically rank species based on conditions.

    Factors in water temperature, month, wind direction, wind speed,
    wave height, and time of day.  Only species scoring above
    SPECIES_SCORE_THRESHOLD are included.  Each species gets an
    activity label: Hot, Active, or Possible.
    """
    scored = []
    for sp in SPECIES_DB:
        s = _score_species(
            sp, month, water_temp,
            wind_dir=wind_dir,
            wind_range=wind_range,
            wave_range=wave_range,
            hour=hour,
        )
        if s >= SPECIES_SCORE_THRESHOLD:
            explanation = _get_explanation(sp, month, water_temp)
            scored.append((s, sp, explanation))

    scored.sort(key=lambda x: x[0], reverse=True)

    result: List[Dict[str, Any]] = []
    for rank, (score, sp, explanation) in enumerate(scored[:10], start=1):
        if score >= 65:
            activity = "Hot"
        elif score >= 50:
            activity = "Active"
        else:
            activity = "Possible"

        result.append({
            "rank": rank,
            "name": sp["name"],
            "score": round(score, 1),
            "activity": activity,
            "explanation": explanation,
            "bait": sp["bait"],
            "rig": sp["rig"],
            "hook_size": sp["hook_size"],
            "sinker": sp["sinker"],
        })

    return result


def build_bait_ranking(
    species_ranking: List[Dict[str, Any]],
    month: int,
) -> List[Dict[str, str]]:
    """Rank baits by relevance to the current top species and season.

    Baits whose target species rank highly are scored higher.  Baits that are
    out of season (``available_months``) receive a penalty so anglers see what
    they can actually get right now.  Season-specific notes override defaults.
    """
    season = _get_season(month)

    # Map species short names to their rank for quick lookup.
    species_ranks: Dict[str, int] = {}
    for sp in species_ranking:
        short = sp["name"].split("(")[0].strip()
        species_ranks[short] = sp["rank"]

    scored_baits: List[Tuple[float, Dict[str, str]]] = []
    for bait_entry in BAIT_DB:
        bait_score = 0.0
        for target in bait_entry["targets"]:
            rank = species_ranks.get(target)
            if rank is not None:
                bait_score += max(0, 20 - rank)

        # Penalise out-of-season baits so in-season options float to the top
        available = bait_entry.get("available_months")
        if available and month not in available:
            bait_score *= 0.25

        # Pick season-specific notes when available
        notes = bait_entry["notes"]
        seasonal_notes = bait_entry.get("notes_seasonal", {})
        if season in seasonal_notes:
            notes = seasonal_notes[season]

        scored_baits.append((bait_score, {"bait": bait_entry["bait"], "notes": notes}))

    scored_baits.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored_baits]


def generate_forecast() -> Dict[str, Any]:
    """Generate the complete fishing forecast.

    Fetches marine conditions and water temperature, classifies fishability,
    then dynamically determines which species are biting based on the current
    month and water temperature.  Rig recommendations are matched to active
    species.
    """
    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    month = now.month

    wind_range, wave_range, wind_dir = get_marine_conditions(month)
    verdict = classify_conditions(wind_range, wave_range)

    water_temp, temp_is_live = get_water_temp(month)

    def format_range(r: Optional[Tuple[float, float]], unit: str) -> str:
        if r is None:
            return "Unknown"
        low, high = r
        if low == high:
            return f"{low:.0f} {unit}"
        return f"{low:.0f}-{high:.0f} {unit}"

    try:
        sunrise, sunset = _sun_times(now)
        sun_str = f"{sunrise.strftime('%-I:%M %p')} / {sunset.strftime('%-I:%M %p')}"
    except Exception:
        sun_str = "Unavailable"

    wind_str = format_range(wind_range, "kt")
    if wind_dir and wind_str != "Unknown":
        wind_str = f"{wind_dir} {wind_str}"

    conditions = {
        "wind": wind_str,
        "waves": format_range(wave_range, "ft"),
        "verdict": verdict,
        "water_temp_f": round(water_temp, 1),
        "water_temp_live": temp_is_live,
        "sunrise_sunset": sun_str,
    }

    species = build_species_ranking(
        month, water_temp,
        wind_dir=wind_dir,
        wind_range=wind_range,
        wave_range=wave_range,
        hour=now.hour,
    )
    rig_recommendations = build_rig_recommendations(species)

    forecast: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "conditions": conditions,
        "species": species,
        "rig_recommendations": rig_recommendations,
        "bait_rankings": build_bait_ranking(species, month),
    }
    return forecast


def load_cached_forecast() -> Optional[Dict[str, Any]]:
    """Load the cached forecast from disk if present."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_forecast(data: Dict[str, Any]) -> None:
    """Persist the forecast to disk."""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# Maximum age (in hours) before a cached forecast is considered stale
# and automatically refreshed on the next page load.
CACHE_MAX_AGE_HOURS = 4


def _forecast_age_minutes(forecast: Dict[str, Any]) -> Optional[float]:
    """Return the age of a cached forecast in minutes, or None."""
    try:
        generated = datetime.fromisoformat(forecast["generated_at"])
        now = datetime.now(ZoneInfo("America/New_York"))
        return (now - generated).total_seconds() / 60
    except Exception:
        return None


def _human_age(minutes: Optional[float]) -> str:
    """Convert a duration in minutes to a human-friendly string."""
    if minutes is None:
        return ""
    if minutes < 1:
        return "just now"
    if minutes < 60:
        m = int(minutes)
        return f"{m} min ago"
    hours = minutes / 60
    if hours < 24:
        h = int(hours)
        return f"{h} hr ago" if h == 1 else f"{h} hrs ago"
    days = int(hours / 24)
    return f"{days} day ago" if days == 1 else f"{days} days ago"


@app.route("/")
def index() -> str:
    """Render the dashboard with the current forecast.

    If the cached forecast is older than ``CACHE_MAX_AGE_HOURS`` the
    server automatically attempts a refresh.  If the query parameter
    ``cached`` is present, a banner indicates that a stale/cached
    forecast is being served (e.g. after a failed refresh).
    """
    cached_flag = request.args.get("cached")
    forecast = load_cached_forecast()

    # Auto-refresh if cache is missing or stale
    needs_refresh = forecast is None
    if forecast and not needs_refresh:
        age = _forecast_age_minutes(forecast)
        if age is not None and age > CACHE_MAX_AGE_HOURS * 60:
            needs_refresh = True

    if needs_refresh:
        try:
            forecast = generate_forecast()
            save_forecast(forecast)
            cached_flag = None  # Fresh data
        except Exception:
            if forecast is None:
                return render_template(
                    "error.html",
                    message="Could not load forecast. Please try refreshing later.",
                ), 500
            # Fall through to serve stale cache
            cached_flag = "true"

    # Attach human-readable age for the template
    forecast["age_human"] = _human_age(_forecast_age_minutes(forecast))

    return render_template("index.html", forecast=forecast, cached=cached_flag)


@app.route("/api/forecast")
def api_forecast() -> Any:
    """Return the current forecast as JSON."""
    forecast = load_cached_forecast()
    if forecast:
        return jsonify(forecast)
    # Return 503 if no forecast is available
    return jsonify({"error": "No forecast available"}), 503


@app.route("/api/refresh", methods=["POST"])
def api_refresh() -> Any:
    """Trigger generation of a new forecast.

    On success the new forecast is saved and the user is redirected
    back to the dashboard.  If an exception occurs, the user is
    redirected back with a `cached` flag indicating the cached
    forecast is being served.
    """
    try:
        new_forecast = generate_forecast()
        save_forecast(new_forecast)
        return redirect(url_for("index"))
    except Exception as exc:
        print(f"Error refreshing forecast: {exc}")
        return redirect(url_for("index", cached="true"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5757))
    app.run(host="0.0.0.0", port=port)
