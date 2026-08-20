"""Astronomy provider adapter (sprint 16).

Ports sunrise/sunset, twilight, lunar, and solunar calculations from the
legacy `services/astro.py` — pure math (NOAA's simplified solar-position
algorithm plus a synodic-month lunar-phase approximation), no network
calls, unlike sprints 13-15's NWS/NOAA CO-OPS/NDBC adapters. Grouped with
them in docs/CANONICAL_ROADMAP.md's sprint ledger because it's the last
of the four data sources `domain/forecast.py` fans out to.

Adaptations from the legacy module (per docs/R1_RECONCILIATION_AUDIT.md,
an adapt, not a verbatim carry-over):

- Returns typed, timezone-aware `datetime`s instead of pre-formatted
  12-hour strings (`"6:32 AM"`). The legacy module baked in formatting
  because Flask templates consumed the dicts directly; a typed API
  should hand back real datetimes and let the presentation layer (the
  Next.js BFF) format them.
- Moon phase name and solunar rating are enums (`MoonPhaseName`,
  `SolunarRating`) instead of loose strings, with the same thresholds.
- `_sun_times`'s "lat/lng == 0 means unset, substitute the default
  location" sentinel is dropped: (0, 0) is a real point off the Gulf of
  Guinea, not a safe "unset" marker, and treating it as one was a latent
  bug. Callers now pass real coordinates; there is no silent fallback.
  `_sun_times` and `_sun_event_time` (a generic version parameterized by
  the same-formula "zenith angle") were separate implementations in the
  legacy module; here sunrise/sunset is just `_sun_event_time` at the
  standard 90.833° zenith, removing the duplicate formula.
- `_moon_transit_hours`'s "no tzinfo on `dt`, guess UTC-5" fallback is
  dropped: every public function in this module normalizes its `dt`
  argument to timezone-aware exactly once, so internal helpers can
  assume it always is.

Known approximation carried over unchanged: moonrise/moonset and the
solunar major/minor windows are computed as an hour-of-day wrapped
`mod 24` and stamped onto the same calendar date as the input `dt` —
same as the legacy module. A window that crosses midnight (e.g. a
moonset the legacy math places at "26:00") is not rolled onto the next
calendar day; it wraps back to that day's 02:00. This is an existing
approximation in the solunar math itself, not something this port
changes — accurate day-boundary handling would require a materially
different algorithm and is out of scope here.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.infra.timezones import safe_zone as _safe_zone

_SYNODIC_MONTH_DAYS = 29.53058867
_MOON_RISE_SET_OFFSET_HOURS = 6.2
_EARTH_MOON_DISTANCE_KM = 384400
_MOON_ANOMALY_COEFFICIENT_KM = 20905

_CIVIL_ZENITH_DEG = 96.0
_NAUTICAL_ZENITH_DEG = 102.0
_ASTRONOMICAL_ZENITH_DEG = 108.0
_SUNRISE_SUNSET_ZENITH_DEG = 90.833


class MoonPhaseName(str, Enum):
    NEW = "New Moon"
    WAXING_CRESCENT = "Waxing Crescent"
    FIRST_QUARTER = "First Quarter"
    WAXING_GIBBOUS = "Waxing Gibbous"
    FULL = "Full Moon"
    WANING_GIBBOUS = "Waning Gibbous"
    LAST_QUARTER = "Last Quarter"
    WANING_CRESCENT = "Waning Crescent"


class SolunarRating(str, Enum):
    EXCELLENT = "Excellent"
    GOOD = "Good"
    FAIR = "Fair"
    POOR = "Poor"


class SunTimes(BaseModel):
    sunrise: datetime
    sunset: datetime


class TwilightTimes(BaseModel):
    civil_dawn: datetime
    civil_dusk: datetime
    nautical_dawn: datetime
    nautical_dusk: datetime
    astronomical_dawn: datetime
    astronomical_dusk: datetime
    sunrise: datetime
    sunset: datetime


class LunarDetails(BaseModel):
    moonrise: datetime
    moonset: datetime
    age_days: float
    distance_km: float


class SolunarPeriod(BaseModel):
    start: datetime
    end: datetime


class SolunarTimes(BaseModel):
    major_periods: list[SolunarPeriod]
    minor_periods: list[SolunarPeriod]
    moon_phase: MoonPhaseName
    illumination_pct: float
    rating: SolunarRating


def _ensure_tz(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=tz)


def _eqtime(gamma: float) -> float:
    """Equation of time correction in minutes (NOAA algorithm)."""
    return 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )


def _solar_decl(gamma: float) -> float:
    """Solar declination in radians (NOAA algorithm)."""
    return (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )


def _sun_event_time(
    dt: datetime,
    lat: float,
    lng: float,
    tz: ZoneInfo,
    zenith_deg: float,
    rising: bool,
) -> datetime:
    """Compute a sun event (sunrise/sunset, or a twilight boundary at a
    non-standard zenith angle) for the given day and coordinates.
    """
    n = dt.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)
    eqtime = _eqtime(gamma)
    decl = _solar_decl(gamma)
    lat_rad = math.radians(lat)
    cos_ha = math.cos(math.radians(zenith_deg)) / (
        math.cos(lat_rad) * math.cos(decl)
    ) - math.tan(lat_rad) * math.tan(decl)
    cos_ha = max(-1.0, min(1.0, cos_ha))  # clamp for polar regions
    ha = math.degrees(math.acos(cos_ha))
    event_utc_minutes = 720 - 4 * (lng + ha if rising else lng - ha) - eqtime
    base = datetime(dt.year, dt.month, dt.day, tzinfo=ZoneInfo("UTC"))
    return (base + timedelta(minutes=event_utc_minutes)).astimezone(dt.tzinfo)


def _moon_phase(dt: datetime) -> float:
    """Return the moon phase as a fraction (0.0 = new, 0.5 = full)."""
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=ZoneInfo("UTC"))
    diff = (dt - ref).total_seconds()
    return (diff / (_SYNODIC_MONTH_DAYS * 86400)) % 1.0


def _moon_transit_hours(dt: datetime, lng: float) -> tuple[float, float]:
    """Approximate moon overhead and underfoot times (local hour of day).

    Returns (overhead_hour, underfoot_hour): rough estimates from the
    moon's position relative to the observer's longitude and orbital
    phase. At new moon the moon transits at ~noon local solar time; at
    full moon it transits at ~midnight.
    """
    phase = _moon_phase(dt)
    transit_solar_hr = (phase * 24.0) % 24.0
    offset = dt.utcoffset()
    utc_offset_hours = offset.total_seconds() / 3600 if offset is not None else 0.0
    solar_offset = (lng / 15.0) - utc_offset_hours
    overhead = (12.0 + transit_solar_hr - solar_offset) % 24.0
    underfoot = (overhead + 12.0) % 24.0
    return overhead, underfoot


def _hour_to_datetime(day: date, hour: float, tz: ZoneInfo) -> datetime:
    """Stamp an hour-of-day float (wrapped mod 24) onto *day* — see the
    module docstring's note on the day-boundary approximation this
    carries over from the legacy module.
    """
    wrapped = hour % 24.0
    h = int(wrapped)
    m = round((wrapped - h) * 60)
    if m == 60:
        m = 0
        h = (h + 1) % 24
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz)


def compute_sun_times(dt: datetime, lat: float, lng: float, tz_name: str) -> SunTimes:
    tz = _safe_zone(tz_name)
    dt = _ensure_tz(dt, tz)
    sunrise = _sun_event_time(
        dt, lat, lng, tz, zenith_deg=_SUNRISE_SUNSET_ZENITH_DEG, rising=True
    )
    sunset = _sun_event_time(
        dt, lat, lng, tz, zenith_deg=_SUNRISE_SUNSET_ZENITH_DEG, rising=False
    )
    return SunTimes(sunrise=sunrise, sunset=sunset)


def compute_twilight_times(
    dt: datetime, lat: float, lng: float, tz_name: str
) -> TwilightTimes:
    """Compute civil/nautical/astronomical dawn+dusk and sunrise/sunset."""
    tz = _safe_zone(tz_name)
    dt = _ensure_tz(dt, tz)

    def event(zenith_deg: float, rising: bool) -> datetime:
        return _sun_event_time(dt, lat, lng, tz, zenith_deg=zenith_deg, rising=rising)

    return TwilightTimes(
        civil_dawn=event(_CIVIL_ZENITH_DEG, rising=True),
        civil_dusk=event(_CIVIL_ZENITH_DEG, rising=False),
        nautical_dawn=event(_NAUTICAL_ZENITH_DEG, rising=True),
        nautical_dusk=event(_NAUTICAL_ZENITH_DEG, rising=False),
        astronomical_dawn=event(_ASTRONOMICAL_ZENITH_DEG, rising=True),
        astronomical_dusk=event(_ASTRONOMICAL_ZENITH_DEG, rising=False),
        sunrise=event(_SUNRISE_SUNSET_ZENITH_DEG, rising=True),
        sunset=event(_SUNRISE_SUNSET_ZENITH_DEG, rising=False),
    )


def compute_lunar_details(dt: datetime, lng: float, tz_name: str) -> LunarDetails:
    """Compute moonrise/moonset plus phase age and geocentric distance."""
    tz = _safe_zone(tz_name)
    dt = _ensure_tz(dt, tz)

    phase_frac = _moon_phase(dt)
    age_days = phase_frac * _SYNODIC_MONTH_DAYS
    overhead, _underfoot = _moon_transit_hours(dt, lng)
    moonrise_h = overhead - _MOON_RISE_SET_OFFSET_HOURS
    moonset_h = overhead + _MOON_RISE_SET_OFFSET_HOURS

    # Approximate geocentric moon distance in km with a simple anomaly model.
    anomaly = 2 * math.pi * phase_frac
    distance_km = _EARTH_MOON_DISTANCE_KM - _MOON_ANOMALY_COEFFICIENT_KM * math.cos(
        anomaly
    )

    day = dt.date()
    return LunarDetails(
        moonrise=_hour_to_datetime(day, moonrise_h, tz),
        moonset=_hour_to_datetime(day, moonset_h, tz),
        age_days=round(age_days, 1),
        distance_km=round(distance_km),
    )


def _moon_phase_name_and_rating(
    phase_frac: float,
) -> tuple[MoonPhaseName, SolunarRating]:
    if phase_frac < 0.05 or phase_frac > 0.95:
        return MoonPhaseName.NEW, SolunarRating.EXCELLENT
    if 0.45 < phase_frac < 0.55:
        return MoonPhaseName.FULL, SolunarRating.EXCELLENT
    if phase_frac < 0.22:
        return MoonPhaseName.WAXING_CRESCENT, SolunarRating.POOR
    if phase_frac < 0.25:
        return MoonPhaseName.WAXING_CRESCENT, SolunarRating.FAIR
    if phase_frac < 0.30:
        return MoonPhaseName.FIRST_QUARTER, SolunarRating.GOOD
    if phase_frac < 0.45:
        return MoonPhaseName.WAXING_GIBBOUS, SolunarRating.GOOD
    if phase_frac < 0.75:
        return MoonPhaseName.WANING_GIBBOUS, SolunarRating.GOOD
    if phase_frac < 0.80:
        return MoonPhaseName.LAST_QUARTER, SolunarRating.GOOD
    if phase_frac < 0.88:
        return MoonPhaseName.WANING_CRESCENT, SolunarRating.FAIR
    return MoonPhaseName.WANING_CRESCENT, SolunarRating.POOR


def compute_solunar_times(
    dt: datetime, lat: float, lng: float, tz_name: str
) -> SolunarTimes:
    """Compute solunar major (~2hr, centered on moon overhead/underfoot)
    and minor (~1hr, centered on moonrise/moonset) fishing periods.
    """
    tz = _safe_zone(tz_name)
    dt = _ensure_tz(dt, tz)
    day = dt.date()

    phase_frac = _moon_phase(dt)
    overhead, underfoot = _moon_transit_hours(dt, lng)

    def window(center: float, half_width_hours: float) -> SolunarPeriod:
        start = _hour_to_datetime(day, center - half_width_hours, tz)
        end = _hour_to_datetime(day, center + half_width_hours, tz)
        return SolunarPeriod(start=start, end=end)

    major_periods = sorted(
        [window(overhead, 1.0), window(underfoot, 1.0)], key=lambda p: p.start
    )

    moonrise = overhead - _MOON_RISE_SET_OFFSET_HOURS
    moonset = overhead + _MOON_RISE_SET_OFFSET_HOURS
    minor_periods = sorted(
        [window(moonrise, 0.5), window(moonset, 0.5)], key=lambda p: p.start
    )

    moon_phase, rating = _moon_phase_name_and_rating(phase_frac)
    illumination = (1 - math.cos(2 * math.pi * phase_frac)) / 2

    return SolunarTimes(
        major_periods=major_periods,
        minor_periods=minor_periods,
        moon_phase=moon_phase,
        illumination_pct=round(illumination * 100, 1),
        rating=rating,
    )
