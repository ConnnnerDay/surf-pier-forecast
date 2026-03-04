"""NOAA CO-OPS marine/environmental products and tide/current predictions."""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from services.http_client import get as http_get
from zoneinfo import ZoneInfo

from locations import get_monthly_water_temps

logger = logging.getLogger(__name__)

# Default NOAA CO-OPS station (overridden per location from locations.py)
WATER_TEMP_STATION = "8658163"

# NOAA CO-OPS wind endpoint
COOPS_WIND_URL = (
    "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    "?date=latest&station={station}"
    "&product=wind&units=english"
    "&time_zone=lst_ldt&format=json"
)
WATER_TEMP_URL = (
    "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    "?date=latest&station={station}"
    "&product=water_temperature&units=english"
    "&time_zone=lst_ldt&format=json"
)

# Generic mid-Atlantic historical water temperatures (F) by month.
# Used only as a last resort when no location is set; per-location temps
# come from locations.py via get_monthly_water_temps().
MONTHLY_AVG_WATER_TEMP_F = {
    1: 50, 2: 50, 3: 54, 4: 62, 5: 70, 6: 78,
    7: 82, 8: 83, 9: 80, 10: 72, 11: 62, 12: 54,
}


def fetch_water_temperature(station_id: str = "") -> Optional[float]:
    """Fetch the latest water temperature (F) from NOAA CO-OPS.

    Uses the free Tides & Currents API for the given station.
    Returns None on any failure.
    """
    try:
        url = WATER_TEMP_URL.format(station=station_id or WATER_TEMP_STATION)
        resp = http_get(url, endpoint="noaa.water_temperature", timeout=(3.05, 10))
        resp.raise_for_status()
        data = resp.json()
        reading = data.get("data", [{}])[0].get("v")
        if reading is not None:
            return float(reading)
    except Exception:
        pass
    return None


def fetch_latest_coops_product(station_id: str, product: str, units: str = "english") -> Optional[float]:
    """Fetch latest numeric NOAA CO-OPS product value for a station.

    Supported examples include: air_temperature, humidity, visibility,
    air_pressure, salinity, conductivity.
    """
    try:
        url = (
            "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
            f"?date=latest&station={station_id}"
            f"&product={product}&units={units}"
            "&time_zone=lst_ldt&format=json"
        )
        resp = http_get(url, endpoint=f"noaa.{product}", timeout=(3.05, 10))
        resp.raise_for_status()
        payload = resp.json()
        row = (payload.get("data") or [{}])[0]
        value = row.get("v")
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def fetch_coops_environmental_metrics(station_id: str) -> Dict[str, float]:
    """Fetch optional NOAA CO-OPS environmental products for a station."""
    metrics: Dict[str, float] = {}
    products = {
        "air_temperature": ("air_temp_f", "english"),
        "humidity": ("humidity_pct", "metric"),
        "visibility": ("visibility_mi", "english"),
        "air_pressure": ("air_pressure_mb", "metric"),
        "salinity": ("salinity_psu", "metric"),
        "conductivity": ("conductivity", "metric"),
    }
    for product, (key, units) in products.items():
        val = fetch_latest_coops_product(station_id, product, units=units)
        if val is not None:
            metrics[key] = round(val, 2)
    return metrics


def fetch_currents_predictions(station_id: str, tz_name: str = "America/New_York") -> List[Dict[str, str]]:
    """Fetch NOAA CO-OPS current prediction events (flood/ebb/slack)."""
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    today_str = now.strftime("%Y%m%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y%m%d")
    url = (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
        f"?begin_date={today_str}&end_date={tomorrow_str}"
        f"&station={station_id}"
        "&product=currents_predictions&time_zone=lst_ldt"
        "&units=english&interval=max_slack&format=json"
    )
    try:
        resp = http_get(url, endpoint="noaa.currents_predictions", timeout=(3.05, 12))
        resp.raise_for_status()
        rows = resp.json().get("cp", [])
        out: List[Dict[str, str]] = []
        for row in rows:
            raw = row.get("Time") or row.get("time")
            velocity = row.get("Velocity_Major") or row.get("Velocity") or row.get("v")
            event = row.get("Type") or row.get("type") or ""
            if not raw:
                continue
            try:
                dt = datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
                when = dt.strftime("%-I:%M %p")
            except Exception:
                when = raw
            out.append({
                "time": when,
                "event": str(event).title(),
                "speed_kt": f"{float(velocity):.2f}" if velocity not in (None, "") else "0.00",
            })
        return out
    except Exception:
        return []


def fetch_currents_observation(station_id: str, tz_name: str = "America/New_York") -> Optional[Dict[str, str]]:
    """Fetch latest measured current speed/direction from NOAA CO-OPS."""
    tz = ZoneInfo(tz_name)
    url = (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
        f"?date=latest&station={station_id}"
        "&product=currents&time_zone=lst_ldt"
        "&units=english&format=json"
    )
    try:
        resp = http_get(url, endpoint="noaa.currents", timeout=(3.05, 12))
        resp.raise_for_status()
        rows = resp.json().get("data", [])
        if not rows:
            return None
        row = rows[0]
        raw_time = row.get("t") or row.get("Time") or row.get("time")
        speed = row.get("s") or row.get("Speed") or row.get("v") or row.get("Velocity")
        direction = row.get("d") or row.get("Direction") or row.get("dir")

        when = "Now"
        if raw_time:
            try:
                dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
                when = dt.strftime("%-I:%M %p")
            except Exception:
                when = str(raw_time)

        speed_kt = f"{float(speed):.2f}" if speed not in (None, "") else "0.00"
        return {
            "time": when,
            "event": "Observed",
            "speed_kt": speed_kt,
            "direction": str(direction) if direction not in (None, "") else "",
        }
    except Exception:
        return None


def get_water_temp(
    month: int,
    location: Optional[Dict[str, Any]] = None,
    sources_used: Optional[List[str]] = None,
    fallbacks_triggered: Optional[List[str]] = None,
) -> Tuple[float, bool]:
    """Return (water_temp_f, is_live).

    Tries the live NOAA reading first.  Falls back to the historical
    monthly average when the API is unreachable.
    """
    station_id = (location or {}).get("coops_station", WATER_TEMP_STATION)
    live = fetch_water_temperature(station_id)
    if live is not None:
        if sources_used is not None:
            sources_used.append("NOAA CO-OPS water temperature")
        return live, True
    if location:
        temps = get_monthly_water_temps(location)
        if fallbacks_triggered is not None:
            fallbacks_triggered.append("monthly_location_water_temp")
        return float(temps[month]), False
    if fallbacks_triggered is not None:
        fallbacks_triggered.append("monthly_regional_water_temp")
    return float(MONTHLY_AVG_WATER_TEMP_F[month]), False


# -- Source 3: NOAA CO-OPS wind data (same station as water temp) -----------

def _try_coops_wind(station_id: str = "") -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]], Optional[str]]:
    """Fetch wind from a NOAA CO-OPS station.

    This is the same station we use for water temperature, so if water temp
    loads successfully this source is very likely to work too.
    Returns wind data only (no wave data from this source).
    """
    url = COOPS_WIND_URL.format(station=station_id or WATER_TEMP_STATION)
    resp = http_get(url, endpoint="noaa.coops_wind", timeout=(3.05, 10))
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


def fetch_tide_predictions(
    station_id: str,
    tz_name: str = "America/New_York",
) -> List[Dict[str, str]]:
    """Fetch today's tide predictions from NOAA CO-OPS.

    Returns a list of dicts like:
        [{"time": "6:32 AM", "type": "High", "height_ft": "5.2"}, ...]
    Returns an empty list on any error.
    """
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    today_str = now.strftime("%Y%m%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y%m%d")
    url = (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
        f"?begin_date={today_str}&end_date={tomorrow_str}"
        f"&station={station_id}"
        "&product=predictions&datum=MLLW&units=english"
        "&time_zone=lst_ldt&format=json&interval=hilo"
    )
    try:
        resp = http_get(url, endpoint="noaa.tide_predictions", timeout=(3.05, 12))
        resp.raise_for_status()
        data = resp.json()
        predictions = data.get("predictions", [])
        tides = []
        for p in predictions:
            raw_time = p.get("t", "")
            height = p.get("v", "0")
            tide_type = "High" if p.get("type") == "H" else "Low"
            dt = None
            try:
                dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M")
                dt = dt.replace(tzinfo=tz)
                time_str = dt.strftime("%-I:%M %p")
            except Exception:
                time_str = raw_time
            tides.append({
                "time": time_str,
                "type": tide_type,
                "height_ft": f"{float(height):.1f}",
                "hour": dt.hour + dt.minute / 60 if isinstance(dt, datetime) else 12.0,
                "height_num": float(height),
                "date_str": dt.strftime("%Y%m%d") if isinstance(dt, datetime) else today_str,
            })
        return tides
    except Exception:
        return []



def build_tide_chart_svg(tides: List[Dict[str, Any]]) -> str:
    """Build an SVG path string for a smooth tide curve.

    Returns a dict with 'path' (SVG path d attribute), 'points' (list of
    {cx, cy, label, height} for the markers), 'viewBox', and 'fill_path'.
    Only considers tides within a 24-hour window.
    """
    if len(tides) < 2:
        return ""

    # Chart dimensions
    W, H = 600, 140
    PAD_X, PAD_TOP, PAD_BOT = 50, 20, 30

    # Filter to reasonable 24h range and extract numeric data
    pts = []
    for t in tides:
        h = t.get("hour", 12.0)
        ht = t.get("height_num", 0.0)
        if 0 <= h <= 30:  # Allow some overflow for next-day tides
            pts.append((h, ht, t["type"], t["time"], t["height_ft"]))
    if len(pts) < 2:
        return ""

    # Compute bounds
    min_h = min(p[1] for p in pts)
    max_h = max(p[1] for p in pts)
    h_range = max_h - min_h if max_h != min_h else 1.0
    min_hour = min(p[0] for p in pts)
    max_hour = max(p[0] for p in pts)
    hour_range = max_hour - min_hour if max_hour != min_hour else 24.0

    def to_x(hr: float) -> float:
        return PAD_X + (hr - min_hour) / hour_range * (W - 2 * PAD_X)

    def to_y(ht: float) -> float:
        return PAD_TOP + (1 - (ht - min_h) / h_range) * (H - PAD_TOP - PAD_BOT)

    # Build smooth curve using cubic bezier through points
    coords = [(to_x(p[0]), to_y(p[1])) for p in pts]
    path_parts = [f"M{coords[0][0]:.1f},{coords[0][1]:.1f}"]
    for i in range(1, len(coords)):
        # Simple smooth curve: control points at 1/3 intervals
        x0, y0 = coords[i - 1]
        x1, y1 = coords[i]
        cx1 = x0 + (x1 - x0) * 0.4
        cx2 = x1 - (x1 - x0) * 0.4
        path_parts.append(f"C{cx1:.1f},{y0:.1f} {cx2:.1f},{y1:.1f} {x1:.1f},{y1:.1f}")

    path_d = " ".join(path_parts)

    # Fill path (close to bottom)
    fill_d = path_d + f" L{coords[-1][0]:.1f},{H - PAD_BOT:.1f} L{coords[0][0]:.1f},{H - PAD_BOT:.1f} Z"

    # Build point markers
    markers = []
    for i, p in enumerate(pts):
        markers.append({
            "cx": f"{coords[i][0]:.1f}",
            "cy": f"{coords[i][1]:.1f}",
            "type": p[2],
            "time": p[3],
            "height": p[4],
        })

    import json as _json
    return _json.dumps({
        "viewBox": f"0 0 {W} {H}",
        "path": path_d,
        "fill_path": fill_d,
        "markers": markers,
        "width": W,
        "height": H,
    })
