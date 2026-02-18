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
# NWS marine zone forecast (free, no API key, requires User-Agent)
# ---------------------------------------------------------------------------

# AMZ158 = Coastal waters from Surf City to Cape Fear NC out 20 NM
NWS_MARINE_ZONE = "AMZ158"
NWS_FORECAST_URL = (
    f"https://api.weather.gov/zones/forecast/{NWS_MARINE_ZONE}/forecast"
)


def fetch_marine_forecast() -> List[Dict[str, Any]]:
    """Fetch marine forecast periods from the NWS API.

    Returns a list of period dicts with ``name`` and ``detailedForecast``.
    """
    headers = {
        "User-Agent": "(SurfPierForecast, github.com/ConnnnerDay/surf-pier-forecast)",
        "Accept": "application/geo+json",
    }
    response = requests.get(NWS_FORECAST_URL, headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()
    return data["properties"]["periods"]


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
        "rig": "Pier structure rig with short fluorocarbon leader",
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
        "rig": "Pier structure rig with heavy leader",
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
        "rig": "Carolina/fishfinder rig with steel leader",
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
        "rig": "Double-dropper bottom rig with small hooks",
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
        "rig": "Carolina/fishfinder rig with heavy leader",
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
        "rig": "Carolina/fishfinder rig, heavy, or free-lined live bait",
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
        "rig": "Double-dropper rig with small hooks",
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
        "rig": "Carolina/fishfinder rig with light fluorocarbon leader",
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
        "rig": "Float rig with wire leader and stinger hook",
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
        "rig": "Pier structure rig with small strong hooks",
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
        "rig": "Pier structure rig with light leader and small hooks",
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
        "rig": "Carolina/fishfinder rig, heavy tackle, or free-lined live bait",
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
        "rig": "Double-dropper rig with small hooks",
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
        "rig": "Double-dropper rig with very small hooks",
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
        "rig": "Carolina/fishfinder rig with heavy wire leader",
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
        "rig": "Carolina/fishfinder rig with heavy wire leader",
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
        "rig": "Carolina/fishfinder rig with wire leader",
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
        "rig": "Carolina/fishfinder rig with heavy wire leader and heavy tackle",
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
        "rig": "Carolina/fishfinder rig with heavy wire leader",
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
        "rig": "Carolina/fishfinder rig with light wire or heavy fluorocarbon leader",
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
        "rig": "Carolina/fishfinder rig with heavy wire leader",
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
        "rig": "Carolina/fishfinder rig with heavy wire leader and heavy tackle",
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
        "rig": "Carolina/fishfinder rig with heavy leader",
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
        "rig": "Carolina/fishfinder rig with heavy leader",
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
        "rig": "Carolina/fishfinder rig with heavy leader or free-lined live bait",
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
        "rig": "Double-dropper rig or sabiki with light tackle",
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
        "rig": "Double-dropper rig with small hooks and light leader",
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
        "rig": "Carolina/fishfinder rig, heavy tackle, or vertical jig",
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
        "rig": "Carolina/fishfinder rig with heavy leader or vertical jig",
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
        "rig": "Carolina/fishfinder rig with fluorocarbon leader",
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
        "rig": "Double-dropper bottom rig or Carolina rig with heavy leader",
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
        "rig": "Double-dropper bottom rig (chicken rig) with small hooks",
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
        "rig": "Pier structure rig with fluorocarbon leader",
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
        "rig": "Double-dropper bottom rig with light leader",
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
        "rig": "Carolina/fishfinder rig with heavy fluorocarbon leader",
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
        "rig": "Carolina/fishfinder rig with heavy leader",
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
        "rig": "Carolina/fishfinder rig with heavy fluorocarbon leader",
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
        "rig": "Carolina/fishfinder rig with very heavy leader",
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
        "rig": "Carolina/fishfinder rig with fluorocarbon leader or free-lined",
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
        "rig": "Carolina rig with 24-36 in fluorocarbon leader",
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
        "rig": "Double-dropper bottom rig with small hooks",
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
        "rig": "Double-dropper bottom rig",
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
        "rig": "Double-dropper bottom rig with small hooks",
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
        "rig": "Double-dropper bottom rig",
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
        "rig": "Double-dropper bottom rig or Carolina rig",
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
        "rig": "Pier structure rig with light fluorocarbon leader",
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
        "rig": "Pier structure rig with very small hooks",
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
        "rig": "Double-dropper rig with shad darts (tandem rig)",
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
        "rig": "Double-dropper rig with shad darts (tandem rig)",
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
        "rig": "Double-dropper rig with tiny hooks or cast net",
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
        "rig": "Double-dropper bottom rig or Carolina rig",
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
        "rig": "Carolina/fishfinder rig or double-dropper bottom rig",
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
        "rig": "Double-dropper bottom rig or Carolina rig",
        "hook_size": "#2-2/0 hook",
        "sinker": "1-3 oz pyramid sinker",
        "explanation_cold": "Lizardfish are in deeper water during cold months and rarely caught inshore.",
        "explanation_warm": "Lizardfish are toothy bottom ambush predators common in the surf and around piers; they aggressively hit cut bait and small live baits.",
    },
]


# ---------------------------------------------------------------------------
# Dynamic rig recommendations -- built from active species
# ---------------------------------------------------------------------------

RIG_CATEGORIES: Dict[str, Dict[str, str]] = {
    "carolina": {
        "name": "Carolina/Fishfinder Rig",
        "description": (
            "A sliding egg sinker rides on the main line above a swivel. "
            "Tie 18-24 in of fluorocarbon leader and finish with a circle hook. "
            "The go-to rig for surf and inlet bottom fishing."
        ),
        "mainline": "20-30 lb braid with 40 lb shock leader",
        "leader": "18-24 in 20-30 lb fluorocarbon",
    },
    "double-dropper": {
        "name": "Double-Dropper (Hi-Lo) Rig",
        "description": (
            "Two dropper loops spaced along a 30-40 lb mono leader "
            "with a weight at the bottom. Great for covering two "
            "depths at once."
        ),
        "mainline": "15-20 lb mono or braid",
        "leader": "30-40 lb mono with two dropper loops",
    },
    "pier structure": {
        "name": "Pier Structure Rig",
        "description": (
            "Heavy braid with fluorocarbon leader and a short drop "
            "to minimize snags around pilings, jetties and rock structure."
        ),
        "mainline": "30-50 lb braid",
        "leader": "1-2 ft 30-50 lb fluorocarbon",
    },
    "float": {
        "name": "Float/Free-Line Rig",
        "description": (
            "A long-shank hook on wire or heavy fluorocarbon leader, "
            "free-lined or suspended under a float. Designed for fast "
            "pelagic species near the surface."
        ),
        "mainline": "15-20 lb mono or braid",
        "leader": "Wire or 40 lb fluorocarbon",
    },
    "popping-cork": {
        "name": "Popping-Cork Rig",
        "description": (
            "A concave popping cork above a leader and circle hook. "
            "Pop the cork to create commotion that mimics feeding "
            "activity and draws fish in."
        ),
        "mainline": "15-20 lb braid",
        "leader": "18-24 in 15-20 lb fluorocarbon",
    },
}


def _classify_rig(rig_text: str) -> str:
    """Map a species' rig description to a canonical rig category key."""
    text = rig_text.lower()
    if "popping" in text or "cork" in text:
        return "popping-cork"
    if "pier" in text or "structure" in text:
        return "pier structure"
    if "double-dropper" in text or "hi-lo" in text or "two-hook" in text:
        return "double-dropper"
    if "float" in text or "free-line" in text or "long-shank" in text:
        return "float"
    if "carolina" in text or "fishfinder" in text or "fish finder" in text:
        return "carolina"
    return "carolina"


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
        })

    return recommendations


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
        "targets": ["Black sea bass", "Whiting", "Northern puffer", "Triggerfish"],
    },
    {
        "bait": "Fiddler crabs",
        "notes": "Essential for sheepshead and tautog; use whole crabs on small strong hooks.",
        "targets": ["Sheepshead", "Tautog", "Triggerfish"],
    },
    {
        "bait": "Bloodworms",
        "notes": "Popular for whiting, black drum, spot and puffer fish; cut into small pieces for double-dropper rigs.",
        "targets": ["Whiting", "Black drum", "Northern puffer", "Spot", "Atlantic croaker"],
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
    {
        "bait": "Fresh shrimp pieces",
        "notes": "Cut shrimp on bottom rigs is a universal bait for smaller surf and pier species.",
        "targets": ["Atlantic croaker", "Pinfish", "Pigfish", "Spot", "Gray trout"],
    },
    {
        "bait": "Live cigar minnows or threadfin",
        "notes": "Prime bait for king mackerel and false albacore; rig on wire leader with stinger hook.",
        "targets": ["King mackerel", "False albacore", "Cobia"],
    },
    {
        "bait": "Large cut menhaden or bluefish chunks",
        "notes": "Heavy shark baits; use fresh, bloody chunks on wire leader for maximum scent trail.",
        "targets": ["Blacktip shark", "Bull shark", "Sandbar shark", "Spinner shark", "Dusky shark"],
    },
    {
        "bait": "Live blue runners",
        "notes": "Top live bait for large gamefish; catch on sabiki rigs and fish on heavy tackle.",
        "targets": ["Greater amberjack", "King mackerel", "Cobia", "Black grouper", "Gag grouper"],
    },
    {
        "bait": "Live menhaden (pogies)",
        "notes": "The king of live baits in NC; cast-net schools and fish on circle hooks for almost any large gamefish.",
        "targets": ["Red drum", "Cobia", "Tarpon", "King mackerel", "Jack crevalle", "Gag grouper"],
    },
    {
        "bait": "Ballyhoo (rigged or live)",
        "notes": "Premier offshore trolling bait; rig on wire or heavy fluorocarbon for billfish and pelagics.",
        "targets": ["Mahi-mahi", "Sailfish", "Wahoo", "White marlin", "Blue marlin"],
    },
    {
        "bait": "Shad darts and small jigs",
        "notes": "Essential tackle for spring shad runs; fish tandem rigs in current near river mouths and bridges.",
        "targets": ["Hickory shad", "American shad"],
    },
    {
        "bait": "Live crabs (blue crab, fiddler)",
        "notes": "Top bait for structure-oriented species; use whole small crabs or halved blue crabs on circle hooks.",
        "targets": ["Sheepshead", "Tautog", "Permit", "Triggerfish", "Bonnethead shark"],
    },
    {
        "bait": "Cut squid strips",
        "notes": "Durable and versatile bottom fishing bait; stays on the hook well for reef and wreck species.",
        "targets": ["Red snapper", "Vermilion snapper", "Black sea bass", "Red porgy", "White grunt", "Scup"],
    },
    {
        "bait": "Cedar plugs and trolling feathers",
        "notes": "Classic offshore trolling lures for tuna; troll at 6-8 knots in clean blue water.",
        "targets": ["Blackfin tuna", "Yellowfin tuna", "Atlantic bonito"],
    },
]


def _score_species(
    sp: Dict[str, Any],
    month: int,
    water_temp: float,
) -> float:
    """Compute a bite-likelihood score for a species given current conditions.

    Score components (max 80):
    - Temperature fit (0-50): how close current water temp is to the
      species' ideal range.
    - Seasonal fit (0-30): whether the current month is a peak, good,
      or off month.
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

    return score


# Minimum score (out of 80) to include a species in the forecast.
# This filters out species that technically survive but aren't really biting.
SPECIES_SCORE_THRESHOLD = 30


def build_species_ranking(
    month: int,
    water_temp: float,
) -> List[Dict[str, Any]]:
    """Dynamically rank species based on current month and water temperature.

    Only species scoring above SPECIES_SCORE_THRESHOLD are included.
    Each species gets an activity label: Hot, Active, or Possible.
    """
    is_cold = water_temp < 65

    scored = []
    for sp in SPECIES_DB:
        s = _score_species(sp, month, water_temp)
        if s >= SPECIES_SCORE_THRESHOLD:
            explanation = sp["explanation_cold"] if is_cold else sp["explanation_warm"]
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

    Fetches marine conditions and water temperature, classifies fishability,
    then dynamically determines which species are biting based on the current
    month and water temperature.  Rig recommendations are matched to active
    species.
    """
    periods = fetch_marine_forecast()
    wind_range, wave_range, wind_dir = parse_conditions(periods)
    verdict = classify_conditions(wind_range, wave_range)
    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    month = now.month

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

    species = build_species_ranking(month, water_temp)
    rig_recommendations = build_rig_recommendations(species)

    forecast: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "conditions": conditions,
        "species": species,
        "rig_recommendations": rig_recommendations,
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
