"""
Surf and Pier Fishing Forecast Application
----------------------------------------

This Flask application generates and serves a 24-hour surf and pier fishing
outlook for Wrightsville Beach and Carolina Beach, North Carolina.  The
forecast combines NOAA marine conditions with seasonal fishing patterns to
dynamically rank the species most likely to bite, recommend bait and bottom
rigs, assess fishability, and cache the most recent report.  A simple web
interface allows manual refreshes on demand.

The application exposes three endpoints:

* ``/`` -- HTML dashboard showing the latest forecast with a refresh button.
* ``/api/forecast`` -- Returns the current forecast in JSON format.
* ``/api/refresh`` -- POST endpoint that triggers regeneration of the
  forecast.  On success the browser is redirected back to ``/``; on
  failure the cached forecast remains untouched.

Data is persisted to ``data/forecast.json``.  If a refresh fails due to a
network error or parsing problem, the server continues to serve the cached
forecast and indicates the timestamp when it was generated.

The forecast generation routine fetches the National Weather Service
marine zone forecast (KILM FZUS52) for the next 24 hours and derives
maximum sustained wind and wave height values.  It also fetches the current
water temperature from NOAA CO-OPS (free, no API key).  Based on
predetermined thresholds the conditions are classified as Fishable, Marginal
or Not worth it.  Species rankings are computed dynamically from the current
month and water temperature.
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


def fetch_marine_forecast() -> str:
    """Fetch the raw marine forecast text from NOAA.

    Returns the HTML page as plain text.  Raises requests.HTTPError on
    network failures.
    """
    url = "https://www.ndbc.noaa.gov/data/Forecasts/FZUS52.KILM.html"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.text


def parse_conditions(text: str) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
    """Parse the forecast text and extract wind and wave ranges for the next 24 hours.

    The NDBC forecast page lists several time periods such as THIS AFTERNOON,
    TONIGHT and the upcoming day name.  This function collects the first three
    relevant segments (covering roughly the next 24 hours) and extracts numeric
    ranges for sustained winds (knots) and seas (feet).  If a range is
    specified (e.g. "10 to 15 kt") the minimum and maximum values are
    captured; otherwise both values are identical.  Returns a tuple
    ``(wind_range, wave_range)`` where each element is itself a
    ``(minimum, maximum)`` pair or ``None`` if no data could be found.
    """
    # Normalize whitespace to simplify regex searches
    cleaned = "\n".join(line.strip() for line in text.splitlines())

    # Build dynamic day-name keywords based on the current date.
    # The NOAA forecast uses abbreviated day names like MON, TUE, WED etc.
    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    day_abbrevs = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    today_abbrev = day_abbrevs[now.weekday()]
    tomorrow_abbrev = day_abbrevs[(now.weekday() + 1) % 7]

    # Keywords that mark forecast time-period headings
    heading_keywords = {"THIS", "TONIGHT", "TODAY", today_abbrev, tomorrow_abbrev}

    lines = cleaned.split("\n")
    selected: List[str] = []
    for line in lines:
        tokens = line.split()
        if not tokens:
            continue
        first = tokens[0]
        if first in heading_keywords:
            selected.append(line)
        if len(selected) >= 3:
            break
    wind_ranges: List[Tuple[float, float]] = []
    wave_ranges: List[Tuple[float, float]] = []
    for seg in selected:
        # Extract sustained wind range (kt)
        wind_match = re.search(r"(\d+)(?:\s*to\s*(\d+))?\s*kt", seg)
        if wind_match:
            low = float(wind_match.group(1))
            high = float(wind_match.group(2)) if wind_match.group(2) else low
            wind_ranges.append((low, high))
        # Extract seas (wave height) range (ft)
        sea_match = re.search(r"seas?\s*(\d+)(?:\s*to\s*(\d+))?\s*ft", seg)
        if sea_match:
            low = float(sea_match.group(1))
            high = float(sea_match.group(2)) if sea_match.group(2) else low
            wave_ranges.append((low, high))
    if wind_ranges:
        wind_min = min(w[0] for w in wind_ranges)
        wind_max = max(w[1] for w in wind_ranges)
        wind_range = (wind_min, wind_max)
    else:
        wind_range = None
    if wave_ranges:
        wave_min = min(s[0] for s in wave_ranges)
        wave_max = max(s[1] for s in wave_ranges)
        wave_range = (wave_min, wave_max)
    else:
        wave_range = None
    return wind_range, wave_range


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
# NOAA tide predictions (free, no API key)
# ---------------------------------------------------------------------------

TIDE_PREDICTIONS_URL = (
    "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    "?begin_date={begin}&end_date={end}&station={station}"
    "&product=predictions&datum=MLLW&units=english"
    "&time_zone=lst_ldt&format=json&interval=hilo"
)


def fetch_tide_predictions() -> List[Dict[str, Any]]:
    """Fetch today's and tomorrow's high/low tide predictions from NOAA CO-OPS.

    Uses the same Wrightsville Beach station (8658163).  Returns a list of
    dicts with keys ``time``, ``height_ft`` and ``type`` ("High" or "Low").
    Returns an empty list on any failure.
    """
    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    date_str = now.strftime("%Y%m%d")
    tomorrow = now + timedelta(days=1)
    end_str = tomorrow.strftime("%Y%m%d")

    try:
        url = TIDE_PREDICTIONS_URL.format(
            begin=date_str, end=end_str, station=WATER_TEMP_STATION,
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result: List[Dict[str, Any]] = []
        for p in data.get("predictions", []):
            result.append({
                "time": p["t"],
                "height_ft": round(float(p["v"]), 1),
                "type": "High" if p["type"] == "H" else "Low",
            })
        return result
    except Exception:
        return []


def get_current_tide_trend(tides: List[Dict[str, Any]]) -> str:
    """Determine the current tide trend based on the next tide event.

    Returns a human-readable string like "Incoming (rising toward high tide
    at 2:34 PM)" or "Unknown" when data is unavailable.
    """
    if not tides:
        return "Unknown"

    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)

    for tide in tides:
        try:
            tide_time = datetime.strptime(tide["time"], "%Y-%m-%d %H:%M")
            tide_time = tide_time.replace(tzinfo=tz)
        except (ValueError, KeyError):
            continue
        if tide_time > now:
            time_str = tide_time.strftime("%-I:%M %p")
            if tide["type"] == "High":
                return f"Incoming (rising toward high tide at {time_str})"
            else:
                return f"Outgoing (falling toward low tide at {time_str})"

    return "Unknown"


# ---------------------------------------------------------------------------
# Moon phase and solunar fishing rating (pure math, no API)
# ---------------------------------------------------------------------------

def _moon_phase_angle(dt: datetime) -> float:
    """Return the moon phase angle in degrees for a given datetime.

    Uses the known new-moon reference of 2000-01-06 18:14 UTC and the
    synodic month (29.53059 days).  0/360 = new moon, 180 = full moon.
    """
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=ZoneInfo("UTC"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    diff_seconds = (dt - ref).total_seconds()
    synodic_seconds = 29.53058770576 * 86400
    phase = (diff_seconds % synodic_seconds) / synodic_seconds
    return phase * 360.0


def get_moon_info(dt: datetime) -> Dict[str, Any]:
    """Compute moon phase name, illumination and solunar fishing rating.

    The solunar rating is based on proximity to new/full moon:
    - New/Full moon (+/- ~2 days): Excellent
    - First/Last quarter approaching: Good
    - Mid-quarter phases: Fair / Poor
    """
    angle = _moon_phase_angle(dt)

    if angle < 22.5 or angle >= 337.5:
        phase_name = "New Moon"
    elif angle < 67.5:
        phase_name = "Waxing Crescent"
    elif angle < 112.5:
        phase_name = "First Quarter"
    elif angle < 157.5:
        phase_name = "Waxing Gibbous"
    elif angle < 202.5:
        phase_name = "Full Moon"
    elif angle < 247.5:
        phase_name = "Waning Gibbous"
    elif angle < 292.5:
        phase_name = "Last Quarter"
    else:
        phase_name = "Waning Crescent"

    illumination = round((1 - math.cos(math.radians(angle))) / 2 * 100)

    dist_from_new = min(angle, 360 - angle)
    dist_from_full = abs(angle - 180)
    min_dist = min(dist_from_new, dist_from_full)

    if min_dist < 30:
        solunar_rating = "Excellent"
    elif min_dist < 60:
        solunar_rating = "Good"
    elif min_dist < 90:
        solunar_rating = "Fair"
    else:
        solunar_rating = "Poor"

    return {
        "phase_name": phase_name,
        "illumination_pct": illumination,
        "solunar_rating": solunar_rating,
    }


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
# Best fishing windows
# ---------------------------------------------------------------------------

def compute_best_windows(
    tides: List[Dict[str, Any]],
    moon: Dict[str, Any],
    dt: datetime,
) -> List[Dict[str, str]]:
    """Identify the best fishing windows for the next 24 hours.

    Prime fishing windows are:
    1. **Tide changes**: 1 hour before through 1 hour after each high/low
       tide -- moving water stirs up bait and triggers feeding.
    2. **Dawn / dusk**: the classic low-light feeding periods.
    3. **Overlap**: when a tide change coincides with dawn or dusk, that
       window gets the highest priority.

    Returns a list of dicts with ``window``, ``time``, and ``reason``.
    """
    tz = ZoneInfo("America/New_York")
    now = dt if dt.tzinfo else dt.replace(tzinfo=tz)

    try:
        sunrise, sunset = _sun_times(now)
    except Exception:
        sunrise = sunset = None

    windows: List[Dict[str, str]] = []

    # Tide-change windows
    for tide in tides:
        try:
            tide_time = datetime.strptime(tide["time"], "%Y-%m-%d %H:%M")
            tide_time = tide_time.replace(tzinfo=tz)
        except (ValueError, KeyError):
            continue

        # Only include future events within ~24 hours
        if tide_time < now or tide_time > now + timedelta(hours=26):
            continue

        start = tide_time - timedelta(hours=1)
        end = tide_time + timedelta(hours=1)
        time_str = f"{start.strftime('%-I:%M %p')} - {end.strftime('%-I:%M %p')}"

        # Check for dawn/dusk overlap
        near_dawn = sunrise and abs((tide_time - sunrise).total_seconds()) < 5400
        near_dusk = sunset and abs((tide_time - sunset).total_seconds()) < 5400

        if near_dawn:
            quality = "Prime"
            reason = f"{tide['type']} tide change at dawn -- peak feeding window"
        elif near_dusk:
            quality = "Prime"
            reason = f"{tide['type']} tide change at dusk -- peak feeding window"
        else:
            quality = "Good"
            reason = f"{tide['type']} tide change -- moving water triggers feeding"

        windows.append({
            "quality": quality,
            "time": time_str,
            "reason": reason,
        })

    # Add dawn and dusk if they don't already overlap with a tide window
    if sunrise and sunrise > now:
        dawn_start = sunrise - timedelta(minutes=30)
        dawn_end = sunrise + timedelta(minutes=90)
        already_covered = any("dawn" in w["reason"].lower() for w in windows)
        if not already_covered:
            windows.append({
                "quality": "Good",
                "time": f"{dawn_start.strftime('%-I:%M %p')} - {dawn_end.strftime('%-I:%M %p')}",
                "reason": "Dawn -- low-light feeding period",
            })

    if sunset and sunset > now:
        dusk_start = sunset - timedelta(minutes=90)
        dusk_end = sunset + timedelta(minutes=30)
        already_covered = any("dusk" in w["reason"].lower() for w in windows)
        if not already_covered:
            windows.append({
                "quality": "Good",
                "time": f"{dusk_start.strftime('%-I:%M %p')} - {dusk_end.strftime('%-I:%M %p')}",
                "reason": "Dusk -- low-light feeding period",
            })

    # Sort by quality (Prime first) then by time
    quality_order = {"Prime": 0, "Good": 1}
    windows.sort(key=lambda w: quality_order.get(w["quality"], 2))

    return windows


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
        "rig": "Carolina/fishfinder rig with sliding egg sinker",
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
        "rig": "Double-dropper (hi-lo) or Carolina rig",
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
        "rig": "Carolina rig with short fluorocarbon leader",
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
        "rig": "Two-hook bottom rig or Carolina rig with heavy leader",
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
        "rig": "Double-dropper bottom rig on braided line",
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
        "rig": "Fishfinder rig with steel leader",
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
        "rig": "Double-dropper rig with small hooks",
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
        "rig": "Two-hook bottom rig",
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
        "rig": "Fishfinder or chunk bait rig with heavy leader",
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
        "rig": "Carolina rig with 24-36 in fluorocarbon leader",
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
        "rig": "Long-shank hook with wire leader, free-lined or under float",
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
        "rig": "Double-dropper rig with floats above hooks",
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
        "rig": "Double-dropper rig with small hooks",
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
        "rig": "Heavy fishfinder rig or free-lined live bait",
        "hook_size": "5/0-8/0 circle hook",
        "sinker": "2-4 oz egg sinker or none for free-lining",
        "explanation_cold": "Cobia have migrated south and are not present in NC waters during cold months.",
        "explanation_warm": "Cobia are cruising near the surface around piers, buoys and structure; sight-cast live eels or large baits to visible fish.",
    },
]

# Static rig templates -- these don't change with conditions
RIG_TEMPLATES: List[Dict[str, str]] = [
    {
        "name": "Carolina/Fishfinder Rig",
        "description": (
            "A sliding egg sinker rides on the main line above a swivel. "
            "Tie 18-24 inches of 20-30 lb fluorocarbon leader and finish with a circle hook. "
            "Ideal for red drum, black drum, bluefish and striped bass."
        ),
        "mainline": "20-30 lb braid with 40 lb shock leader",
        "leader": "18-24 in 20-30 lb fluorocarbon",
        "hook": "2/0-5/0 circle hook",
        "sinker": "Sliding egg sinker 1-4 oz",
    },
    {
        "name": "Double-Dropper (Hi-Lo) Rig",
        "description": (
            "Two dropper loops spaced along a 30-40 lb mono leader with a weight at the bottom. "
            "Effective for whiting, black drum and sea bass when baited with shrimp, squid or worms."
        ),
        "mainline": "15-20 lb mono or braid",
        "leader": "30-40 lb mono with two dropper loops",
        "hook": "#4-3/0 circle hooks on each dropper",
        "sinker": "Pyramid or bank sinker 2-4 oz",
    },
    {
        "name": "Simple Surf Rig",
        "description": (
            "A lightweight rig for small species such as puffer and spot. "
            "Attach a short leader and small hook below a pyramid weight."
        ),
        "mainline": "10-15 lb mono or braid",
        "leader": "12-18 in 15-20 lb mono",
        "hook": "#6-#4 baitholder or circle hook",
        "sinker": "Pyramid sinker 1-2 oz",
    },
    {
        "name": "Pier Structure Rig",
        "description": (
            "Designed for fishing around pilings and rock structure. "
            "Use heavy braid and fluorocarbon leader with a short drop to minimize snags. "
            "Perfect for sheepshead and tautog."
        ),
        "mainline": "30-50 lb braid",
        "leader": "1-2 ft 30-50 lb fluorocarbon",
        "hook": "#2-3/0 J-hook or circle hook",
        "sinker": "Egg or bank sinker 2-4 oz",
    },
]

# Natural baits with the species they target.  Baits whose target species
# rank highly in the current forecast are promoted automatically.
BAIT_DB: List[Dict[str, Any]] = [
    {
        "bait": "Live shrimp",
        "notes": "Top choice for speckled trout and versatile for many species; use under a popping cork or on bottom rigs.",
        "targets": ["Speckled trout", "Red drum", "Sheepshead", "Black drum"],
    },
    {
        "bait": "Cut mullet",
        "notes": "Excellent for red drum and black drum; fresh cut strips release scent and stay on the hook.",
        "targets": ["Red drum", "Black drum", "Bluefish", "Striped bass"],
    },
    {
        "bait": "Menhaden (live or cut)",
        "notes": "Prime bait for red drum, bluefish and striped bass; live menhaden offer a distinct advantage in calm conditions.",
        "targets": ["Red drum", "Bluefish", "Striped bass", "Cobia"],
    },
    {
        "bait": "Sand fleas (mole crabs)",
        "notes": "Effective for whiting and pompano; dig in the swash zone for fresh fleas.",
        "targets": ["Whiting", "Pompano", "Sheepshead"],
    },
    {
        "bait": "Squid strips",
        "notes": "Durable on the hook; attract black sea bass, whiting and puffer fish.",
        "targets": ["Black sea bass", "Whiting", "Northern puffer"],
    },
    {
        "bait": "Fiddler crabs",
        "notes": "Essential for sheepshead and tautog; use whole crabs on small strong hooks.",
        "targets": ["Sheepshead", "Tautog"],
    },
    {
        "bait": "Bloodworms",
        "notes": "Popular for whiting, black drum, spot and puffer fish; cut into small pieces for double-dropper rigs.",
        "targets": ["Whiting", "Black drum", "Northern puffer", "Spot"],
    },
    {
        "bait": "Clams and crab pieces",
        "notes": "Best for black drum; larger pieces stay on the hook and deter small pickers.",
        "targets": ["Black drum", "Tautog", "Sheepshead"],
    },
    {
        "bait": "Live finger mullet",
        "notes": "Top producer for flounder and red drum; hook through the lips and drift slowly along the bottom.",
        "targets": ["Flounder", "Red drum", "Speckled trout"],
    },
]


def _score_species(
    sp: Dict[str, Any],
    month: int,
    water_temp: float,
    solunar_rating: str = "Fair",
) -> float:
    """Compute a bite-likelihood score for a species given current conditions.

    Score components:
    - Temperature fit (0-50): how close current water temp is to the
      species' ideal range.
    - Seasonal fit (0-30): whether the current month is a peak, good,
      or off month.
    - Solunar bonus (0-15): stronger moon phases boost feeding activity.
    - Presence penalty (-100): species gets a large penalty if water temp
      is outside its survivable range entirely.
    """
    score = 0.0

    # Temperature scoring
    if water_temp < sp["temp_min"] or water_temp > sp["temp_max"]:
        return -100.0  # Species not present at this temperature

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

    # Seasonal scoring
    if month in sp["peak_months"]:
        score += 30.0
    elif month in sp["good_months"]:
        score += 15.0
    # Off-season months get 0

    # Solunar bonus -- stronger moon phases increase feeding aggression
    solunar_bonus = {"Excellent": 15.0, "Good": 10.0, "Fair": 5.0, "Poor": 0.0}
    score += solunar_bonus.get(solunar_rating, 0.0)

    return score


def build_species_ranking(
    month: int,
    water_temp: float,
    solunar_rating: str = "Fair",
) -> List[Dict[str, Any]]:
    """Dynamically rank species based on current month, water temperature and
    solunar conditions.

    Species with negative scores (outside survivable temp range) are
    excluded.  The rest are sorted by score descending and assigned ranks.
    """
    is_cold = water_temp < 65

    scored = []
    for sp in SPECIES_DB:
        s = _score_species(sp, month, water_temp, solunar_rating)
        if s >= 0:
            explanation = sp["explanation_cold"] if is_cold else sp["explanation_warm"]
            scored.append((s, sp, explanation))

    scored.sort(key=lambda x: x[0], reverse=True)

    result: List[Dict[str, Any]] = []
    for rank, (score, sp, explanation) in enumerate(scored, start=1):
        result.append({
            "rank": rank,
            "name": sp["name"],
            "explanation": explanation,
            "bait": sp["bait"],
            "rig": sp["rig"],
            "hook_size": sp["hook_size"],
            "sinker": sp["sinker"],
        })

    return result


def build_bait_ranking(species_ranking: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Rank baits by relevance to the current top species.

    Baits whose target species appear higher in the species ranking
    receive a higher score and are listed first.
    """
    # Map species short names to their rank for quick lookup.
    # The short name is derived from the first word(s) before any parenthetical.
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
                # Higher-ranked (lower number) targets contribute more
                bait_score += max(0, 20 - rank)
        scored_baits.append((bait_score, {"bait": bait_entry["bait"], "notes": bait_entry["notes"]}))

    scored_baits.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored_baits]


def generate_forecast() -> Dict[str, Any]:
    """Generate the complete fishing forecast.

    Fetches marine conditions and water temperature, computes wind and wave
    ranges, classifies fishability, then dynamically assembles species
    rankings, rig templates and bait recommendations based on the current
    month and water temperature.
    """
    raw_text = fetch_marine_forecast()
    wind_range, wave_range = parse_conditions(raw_text)
    verdict = classify_conditions(wind_range, wave_range)
    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    month = now.month

    # Fetch live water temperature; fall back to monthly average
    water_temp, temp_is_live = get_water_temp(month)

    # Fetch tide predictions and determine current trend
    tides = fetch_tide_predictions()
    tide_trend = get_current_tide_trend(tides)

    # Moon phase and solunar rating
    moon = get_moon_info(now)

    # Format ranges for display
    def format_range(r: Optional[Tuple[float, float]], unit: str) -> str:
        if r is None:
            return "Unknown"
        low, high = r
        if low == high:
            return f"{low:.0f} {unit}"
        return f"{low:.0f}-{high:.0f} {unit}"

    # Compute sunrise / sunset for display
    try:
        sunrise, sunset = _sun_times(now)
        sun_str = f"{sunrise.strftime('%-I:%M %p')} / {sunset.strftime('%-I:%M %p')}"
    except Exception:
        sun_str = "Unavailable"

    conditions = {
        "wind": format_range(wind_range, "kt"),
        "waves": format_range(wave_range, "ft"),
        "verdict": verdict,
        "water_temp_f": round(water_temp, 1),
        "water_temp_live": temp_is_live,
        "tide_trend": tide_trend,
        "sunrise_sunset": sun_str,
    }

    species = build_species_ranking(month, water_temp, moon["solunar_rating"])

    # Best fishing windows (tides + dawn/dusk + solunar overlap)
    windows = compute_best_windows(tides, moon, now)

    forecast: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "conditions": conditions,
        "tides": tides,
        "moon": moon,
        "windows": windows,
        "species": species,
        "rig_templates": RIG_TEMPLATES,
        "bait_rankings": build_bait_ranking(species),
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


@app.route("/")
def index() -> str:
    """Render the dashboard with the current forecast.

    If the query parameter ``cached`` is present, a banner will be
    displayed indicating that a cached forecast is being served.  This
    parameter is set when a refresh fails and the user is redirected
    back to this route.
    """
    cached_flag = request.args.get("cached")
    forecast = load_cached_forecast()
    if not forecast:
        # If no cached forecast exists, attempt to generate one immediately
        try:
            forecast = generate_forecast()
            save_forecast(forecast)
        except Exception as exc:
            return render_template(
                "error.html",
                message="Could not load forecast. Please try refreshing later.",
            ), 500
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
