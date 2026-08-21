"""Hourly fish-activity outlook (sprint 34's remaining "timing" scope).

Ports the core of the legacy `domain/forecast.py:build_activity_timeline`
-- a 24-hour, one-value-per-hour activity estimate combining solunar
periods, tide changes, dawn/dusk, and wind conditions. Per
docs/R1_RECONCILIATION_AUDIT.md, this is an adapt, not a verbatim
carry-over: inputs come from this recovery's own typed providers
(`app.providers.astronomy`'s `SunTimes`/`SolunarTimes`, the tide
predictions `app.domain.assembly` already fetches for sprint 34's
`ForecastTides`, and the wind range that module has already reconciled
from NWS/NDBC/the wind fallback chain) instead of parsing legacy's
pre-formatted strings ("10-15 mph", "6:12 AM / 7:45 PM") back apart.

`app.domain.models.ForecastEnvelope.hourly_outlook` is the field this
module designs, per sprint 11's domain-models docstring naming sprints
21/22/34/35 as the respective owners of `conditions`/`tides`/
`hourly_outlook`/`recommendations`.

The 24 hours cover the *calendar day containing `now`*, in the
location's own timezone -- a fixed midnight-to-midnight day, not a
rolling 24-hour window starting at `now` -- matching the legacy
function's behavior (its `now_hour` parameter only ever highlighted
"now" within that same fixed day; it didn't change the window).

Deliberately not ported, each requiring an input this recovery hasn't
built yet:

- Barometric-pressure-trend multiplier -- NDBC pressure parsing exists
  (sprint 15) but pressure-*trend* computation is explicitly deferred
  to sprint 35 (see `app.providers.ndbc`'s module docstring); applying
  a trend multiplier here without that data would invent a value, not
  read one.
- `build_best_times`'s labeled 2-3-window summary (a separate legacy
  function, not part of `build_activity_timeline`) -- its bridge/jetty
  and preferred-time/tide-preference boosts need user profile data
  that doesn't exist yet (sprint 36, "Preferences"). Its
  profile-independent subset (dawn/dusk, solunar, high-tide windows)
  is fully derivable from this module's hourly output later without
  another live fetch, so it's left as a follow-up rather than invented
  here or fetched again.

Wind speed thresholds are converted from the legacy function's mph
values to this recovery's canonical kt unit (see
`app.domain.normalize`'s docstring): 25 mph -> ~21.7 kt, 15 mph ->
~13.0 kt (1 kt = 1.15078 mph). The comparison uses the *low* end of the
already-reconciled wind range, matching the legacy function's parse of
the first (lowest) number in a "10-15 mph" string.
"""

from __future__ import annotations

from datetime import datetime
from datetime import time as dtime
from enum import Enum
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.infra.timezones import safe_zone
from app.providers.astronomy import SolunarTimes, SunTimes
from app.providers.noaa_coops import TidePrediction

_BASELINE = 15.0

_DAWN_DUSK_RADIUS_H = 1.5
_DAWN_DUSK_MAX_BOOST = 30.0
_DAWN_DUSK_REASON_THRESHOLD = 8.0

_MAJOR_SOLUNAR_BOOST = 35.0
_MAJOR_SOLUNAR_EDGE_BOOST = 15.0
_MINOR_SOLUNAR_BOOST = 20.0
_MINOR_SOLUNAR_EDGE_BOOST = 8.0

_TIDE_RADIUS_H = 2.0
_HIGH_TIDE_BOOST = 20.0
_LOW_TIDE_BOOST = 12.0

_NIGHT_HOURS = (23, 0, 1, 2, 3, 4)
_NIGHT_PENALTY = 0.7

_BRIGHT_MOON_ILLUMINATION_PCT = 70.0
_BRIGHT_MOON_HOURS = (21, 22, 23, 0, 1, 2, 3)
_BRIGHT_MOON_BOOST = 1.25

_KT_PER_MPH = 1 / 1.15078
_WIND_STRONG_KT = 25 * _KT_PER_MPH
_WIND_STRONG_MULT = 0.65
_WIND_MODERATE_KT = 15 * _KT_PER_MPH
_WIND_MODERATE_MULT = 0.82

_TAG_PRIME_THRESHOLD = 75
_TAG_HIGH_THRESHOLD = 50
_TAG_MED_THRESHOLD = 30

_PEAK_MIN_LEVEL = 50


class ActivityTag(str, Enum):
    LOW = "low"
    MED = "med"
    HIGH = "high"
    PRIME = "prime"


class SunEvent(str, Enum):
    SUNRISE = "sunrise"
    SUNSET = "sunset"


class TideEvent(str, Enum):
    HIGH = "high"
    LOW = "low"


class FeedingBand(str, Enum):
    MAJOR = "major"
    MINOR = "minor"


class HourlyActivity(BaseModel):
    """One hour's estimated activity level plus the overlay rails
    (`sun_event`/`tide_event`/`feeding`) the legacy timeline drew
    alongside the bars, aligned to the same hourly column.
    """

    hour: int
    time: datetime
    level: int
    tag: ActivityTag
    is_now: bool
    peak: bool
    reasons: list[str]
    sun_event: SunEvent | None
    tide_event: TideEvent | None
    tide_time: datetime | None
    feeding: FeedingBand | None


class HourlyOutlook(BaseModel):
    """The sprint-34-designed shape of `ForecastEnvelope.hourly_outlook`
    -- exactly 24 entries, hour 0 (midnight) through hour 23, for the
    calendar day containing the forecast's `now`.
    """

    hours: list[HourlyActivity]


def _hour_of_day(dt: datetime, tz: ZoneInfo) -> float:
    local = dt.astimezone(tz)
    return local.hour + local.minute / 60 + local.second / 3600


def _tag_for_level(level: int) -> ActivityTag:
    if level >= _TAG_PRIME_THRESHOLD:
        return ActivityTag.PRIME
    if level >= _TAG_HIGH_THRESHOLD:
        return ActivityTag.HIGH
    if level >= _TAG_MED_THRESHOLD:
        return ActivityTag.MED
    return ActivityTag.LOW


def _wind_condition_multiplier(wind_range: tuple[float, float] | None) -> float:
    if wind_range is None:
        return 1.0
    wind_low_kt = wind_range[0]
    if wind_low_kt >= _WIND_STRONG_KT:
        return _WIND_STRONG_MULT
    if wind_low_kt >= _WIND_MODERATE_KT:
        return _WIND_MODERATE_MULT
    return 1.0


def build_hourly_outlook(
    *,
    sun_times: SunTimes,
    solunar: SolunarTimes,
    tide_predictions: list[TidePrediction] | None,
    wind_range: tuple[float, float] | None,
    now: datetime,
    tz_name: str,
) -> HourlyOutlook:
    tz = safe_zone(tz_name)
    local_now = now.astimezone(tz)
    today = local_now.date()

    activity = [_BASELINE] * 24
    reason_parts: list[list[str]] = [[] for _ in range(24)]
    feeding_tag: list[FeedingBand | None] = [None] * 24
    sun_events: dict[int, SunEvent] = {}
    tide_events: dict[int, TidePrediction] = {}

    sr_h = _hour_of_day(sun_times.sunrise, tz)
    ss_h = _hour_of_day(sun_times.sunset, tz)
    sun_events[round(sr_h) % 24] = SunEvent.SUNRISE
    sun_events[round(ss_h) % 24] = SunEvent.SUNSET
    for h in range(24):
        dist = abs(h - sr_h)
        if dist < _DAWN_DUSK_RADIUS_H:
            boost = _DAWN_DUSK_MAX_BOOST * max(0.0, 1 - dist / _DAWN_DUSK_RADIUS_H)
            activity[h] += boost
            if boost > _DAWN_DUSK_REASON_THRESHOLD:
                reason_parts[h].append("Dawn")
        dist = abs(h - ss_h)
        if dist < _DAWN_DUSK_RADIUS_H:
            boost = _DAWN_DUSK_MAX_BOOST * max(0.0, 1 - dist / _DAWN_DUSK_RADIUS_H)
            activity[h] += boost
            if boost > _DAWN_DUSK_REASON_THRESHOLD:
                reason_parts[h].append("Dusk")

    for period in solunar.major_periods:
        s_h = _hour_of_day(period.start, tz)
        e_h = _hour_of_day(period.end, tz)
        for h in range(24):
            if s_h <= h <= e_h:
                activity[h] += _MAJOR_SOLUNAR_BOOST
                reason_parts[h].append("Major solunar")
                feeding_tag[h] = FeedingBand.MAJOR
            elif abs(h - s_h) < 1 or abs(h - e_h) < 1:
                activity[h] += _MAJOR_SOLUNAR_EDGE_BOOST

    for period in solunar.minor_periods:
        s_h = _hour_of_day(period.start, tz)
        e_h = _hour_of_day(period.end, tz)
        for h in range(24):
            if s_h <= h <= e_h:
                activity[h] += _MINOR_SOLUNAR_BOOST
                reason_parts[h].append("Minor solunar")
                if feeding_tag[h] != FeedingBand.MAJOR:
                    feeding_tag[h] = FeedingBand.MINOR
            elif abs(h - s_h) < 1 or abs(h - e_h) < 1:
                activity[h] += _MINOR_SOLUNAR_EDGE_BOOST

    for prediction in tide_predictions or []:
        local_time = prediction.time.astimezone(tz)
        if local_time.date() != today:
            continue
        t_h = _hour_of_day(prediction.time, tz)
        tide_events[round(t_h) % 24] = prediction
        boost_ceiling = (
            _HIGH_TIDE_BOOST if prediction.kind == "high" else _LOW_TIDE_BOOST
        )
        for h in range(24):
            dist = abs(h - t_h)
            if dist < _TIDE_RADIUS_H:
                activity[h] += boost_ceiling * max(0.0, 1 - dist / _TIDE_RADIUS_H)
                if dist < 1:
                    reason_parts[h].append(
                        "High tide" if prediction.kind == "high" else "Low tide"
                    )

    for h in _NIGHT_HOURS:
        activity[h] *= _NIGHT_PENALTY

    if solunar.illumination_pct >= _BRIGHT_MOON_ILLUMINATION_PCT:
        for h in _BRIGHT_MOON_HOURS:
            activity[h] *= _BRIGHT_MOON_BOOST

    max_val = max(activity) if max(activity) > 0 else 1.0
    normalized = [min(100.0, activity[h] / max_val * 100) for h in range(24)]

    condition_mult = _wind_condition_multiplier(wind_range)
    levels = [min(100, round(v * condition_mult)) for v in normalized]
    peak_level = max(levels) if levels else 0

    now_hour = local_now.hour

    hours: list[HourlyActivity] = []
    for h in range(24):
        level = levels[h]
        seen: set[str] = set()
        deduped: list[str] = []
        for reason in reason_parts[h]:
            if reason not in seen:
                seen.add(reason)
                deduped.append(reason)

        tide_prediction = tide_events.get(h)
        hours.append(
            HourlyActivity(
                hour=h,
                time=datetime.combine(today, dtime(h, 0), tzinfo=tz),
                level=level,
                tag=_tag_for_level(level),
                is_now=h == now_hour,
                peak=level == peak_level and level >= _PEAK_MIN_LEVEL,
                reasons=deduped,
                sun_event=sun_events.get(h),
                tide_event=(
                    TideEvent(tide_prediction.kind)
                    if tide_prediction is not None
                    else None
                ),
                tide_time=tide_prediction.time if tide_prediction is not None else None,
                feeding=feeding_tag[h],
            )
        )

    return HourlyOutlook(hours=hours)


__all__ = [
    "ActivityTag",
    "FeedingBand",
    "HourlyActivity",
    "HourlyOutlook",
    "SunEvent",
    "TideEvent",
    "build_hourly_outlook",
]
