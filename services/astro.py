"""Sunrise / sunset and solunar calculations (pure math, no API)."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Default coordinates (overridden per location; only used when no location set)
_LAT = 34.2104
_LNG = -77.7964


def _sun_times(
    dt: datetime,
    lat: float = 0,
    lng: float = 0,
    tz_name: str = "America/New_York",
) -> Tuple[datetime, datetime]:
    """Compute approximate sunrise and sunset for a coastal location.

    Uses the simplified NOAA algorithm based on the day-of-year, latitude,
    and an approximate equation of time.  Returns (sunrise, sunset) as
    timezone-aware datetimes.  Accuracy is within a few minutes -- good
    enough for fishing planning.
    """
    if lat == 0:
        lat = _LAT
    if lng == 0:
        lng = _LNG
    tz = ZoneInfo(tz_name)
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

    lat_rad = math.radians(lat)

    # Hour angle at sunrise/sunset (degrees)
    cos_ha = (
        math.cos(math.radians(90.833)) / (math.cos(lat_rad) * math.cos(decl))
        - math.tan(lat_rad) * math.tan(decl)
    )
    # Clamp for polar regions
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha = math.degrees(math.acos(cos_ha))

    # Sunrise and sunset in minutes from midnight UTC
    sunrise_utc = 720 - 4 * (lng + ha) - eqtime
    sunset_utc = 720 - 4 * (lng - ha) - eqtime

    base = datetime(dt.year, dt.month, dt.day, tzinfo=ZoneInfo("UTC"))
    sunrise = base + timedelta(minutes=sunrise_utc)
    sunset = base + timedelta(minutes=sunset_utc)

    return sunrise.astimezone(tz), sunset.astimezone(tz)


def _moon_phase(dt: datetime) -> float:
    """Return the moon phase as a fraction (0.0 = new, 0.5 = full)."""
    # Reference new moon: 2000-01-06 18:14 UTC
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=ZoneInfo("UTC"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    diff = (dt - ref).total_seconds()
    synodic = 29.53058867  # days
    phase = (diff / (synodic * 86400)) % 1.0
    return phase


def _moon_transit_hours(dt: datetime, lng: float) -> Tuple[float, float]:
    """Approximate moon overhead and underfoot times (local hour of day).

    Returns (overhead_hour, underfoot_hour).  These are rough estimates
    based on the moon's position relative to the observer's longitude and
    the moon's orbital phase.
    """
    phase = _moon_phase(dt)
    # Moon transit relative to solar noon advances ~50 min/day through the lunar cycle
    # At new moon, moon transits at ~12:00 (noon) local solar time
    # At full moon, moon transits at ~00:00 (midnight) local solar time
    transit_solar_hr = (phase * 24.0) % 24.0  # overhead time in solar hours
    # Convert solar time to approximate clock time (simple longitude offset)
    # Standard timezone offset from UTC
    tz = dt.tzinfo
    if tz:
        utc_offset = dt.utcoffset().total_seconds() / 3600
    else:
        utc_offset = -5  # default EST
    solar_offset = (lng / 15.0) - utc_offset
    overhead = (12.0 + transit_solar_hr - solar_offset) % 24.0
    underfoot = (overhead + 12.0) % 24.0
    return overhead, underfoot


def compute_solunar_times(
    dt: datetime,
    lat: float,
    lng: float,
    tz_name: str = "America/New_York",
) -> Dict[str, Any]:
    """Compute solunar major and minor fishing periods for the given day.

    Returns a dict with:
        major_periods: list of (start_time_str, end_time_str) ~2hr windows
        minor_periods: list of (start_time_str, end_time_str) ~1hr windows
        moon_phase: str description (New, Waxing, Full, Waning)
        rating: str (Excellent / Good / Fair) based on moon phase
    """
    tz = ZoneInfo(tz_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)

    phase_frac = _moon_phase(dt)
    overhead, underfoot = _moon_transit_hours(dt, lng)

    def fmt_time(hour: float) -> str:
        h = int(hour) % 24
        m = int((hour - int(hour)) * 60)
        period = "AM" if h < 12 else "PM"
        display_h = h if h <= 12 else h - 12
        if display_h == 0:
            display_h = 12
        return f"{display_h}:{m:02d} {period}"

    def time_window(center: float, half_width: float) -> Tuple[str, str]:
        start = (center - half_width) % 24.0
        end = (center + half_width) % 24.0
        return fmt_time(start), fmt_time(end)

    # Major periods: ~1 hour on each side of moon overhead / underfoot
    major_periods = [
        time_window(overhead, 1.0),
        time_window(underfoot, 1.0),
    ]

    # Minor periods: ~30 min on each side of moonrise / moonset
    # Moonrise/moonset are roughly 6 hours from transit
    moonrise = (overhead - 6.2) % 24.0
    moonset = (overhead + 6.2) % 24.0
    minor_periods = [
        time_window(moonrise, 0.5),
        time_window(moonset, 0.5),
    ]

    # Sort by start time
    def sort_key(p: Tuple[str, str]) -> float:
        parts = p[0].replace(":", " ").replace("AM", "").replace("PM", "").split()
        h = int(parts[0])
        m = int(parts[1])
        is_pm = "PM" in p[0]
        if h == 12:
            h = 0 if not is_pm else 12
        elif is_pm:
            h += 12
        return h + m / 60.0

    major_periods.sort(key=sort_key)
    minor_periods.sort(key=sort_key)

    # Moon phase name and fishing rating
    if phase_frac < 0.05 or phase_frac > 0.95:
        phase_name = "New Moon"
        rating = "Excellent"
    elif 0.45 < phase_frac < 0.55:
        phase_name = "Full Moon"
        rating = "Excellent"
    elif phase_frac < 0.25:
        phase_name = "Waxing Crescent"
        rating = "Fair"
    elif phase_frac < 0.30:
        phase_name = "First Quarter"
        rating = "Good"
    elif phase_frac < 0.45:
        phase_name = "Waxing Gibbous"
        rating = "Good"
    elif phase_frac < 0.75:
        phase_name = "Waning Gibbous"
        rating = "Good"
    elif phase_frac < 0.80:
        phase_name = "Last Quarter"
        rating = "Good"
    else:
        phase_name = "Waning Crescent"
        rating = "Fair"

    return {
        "major_periods": major_periods,
        "minor_periods": minor_periods,
        "moon_phase": phase_name,
        "rating": rating,
    }
