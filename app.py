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

        # Wind direction (e.g. "SW winds", "N winds", "Variable winds")
        dir_match = re.search(
            r"(N|NE|NW|E|SE|S|SW|W|VARIABLE)\s+wind",
            text, re.IGNORECASE,
        )
        if dir_match:
            wind_directions.append(dir_match.group(1).upper())

        # Wind speed in knots
        wind_match = re.search(
            r"(\d+)(?:\s*to\s*(\d+))?\s*kt", text, re.IGNORECASE,
        )
        if wind_match:
            low = float(wind_match.group(1))
            high = float(wind_match.group(2)) if wind_match.group(2) else low
            wind_ranges.append((low, high))

        # Sea/wave height in feet
        sea_match = re.search(
            r"seas?\s*(\d+)(?:\s*to\s*(\d+))?\s*ft", text, re.IGNORECASE,
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
    for rank, (score, sp, explanation) in enumerate(scored, start=1):
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
