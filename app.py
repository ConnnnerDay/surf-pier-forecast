"""
Surf and Pier Fishing Forecast Application
----------------------------------------

This Flask application generates and serves a 24‑hour surf and pier fishing
outlook for Wrightsville Beach and Carolina Beach, North Carolina.  The
forecast combines NOAA marine conditions with seasonal fishing patterns to
rank the species most likely to bite, recommends bait and bottom rigs,
assesses fishability, and caches the most recent report.  A simple web
interface allows manual refreshes on demand.

The application exposes three endpoints:

* ``/`` – HTML dashboard showing the latest forecast with a refresh button.
* ``/api/forecast`` – Returns the current forecast in JSON format.
* ``/api/refresh`` – POST endpoint that triggers regeneration of the
  forecast.  On success the browser is redirected back to ``/``; on
  failure the cached forecast remains untouched.

Data is persisted to ``data/forecast.json``.  If a refresh fails due to a
network error or parsing problem, the server continues to serve the cached
forecast and indicates the timestamp when it was generated.

The forecast generation routine fetches the National Weather Service
marine zone forecast (KILM FZUS52) for the next 24 hours and derives
maximum sustained wind and wave height values.  Based on predetermined
thresholds the conditions are classified as Fishable, Marginal or
Not worth it.  Species rankings and rig recommendations are based on
research from official North Carolina Department of Environmental Quality
(DEQ) species profiles and other reputable sources.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
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
    TONIGHT and TUE.  This function collects the first three relevant
    segments (covering roughly the next 24 hours) and extracts numeric
    ranges for sustained winds (knots) and seas (feet).  If a range is
    specified (e.g. "10 to 15 kt") the minimum and maximum values are
    captured; otherwise both values are identical.  Returns a tuple
    ``(wind_range, wave_range)`` where each element is itself a
    ``(minimum, maximum)`` pair or ``None`` if no data could be found.
    """
    # Normalize whitespace to simplify regex searches
    cleaned = "\n".join(line.strip() for line in text.splitlines())
    # Build a regex that matches segments beginning with our target time
    # windows.  We capture a reasonable amount of text after each heading
    # until the next capitalized heading or double newline.
    pattern = re.compile(
        r"(THIS AFTERNOON|TONIGHT|TUE|TUE NIGHT)[^A-Z]{0,100}", re.MULTILINE
    )
    segments = pattern.findall(cleaned)
    # The regex above only returns the heading itself; we need the full
    # lines for each matched heading.  A simpler approach is to search the
    # page line by line and pick lines starting with our keywords.
    lines = cleaned.split("\n")
    selected: List[str] = []
    for line in lines:
        token = line.split()
        if not token:
            continue
        key = token[0]
        # Compare first word to targeted headings
        if key in {"THIS", "TONIGHT", "TUE"}:
            # Ensure the full heading is considered (THIS AFTERNOON, TUE NIGHT etc.)
            selected.append(line)
        elif key == "TUE" and len(token) > 1 and token[1] == "NIGHT":
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

    * Fishable – maximum sustained wind < 15 kt **and** maximum sea height < 3 ft.
    * Marginal – maximum sustained wind ≤ 20 kt **or** maximum sea height ≤ 5 ft.
    * Not worth it – winds > 20 kt **or** seas > 5 ft (small craft advisory
      conditions).
    """
    if wind_range is None or wave_range is None:
        return "Unknown"
    wind_max = wind_range[1]
    wave_max = wave_range[1]
    if wind_max < 15 and wave_max < 3:
        return "Fishable"
    elif wind_max <= 20 or wave_max <= 5:
        return "Marginal"
    else:
        return "Not worth it"


def build_species_ranking() -> List[Dict[str, Any]]:
    """Construct a ranked list of species with bait and rig recommendations.

    The ranking is tailored for mid-February nearshore fishing around
    Wrightsville Beach and Carolina Beach.  Species are ordered by
    likelihood of biting given seasonal patterns, official reports and
    typical winter behaviour.
    """
    return [
        {
            "rank": 1,
            "name": "Red drum (puppy drum)",
            "explanation": (
                "Winter red drum congregate in deeper marsh channels and holes. "
                "They forage along the bottom for shrimp, crabs and small fish."
            ),
            "bait": "Cut menhaden or mullet strips; fresh shrimp; live finger mullet when available",
            "rig": "Carolina/fishfinder rig with sliding egg sinker",
            "hook_size": "2/0–5/0 circle hook",
            "sinker": "2–4 oz egg sinker",
        },
        {
            "rank": 2,
            "name": "Speckled trout (spotted seatrout)",
            "explanation": (
                "Speckled trout remain active in deeper holes and backwater creeks. "
                "Live shrimp or finger mullet entice sluggish winter fish."
            ),
            "bait": "Live shrimp (most productive); finger mullet; small menhaden",
            "rig": "Popping‑cork or fishfinder rig on light leader",
            "hook_size": "1/0–2/0 circle hook",
            "sinker": "1–2 oz",  # light weight to allow natural presentation
        },
        {
            "rank": 3,
            "name": "Black drum",
            "explanation": (
                "Black drum are bottom feeders that locate prey with chin barbels. "
                "They prefer mollusks, shrimp and crab pieces and are rarely fooled by lures."
            ),
            "bait": "Cut shrimp, clams, blood worms, cut mullet, menhaden or crab pieces",
            "rig": "Double‑dropper (hi‑lo) or Carolina rig",
            "hook_size": "1/0–3/0 circle hook",
            "sinker": "2–4 oz pyramid or bank sinker",
        },
        {
            "rank": 4,
            "name": "Sheepshead",
            "explanation": (
                "Sheepshead feed around pilings and rock structures, nibbling barnacles and crustaceans. "
                "Bites are subtle; anglers fish straight down."
            ),
            "bait": "Live fiddler crabs; sand fleas; small pieces of shrimp",
            "rig": "Carolina rig with short fluorocarbon leader",
            "hook_size": "1/0–3/0 J‑style or circle hook",
            "sinker": "1–3 oz egg sinker",
        },
        {
            "rank": 5,
            "name": "Tautog",
            "explanation": (
                "Tautog (blackfish) cling to jetties and rock piles in winter and feed on crustaceans. "
                "They require strong tackle and small, strong hooks."
            ),
            "bait": "Pieces of fresh shrimp, sand fleas, fiddler or rock crabs, clams",
            "rig": "Two‑hook bottom rig or Carolina rig with heavy leader",
            "hook_size": "#6–#2 strong hook",
            "sinker": "2–4 oz bank or egg sinker",
        },
        {
            "rank": 6,
            "name": "Black sea bass",
            "explanation": (
                "Inshore black sea bass inhabit wrecks and hard bottom. "
                "They feed on crabs, shrimp and small fish; anglers bottom‑fish using strips of squid or fish."
            ),
            "bait": "Strips of squid or cut fish; shrimp",
            "rig": "Double‑dropper bottom rig on braided line",
            "hook_size": "2/0–3/0 circle hook",
            "sinker": "3–4 oz pyramid or bank sinker",
        },
        {
            "rank": 7,
            "name": "Bluefish",
            "explanation": (
                "Bluefish schools roam nearshore waters and readily hit cut bait. "
                "Sharp teeth require wire or heavy mono leaders."
            ),
            "bait": "Cut menhaden or mullet; small fish pieces",
            "rig": "Fishfinder rig with steel leader",
            "hook_size": "3/0–5/0 J‑hook",
            "sinker": "2–4 oz pyramid sinker",
        },
        {
            "rank": 8,
            "name": "Whiting (sea mullet, kingfish)",
            "explanation": (
                "Whiting patrol sandy bottoms along beaches and piers. "
                "They feed on shrimp, mole crabs and worms; two‑hook bottom rigs are ideal."
            ),
            "bait": "Fresh shrimp, mole crabs (sand fleas), bloodworms, squid",
            "rig": "Double‑dropper rig with small hooks",
            "hook_size": "#4–#2 circle hook",
            "sinker": "1–3 oz pyramid sinker",
        },
        {
            "rank": 9,
            "name": "Northern puffer (blowfish)",
            "explanation": (
                "These small, delicious fish are common in late winter. "
                "They nibble on shrimp and squid pieces presented on small hooks."
            ),
            "bait": "Small pieces of shrimp, bloodworms or squid",
            "rig": "Two‑hook bottom rig",
            "hook_size": "#6–#4 baitholder or circle hook",
            "sinker": "1–2 oz pyramid sinker",
        },
        {
            "rank": 10,
            "name": "Striped bass (rockfish)",
            "explanation": (
                "Striped bass are opportunistic predators found around piers, jetties and surf troughs. "
                "Winter surf anglers use cut or live bait on heavy tackle."
            ),
            "bait": "Cut menhaden, mullet or shad; live mullet or eels",
            "rig": "Fishfinder or chunk bait rig with heavy leader",
            "hook_size": "5/0–7/0 circle hook",
            "sinker": "3–5 oz pyramid or bank sinker",
        },
    ]


def build_rig_templates() -> List[Dict[str, str]]:
    """Define bottom rig templates for surf and pier fishing."""
    return [
        {
            "name": "Carolina/Fishfinder Rig",
            "description": (
                "A sliding egg sinker rides on the main line above a swivel. "
                "Tie 18–24 inches of 20–30 lb fluorocarbon leader and finish with a circle hook. "
                "Ideal for red drum, black drum, bluefish and striped bass."
            ),
            "mainline": "20–30 lb braid with 40 lb shock leader",
            "leader": "18–24 in 20–30 lb fluorocarbon",
            "hook": "2/0–5/0 circle hook",
            "sinker": "Sliding egg sinker 1–4 oz",
        },
        {
            "name": "Double‑Dropper (Hi‑Lo) Rig",
            "description": (
                "Two dropper loops spaced along a 30–40 lb mono leader with a weight at the bottom. "
                "Effective for whiting, black drum and sea bass when baited with shrimp, squid or worms."
            ),
            "mainline": "15–20 lb mono or braid",
            "leader": "30–40 lb mono with two dropper loops",
            "hook": "#4–3/0 circle hooks on each dropper",
            "sinker": "Pyramid or bank sinker 2–4 oz",
        },
        {
            "name": "Simple Surf Rig",
            "description": (
                "A lightweight rig for small species such as puffer and spot. "
                "Attach a short leader and small hook below a pyramid weight."
            ),
            "mainline": "10–15 lb mono or braid",
            "leader": "12–18 in 15–20 lb mono",
            "hook": "#6–#4 baitholder or circle hook",
            "sinker": "Pyramid sinker 1–2 oz",
        },
        {
            "name": "Pier Structure Rig",
            "description": (
                "Designed for fishing around pilings and rock structure. "
                "Use heavy braid and fluorocarbon leader with a short drop to minimize snags. "
                "Perfect for sheepshead and tautog."
            ),
            "mainline": "30–50 lb braid",
            "leader": "1–2 ft 30–50 lb fluorocarbon",
            "hook": "#2–3/0 J‑hook or circle hook",
            "sinker": "Egg or bank sinker 2–4 oz",
        },
    ]


def build_bait_ranking() -> List[Dict[str, str]]:
    """Compile a ranking of natural baits with notes on seasonal availability."""
    return [
        {
            "bait": "Live shrimp",
            "notes": "Top choice for speckled trout and versatile for many species; use under a popping cork or on bottom rigs."
        },
        {
            "bait": "Cut mullet",
            "notes": "Excellent for red drum and black drum; fresh cut strips release scent and stay on the hook."
        },
        {
            "bait": "Menhaden (live or cut)",
            "notes": "Prime bait for red drum, bluefish and striped bass; live menhaden offer a distinct advantage in calm conditions."
        },
        {
            "bait": "Sand fleas (mole crabs)",
            "notes": "Effective for whiting and pompano; dig in the swash zone for fresh fleas."
        },
        {
            "bait": "Squid strips",
            "notes": "Durable on the hook; attract black sea bass, whiting and puffer fish."
        },
        {
            "bait": "Fiddler crabs", 
            "notes": "Essential for sheepshead and tautog; use whole crabs on small strong hooks."
        },
        {
            "bait": "Bloodworms",
            "notes": "Popular for whiting, black drum and puffer fish; cut into small pieces for double‑dropper rigs."
        },
        {
            "bait": "Clams and crab pieces",
            "notes": "Best for black drum; larger pieces stay on the hook and deter small pickers."
        },
    ]


def generate_forecast() -> Dict[str, Any]:
    """Generate the complete fishing forecast.

    Fetches marine conditions, computes wind and wave ranges, classifies
    fishability, then assembles species rankings, rig templates and bait
    recommendations.  Raises requests.HTTPError if the marine forecast
    cannot be retrieved.
    """
    raw_text = fetch_marine_forecast()
    wind_range, wave_range = parse_conditions(raw_text)
    verdict = classify_conditions(wind_range, wave_range)
    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    # Format ranges for display
    def format_range(r: Optional[Tuple[float, float]], unit: str) -> str:
        if r is None:
            return "Unknown"
        low, high = r
        if low == high:
            return f"{low:.0f} {unit}"
        return f"{low:.0f}–{high:.0f} {unit}"

    conditions = {
        "wind": format_range(wind_range, "kt"),
        "waves": format_range(wave_range, "ft"),
        "verdict": verdict,
    }
    forecast: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "conditions": conditions,
        "species": build_species_ranking(),
        "rig_templates": build_rig_templates(),
        "bait_rankings": build_bait_ranking(),
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
            # Without any data, display a minimal error message
            return f"<h1>Error loading forecast</h1><p>{exc}</p>", 500
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
        # Log the error to console and redirect back with cached flag
        print(f"Error refreshing forecast: {exc}")
        return redirect(url_for("index", cached="true"))


if __name__ == "__main__":
    # Bind to all interfaces on the specified port
    port = int(os.environ.get("PORT", 5757))
    app.run(host="0.0.0.0", port=port)