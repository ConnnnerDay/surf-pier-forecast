"""NDBC buoy real-time observations (wind, waves, pressure)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from services.http_client import get as http_get

logger = logging.getLogger(__name__)

# Default NDBC buoy stations (overridden per location from locations.py)
NDBC_STATIONS = [
    ("41110", "Masonboro Inlet"),
    ("41037", "Offshore Buoy"),
]

_MS_TO_KNOTS = 1.94384
_M_TO_FEET = 3.28084


def _deg_to_compass(deg: float) -> str:
    """Convert wind direction in degrees to a compass abbreviation."""
    _DEG_TO_DIR = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    ]
    idx = round(deg / 22.5) % 16
    return _DEG_TO_DIR[idx]


def _try_ndbc_station(
    station_id: str,
) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]], Optional[str]]:
    """Fetch real-time wind/wave observations from a single NDBC buoy."""
    url = f"https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"
    resp = http_get(url, endpoint="ndbc.realtime", headers={"User-Agent": "SurfPierForecast/1.0"}, timeout=(3.05, 15))
    resp.raise_for_status()

    lines = resp.text.strip().split("\n")
    if len(lines) < 3:
        return None, None, None

    header = lines[0].replace("#", "").split()
    col = {name: idx for idx, name in enumerate(header)}

    _MISSING = {"MM", "99.0", "99.00", "999", "999.0"}
    wind_range = None
    wave_range = None
    wind_dir = None

    for line in lines[2:12]:  # Check up to 10 recent observations
        fields = line.split()
        if len(fields) < len(header):
            continue

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


def fetch_barometric_pressure(
    location: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch barometric pressure from NDBC buoy or NOAA CO-OPS station.

    Returns a dict with:
        pressure_mb: float (millibars / hPa)
        pressure_inhg: float (inches of mercury)
        trend: str ("Rising", "Falling", "Steady")
        fishing_impact: str (description of fishing impact)
    Returns None on failure.
    """
    ndbc_list = (location or {}).get("ndbc_stations", [s[0] for s in NDBC_STATIONS])

    # Try NDBC buoys first for pressure
    for station_id in ndbc_list[:3]:
        try:
            url = f"https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"
            resp = http_get(url, endpoint="ndbc.pressure", headers={"User-Agent": "SurfPierForecast/1.0"}, timeout=(3.05, 10))
            resp.raise_for_status()
            lines = resp.text.strip().split("\n")
            if len(lines) < 3:
                continue

            header = lines[0].replace("#", "").split()
            col = {name: idx for idx, name in enumerate(header)}
            if "PRES" not in col:
                continue

            _MISSING = {"MM", "99.0", "99.00", "999", "999.0", "9999.0"}

            # Get last 2-3 readings for trend
            pressures = []
            for line in lines[2:12]:
                fields = line.split()
                if len(fields) < len(header):
                    continue
                pres_raw = fields[col["PRES"]]
                if pres_raw not in _MISSING:
                    pressures.append(float(pres_raw))
                if len(pressures) >= 3:
                    break

            if not pressures:
                continue

            current_mb = pressures[0]
            current_inhg = current_mb * 0.02953

            # Trend from recent readings
            trend = "Steady"
            if len(pressures) >= 2:
                diff = pressures[0] - pressures[-1]
                if diff > 1.0:
                    trend = "Rising"
                elif diff < -1.0:
                    trend = "Falling"

            # Fishing impact assessment
            if current_mb >= 1020:
                if trend == "Rising":
                    impact = "Excellent — high stable pressure, fish feed actively"
                elif trend == "Falling":
                    impact = "Good — fish sense dropping pressure and feed heavily"
                else:
                    impact = "Good — stable high pressure, consistent bites"
            elif current_mb >= 1010:
                if trend == "Falling":
                    impact = "Very good — dropping pressure triggers feeding frenzy"
                elif trend == "Rising":
                    impact = "Good — rising pressure after a front, fish resuming"
                else:
                    impact = "Average — normal pressure, standard activity"
            else:
                if trend == "Falling":
                    impact = "Poor — very low falling pressure, fish go deep"
                elif trend == "Rising":
                    impact = "Improving — pressure recovering, fish starting to feed"
                else:
                    impact = "Below average — low pressure suppresses feeding"

            return {
                "pressure_mb": round(current_mb, 1),
                "pressure_inhg": round(current_inhg, 2),
                "trend": trend,
                "fishing_impact": impact,
            }
        except Exception:
            continue

    return None
