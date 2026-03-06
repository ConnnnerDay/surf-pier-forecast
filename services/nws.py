"""NWS marine forecast parsing and weather data."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from services.http_client import get as http_get


logger = logging.getLogger(__name__)

# Default NWS marine zone (overridden per location from locations.py)
NWS_MARINE_ZONE = "AMZ158"

_MPH_TO_KNOTS = 0.868976

# Default coordinates (overridden per location)
_LAT = 34.2104
_LNG = -77.7964

_NWS_HEADERS = {
    "User-Agent": "(SurfPierForecast, github.com/ConnnnerDay/surf-pier-forecast)",
    "Accept": "application/ld+json",
}


def _try_nws_forecast(
    zone: str = "",
) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]], Optional[str]]:
    """NWS marine zone forecast -- provides 24-hour forecast ranges."""
    zone = zone or NWS_MARINE_ZONE
    url = f"https://api.weather.gov/zones/forecast/{zone}/forecast"
    response = http_get(url, endpoint="nws.zone_forecast", headers=_NWS_HEADERS, timeout=(3.05, 15))
    response.raise_for_status()
    data = response.json()
    periods = data["properties"]["periods"]
    return parse_conditions(periods)


def _try_nws_gridpoint(
    lat: float = 0, lng: float = 0,
) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]], Optional[str]]:
    """NWS grid forecast for the nearest land point to a location.

    Provides wind speed and direction from the standard forecast.  No wave
    data (land point), but fills the wind gap if marine sources are down.
    """
    if lat == 0:
        lat = _LAT
    if lng == 0:
        lng = _LNG
    # First get the gridpoint info
    pts = http_get(
        f"https://api.weather.gov/points/{lat},{lng}",
        endpoint="nws.points",
        headers=_NWS_HEADERS, timeout=(3.05, 10),
    )
    pts.raise_for_status()
    forecast_url = pts.json()["properties"]["forecast"]

    # Then get the forecast
    fc = http_get(forecast_url, endpoint="nws.forecast", headers=_NWS_HEADERS, timeout=(3.05, 10))
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


def fetch_weather_alerts(lat: float, lng: float) -> List[Dict[str, str]]:
    """Fetch active weather alerts from NWS for a lat/lng.

    Returns a list of dicts with: event, severity, headline, description.
    Only includes marine and weather-relevant alerts.
    """
    try:
        resp = http_get(
            f"https://api.weather.gov/alerts/active?point={lat},{lng}",
            endpoint="nws.alerts",
            headers=_NWS_HEADERS, timeout=(3.05, 10),
        )
        resp.raise_for_status()
        features = resp.json().get("@graph", [])
        alerts = []
        for f in features[:5]:
            event = f.get("event", "")
            severity = f.get("severity", "")
            headline = f.get("headline", "")
            description = (f.get("description", "") or "")[:300]
            if event:
                alerts.append({
                    "event": event,
                    "severity": severity,
                    "headline": headline,
                    "description": description,
                })
        return alerts
    except Exception:
        logger.debug("Weather alerts unavailable", exc_info=True)
        return []



def fetch_state_alerts(state_code: str) -> List[Dict[str, str]]:
    """Fetch active alerts for an entire state via /alerts/active?area=XX."""
    if not state_code:
        return []
    try:
        resp = http_get(
            f"https://api.weather.gov/alerts/active?area={state_code.upper()}",
            endpoint="nws.alerts_state",
            headers=_NWS_HEADERS,
            timeout=(3.05, 10),
        )
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features", []) or data.get("@graph", [])
        alerts: List[Dict[str, str]] = []
        for feat in features[:10]:
            props = feat.get("properties", feat)
            event = props.get("event", "")
            if not event:
                continue
            alerts.append({
                "event": event,
                "severity": props.get("severity", ""),
                "headline": props.get("headline", ""),
                "description": (props.get("description", "") or "")[:300],
            })
        return alerts
    except Exception:
        logger.debug("State alerts unavailable", exc_info=True)
        return []

def fetch_current_weather(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """Fetch current weather observations from NWS.

    Returns a dict with: air_temp_f, humidity, description, sky, wind_chill_f.
    Returns None on failure.
    """
    try:
        # Get nearest observation station from the NWS points API
        pts = http_get(
            f"https://api.weather.gov/points/{lat},{lng}",
            endpoint="nws.points",
            headers=_NWS_HEADERS, timeout=(3.05, 10),
        )
        pts.raise_for_status()
        obs_url = pts.json()["properties"].get("observationStations", "")
        if not obs_url:
            return None

        # Get latest observation from nearest station
        stations = http_get(obs_url, endpoint="nws.observation_stations", headers=_NWS_HEADERS, timeout=(3.05, 10))
        stations.raise_for_status()
        station_list = stations.json().get("observationStations", [])
        if not station_list:
            return None

        station_id = station_list[0].rstrip("/").split("/")[-1]
        obs = http_get(
            f"https://api.weather.gov/stations/{station_id}/observations/latest",
            endpoint="nws.observation_latest",
            headers=_NWS_HEADERS, timeout=(3.05, 10),
        )
        obs.raise_for_status()
        props = obs.json().get("properties", {})

        # Extract data
        temp_c = props.get("temperature", {}).get("value")
        humidity = props.get("relativeHumidity", {}).get("value")
        description = props.get("textDescription", "")
        wind_chill_c = props.get("windChill", {}).get("value")

        result: Dict[str, Any] = {"description": description or ""}

        if temp_c is not None:
            result["air_temp_f"] = round(temp_c * 9 / 5 + 32, 1)
        if humidity is not None:
            result["humidity"] = round(humidity, 0)
        if wind_chill_c is not None:
            result["wind_chill_f"] = round(wind_chill_c * 9 / 5 + 32, 1)

        # Compute feels-like / heat index for warm weather
        if temp_c is not None and humidity is not None:
            temp_f = result["air_temp_f"]
            if temp_f >= 80 and humidity >= 40:
                # Simplified heat index
                hi = (-42.379 + 2.04901523 * temp_f
                      + 10.14333127 * humidity
                      - 0.22475541 * temp_f * humidity
                      - 0.00683783 * temp_f ** 2
                      - 0.05481717 * humidity ** 2
                      + 0.00122874 * temp_f ** 2 * humidity
                      + 0.00085282 * temp_f * humidity ** 2
                      - 0.00000199 * temp_f ** 2 * humidity ** 2)
                result["feels_like_f"] = round(hi, 0)

        return result if "air_temp_f" in result else None
    except Exception:
        logger.debug("Current weather unavailable", exc_info=True)
        return None


def _fetch_nws_extended(lat: float, lng: float, zone: str = "") -> List[Dict[str, str]]:
    """Fetch the NWS 7-day forecast for a lat/lng.

    Tries the NWS gridpoint forecast first (works for land coordinates).  For
    pier / offshore coordinates where the gridpoint API returns an error, falls
    back to the NWS marine zone forecast identified by *zone* (e.g. "AMZ158").
    Marine zone periods carry wind speed in knots inside ``detailedForecast``
    text rather than in a separate ``windSpeed`` field.

    Returns a list of period dicts with name, detailedForecast, etc.
    Returns an empty list on failure.
    """
    try:
        pts = http_get(
            f"https://api.weather.gov/points/{lat},{lng}",
            endpoint="nws.points",
            headers=_NWS_HEADERS, timeout=(3.05, 10),
        )
        pts.raise_for_status()
        forecast_url = pts.json()["properties"]["forecast"]
        fc = http_get(forecast_url, endpoint="nws.forecast", headers=_NWS_HEADERS, timeout=(3.05, 10))
        fc.raise_for_status()
        return fc.json()["properties"]["periods"]
    except Exception:
        logger.debug("NWS gridpoint forecast unavailable, trying marine zone", exc_info=True)

    if not zone:
        return []

    try:
        url = f"https://api.weather.gov/zones/forecast/{zone}/forecast"
        fc = http_get(url, endpoint="nws.zone_forecast", headers=_NWS_HEADERS, timeout=(3.05, 15))
        fc.raise_for_status()
        return fc.json()["properties"]["periods"]
    except Exception:
        logger.debug("NWS marine zone forecast unavailable", exc_info=True)
        return []
