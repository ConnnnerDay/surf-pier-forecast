"""Forecast assembly, conditions analysis, and advisory features."""

from __future__ import annotations

import concurrent.futures as _cf
import logging
import math
import operator as _operator
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from locations import get_fallback_conditions, get_monthly_water_temps
from regulations import get_official_regulations_url
from utils import safe_zone as _safe_zone

from services.astro import (
    _sun_times,
    compute_lunar_details,
    compute_solunar_times,
    compute_twilight_times,
)
from services.ndbc import (
    NDBC_STATIONS,
    _try_ndbc_station,
    fetch_barometric_pressure,
)
from services.noaa import (
    MONTHLY_AVG_WATER_TEMP_F,
    WATER_TEMP_STATION,
    _try_coops_wind,
    build_tide_chart_svg,
    fetch_coops_environmental_metrics,
    fetch_currents_observation,
    fetch_currents_predictions,
    fetch_tide_predictions,
    get_water_temp,
)
from services.nws import (
    NWS_MARINE_ZONE,
    _KT_TO_MPH,
    _MPH_TO_KNOTS,
    _try_nws_forecast,
    _try_nws_gridpoint,
    _fetch_nws_extended,
    fetch_current_weather,
    fetch_state_alerts,
    fetch_weather_alerts,
)
from services.datagov import get_water_quality_summary as _get_wq
from services.hdx_fao import get_hdx_fao_enrichment as _get_fao
from domain.species import (
    _OFFSHORE_DIRS_EAST,
    _OFFSHORE_DIRS_WEST,
    _ONSHORE_DIRS_EAST,
    _ONSHORE_DIRS_WEST,
    _SPECIES_BY_COAST,
    _build_conditions_modifier,
    _build_profile_filter,
    _get_technique_tip,
    _score_species,
    build_bait_ranking,
    build_lure_recommendations,
    build_natural_bait_chart,
    build_rig_recommendations,
    build_species_calendar,
    build_species_ranking,
    build_spawning_report,
)

logger = logging.getLogger(__name__)

FORECAST_VERSION = "v1.0.0"

# In-process cache for personalize_forecast results.
# Key: (loc_id, generated_at, hour, profile_tuple).  Hour-level granularity
# matches the technique-tip and gear-checklist inputs; generated_at invalidates
# when a fresh forecast arrives; profile_tuple covers all profile fields.
_PERSONALIZE_CACHE: dict[tuple, dict] = {}
_PERSONALIZE_CACHE_MAX = 64

def _profile_cache_key(forecast: dict, location: dict | None, profile: dict) -> tuple:
    """Build a hashable cache key for a personalized forecast."""
    loc_id = (location or {}).get("id", "")
    generated_at = forecast.get("generated_at", "")
    tz_name = (location or {}).get("timezone", "America/New_York")
    tz = _safe_zone(tz_name)
    hour = datetime.now(tz).hour

    def _freeze(v: object) -> object:
        if isinstance(v, list):
            return tuple(v)
        return v

    profile_tuple = tuple(
        sorted((_freeze(k), _freeze(v)) for k, v in profile.items())
    )
    return (loc_id, generated_at, hour, profile_tuple)

# Shared pool for all forecast requests.  Creating a new executor per request
# wastes time spawning/tearing down threads; a module-level pool keeps workers
# warm and handles concurrent requests without queuing (30 workers ≈ 3 × 10
# concurrent forecasts before back-pressure).
_FORECAST_POOL = _cf.ThreadPoolExecutor(
    max_workers=30, thread_name_prefix="forecast-fetch"
)

# Generic mid-Atlantic historical monthly averages used as the absolute
# last resort when no location is set.
MONTHLY_AVG_WIND: dict[int, tuple[float, float]] = {
    1: (8, 15),
    2: (8, 16),
    3: (9, 16),
    4: (8, 15),
    5: (7, 13),
    6: (6, 12),
    7: (5, 11),
    8: (5, 11),
    9: (7, 14),
    10: (7, 14),
    11: (7, 14),
    12: (8, 15),
}
MONTHLY_AVG_WAVES: dict[int, tuple[float, float]] = {
    1: (2, 4),
    2: (2, 4),
    3: (2, 4),
    4: (1, 3),
    5: (1, 3),
    6: (1, 2),
    7: (1, 2),
    8: (1, 2),
    9: (2, 4),
    10: (2, 4),
    11: (2, 4),
    12: (2, 4),
}
MONTHLY_AVG_WIND_DIR: dict[int, str] = {
    1: "NW",
    2: "NW",
    3: "SW",
    4: "SW",
    5: "SW",
    6: "SW",
    7: "SW",
    8: "SW",
    9: "NE",
    10: "NE",
    11: "NW",
    12: "NW",
}

# Fallback coordinates used only when no location is set and no session exists.
# Wrightsville Beach, NC — a geographically central east-coast default.
# Override via environment: DEFAULT_LAT / DEFAULT_LNG.
_LAT: float = float(os.environ.get("DEFAULT_LAT", "34.2104"))
_LNG: float = float(os.environ.get("DEFAULT_LNG", "-77.7964"))

# Direction abbreviation map (shared by multiple forecast helpers)
_DIR_MAP: dict[str, str] = {
    "north": "N",
    "northeast": "NE",
    "northwest": "NW",
    "south": "S",
    "southeast": "SE",
    "southwest": "SW",
    "east": "E",
    "west": "W",
}

# -- Source 5: Seasonal averages (ALWAYS succeeds) --------------------------

def _seasonal_averages(
    month: int,
) -> tuple[tuple[float, float], tuple[float, float], str]:
    """Historical monthly averages -- the last resort. Never fails."""
    return (
        MONTHLY_AVG_WIND[month],
        MONTHLY_AVG_WAVES[month],
        MONTHLY_AVG_WIND_DIR[month],
    )

# -- Combined fetcher -------------------------------------------------------

def get_marine_conditions(
    month: int,
    location: Optional[dict[str, Any]] = None,
    sources_used: Optional[list[str]] = None,
    fallbacks_triggered: Optional[list[str]] = None,
) -> tuple[tuple[float, float], tuple[float, float], str]:
    """Get marine conditions, trying every source until we have full data.

    Returns (wind_range, wave_range, wind_dir).  Guaranteed to never return
    None for any field -- seasonal averages fill any remaining gaps.
    """
    nws_zone = (location or {}).get("nws_zone", NWS_MARINE_ZONE)
    ndbc_list = (location or {}).get("ndbc_stations", [s[0] for s in NDBC_STATIONS])
    coops_id = (location or {}).get("coops_station", WATER_TEMP_STATION)
    loc_lat = (location or {}).get("lat", _LAT)
    loc_lng = (location or {}).get("lng", _LNG)

    wind_range: Optional[tuple[float, float]] = None
    wave_range: Optional[tuple[float, float]] = None
    wind_dir: Optional[str] = None

    sources: list[tuple[str, Any]] = [
        ("NWS zone forecast", lambda: _try_nws_forecast(nws_zone)),
    ]
    for sid in ndbc_list:
        sources.append((f"NDBC {sid}", lambda s=sid: _try_ndbc_station(s)))
    sources.append(("NOAA CO-OPS wind", lambda: _try_coops_wind(coops_id)))
    sources.append(
        ("NWS gridpoint forecast", lambda: _try_nws_gridpoint(loc_lat, loc_lng))
    )

    for name, fetcher in sources:
        # Stop once we have both wind and waves
        if wind_range is not None and wave_range is not None and wind_dir is not None:
            break
        try:
            w, s, d = fetcher()
            if wind_range is None and w is not None:
                wind_range = w
                if sources_used is not None:
                    sources_used.append(f"{name}:wind")
                logger.debug("Wind from %s: %s", name, w)
            if wave_range is None and s is not None:
                wave_range = s
                if sources_used is not None:
                    sources_used.append(f"{name}:waves")
                logger.debug("Waves from %s: %s", name, s)
            if wind_dir is None and d is not None:
                wind_dir = d
                if sources_used is not None:
                    sources_used.append(f"{name}:wind_dir")
        except Exception as exc:
            logger.warning("%s unavailable: %s", name, exc, exc_info=True)

    # Fill any remaining gaps with location-specific or default averages
    if location:
        avg_wind, avg_waves, avg_dir = get_fallback_conditions(location, month)
    else:
        avg_wind, avg_waves, avg_dir = _seasonal_averages(month)
    if wind_range is None:
        wind_range = avg_wind
        if fallbacks_triggered is not None:
            fallbacks_triggered.append("seasonal_avg_wind")
        logger.debug("Wind from seasonal avg: %s", avg_wind)
    if wave_range is None:
        wave_range = avg_waves
        if fallbacks_triggered is not None:
            fallbacks_triggered.append("seasonal_avg_waves")
        logger.debug("Waves from seasonal avg: %s", avg_waves)
    if wind_dir is None:
        wind_dir = avg_dir
        if fallbacks_triggered is not None:
            fallbacks_triggered.append("seasonal_avg_wind_dir")

    return wind_range, wave_range, wind_dir

class ExternalDataService:
    """Base service with consistent retries and logging for upstream calls."""

    def __init__(self, retries: int = 2, retry_delay_s: float = 0.15):
        self.retries = retries
        self.retry_delay_s = retry_delay_s

    def _run_with_retries(self, name: str, func: Any, default: Any = None) -> Any:
        for attempt in range(1, self.retries + 2):
            try:
                return func()
            except Exception as exc:
                logger.debug("%s failed on attempt %s: %s", name, attempt, exc)
                if attempt >= self.retries + 1:
                    return default
                time.sleep(self.retry_delay_s)

class MarineForecastService(ExternalDataService):
    def get_marine_forecast(
        self,
        month: int,
        location: Optional[dict[str, Any]] = None,
        sources_used: Optional[list[str]] = None,
        fallbacks_triggered: Optional[list[str]] = None,
    ) -> tuple[tuple[float, float], tuple[float, float], str]:
        return self._run_with_retries(
            "marine forecast",
            lambda: get_marine_conditions(
                month,
                location,
                sources_used=sources_used,
                fallbacks_triggered=fallbacks_triggered,
            ),
            default=_seasonal_averages(month),
        )

class TidePredictionService(ExternalDataService):
    def get_tide_predictions(
        self,
        now: datetime,
        location: Optional[dict[str, Any]] = None,
        tz_name: str = "America/New_York",
    ) -> dict[str, Any]:
        coops_id = (location or {}).get("coops_station", WATER_TEMP_STATION)
        tides = (
            self._run_with_retries(
                "tide predictions",
                lambda: fetch_tide_predictions(coops_id, tz_name),
                default=[],
            )
            or []
        )
        if not tides:
            return {}

        today_date_str = now.strftime("%Y%m%d")
        today_tides = [t for t in tides if t.get("date_str") == today_date_str]
        if not today_tides:
            # Fallback for timezone/date-boundary mismatches: show the first
            # available prediction day instead of hiding tides entirely.
            first_date = next(
                (t.get("date_str") for t in tides if t.get("date_str")), None
            )
            if first_date:
                today_tides = [t for t in tides if t.get("date_str") == first_date]
        result: dict[str, Any] = {"tides": today_tides}
        current_hour = now.hour + now.minute / 60
        chart_data = build_tide_chart_svg(today_tides, now_hour=current_hour)
        if chart_data:
            result["tide_chart"] = chart_data

        tide_state = ""
        for i in range(len(tides) - 1):
            t_now = tides[i].get("hour", 0)
            t_next = tides[i + 1].get("hour", 24)
            if t_now <= current_hour < t_next:
                tide_state = (
                    "Rising" if tides[i + 1].get("type") == "High" else "Falling"
                )
                break
        if not tide_state and tides:
            if current_hour < tides[0].get("hour", 12):
                tide_state = "Falling" if tides[0].get("type") == "Low" else "Rising"
            else:
                tide_state = "Falling" if tides[-1].get("type") == "High" else "Rising"
        if tide_state:
            result["tide_state"] = tide_state
        return result

class BuoyDataService(ExternalDataService):
    def get_barometric_pressure(
        self, location: Optional[dict[str, Any]] = None
    ) -> Optional[float]:
        return self._run_with_retries(
            "barometric pressure",
            lambda: fetch_barometric_pressure(location),
            default=None,
        )

class WeatherDataService(ExternalDataService):
    def get_weather_alerts(self, lat: float, lng: float) -> list[dict[str, Any]]:
        return (
            self._run_with_retries(
                "weather alerts",
                lambda: fetch_weather_alerts(lat, lng),
                default=[],
            )
            or []
        )

    def get_state_alerts(self, state_code: str) -> list[dict[str, Any]]:
        return (
            self._run_with_retries(
                "state weather alerts",
                lambda: fetch_state_alerts(state_code),
                default=[],
            )
            or []
        )

    def get_current_weather(self, lat: float, lng: float) -> Optional[dict[str, Any]]:
        return self._run_with_retries(
            "current weather",
            lambda: fetch_current_weather(lat, lng),
            default=None,
        )

class EnvironmentalDataService(ExternalDataService):
    def get_coops_environmental(self, station_id: str) -> dict[str, float]:
        return (
            self._run_with_retries(
                "coops environmental",
                lambda: fetch_coops_environmental_metrics(station_id),
                default={},
            )
            or {}
        )

    def get_currents(self, station_id: str, tz_name: str) -> list[dict[str, str]]:
        return (
            self._run_with_retries(
                "currents predictions",
                lambda: fetch_currents_predictions(station_id, tz_name),
                default=[],
            )
            or []
        )

    def get_current_observation(
        self, station_id: str, tz_name: str
    ) -> Optional[dict[str, str]]:
        return self._run_with_retries(
            "currents observation",
            lambda: fetch_currents_observation(station_id, tz_name),
            default=None,
        )

class AstronomyService(ExternalDataService):
    def get_sun_times(
        self,
        now: datetime,
        lat: float,
        lng: float,
        tz_name: str,
    ) -> tuple[Optional[datetime], Optional[datetime], str]:
        values = self._run_with_retries(
            "sun times",
            lambda: _sun_times(now, lat, lng, tz_name),
            default=(None, None),
        )
        sunrise, sunset = values
        if sunrise and sunset:
            return (
                sunrise,
                sunset,
                f"{sunrise.strftime('%-I:%M %p')} / {sunset.strftime('%-I:%M %p')}",
            )
        return None, None, "Unavailable"

    def get_solunar_times(
        self, now: datetime, lat: float, lng: float, tz_name: str
    ) -> dict[str, Any]:
        return (
            self._run_with_retries(
                "solunar",
                lambda: compute_solunar_times(now, lat, lng, tz_name),
                default={},
            )
            or {}
        )

    def get_twilight_times(
        self, now: datetime, lat: float, lng: float, tz_name: str
    ) -> dict[str, str]:
        return (
            self._run_with_retries(
                "twilight",
                lambda: compute_twilight_times(now, lat, lng, tz_name),
                default={},
            )
            or {}
        )

    def get_lunar_details(
        self, now: datetime, lng: float, tz_name: str
    ) -> dict[str, Any]:
        return (
            self._run_with_retries(
                "lunar details",
                lambda: compute_lunar_details(now, lng, tz_name),
                default={},
            )
            or {}
        )

class ForecastBuilder:
    """Central forecast orchestrator for external services + domain assembly."""

    def __init__(self) -> None:
        self.marine_service = MarineForecastService()
        self.tide_service = TidePredictionService()
        self.buoy_service = BuoyDataService()
        self.weather_service = WeatherDataService()
        self.environment_service = EnvironmentalDataService()
        self.astro_service = AstronomyService()

# ---------------------------------------------------------------------------
# Fishability scoring constants
# ---------------------------------------------------------------------------
# Starting score — midpoint of the 0-100 scale before any adjustments.
_SCORE_BASELINE = 50.0

# Wind speed thresholds (knots) for scoring adjustments.
_WIND_LIGHT_KT = 8
_WIND_MODERATE_LOW_KT = 12
_WIND_MODERATE_HIGH_KT = 16
_WIND_STRONG_KT = 20
_WIND_VERY_STRONG_KT = 25

# Wave height thresholds (feet) for scoring adjustments.
_WAVE_FLAT_FT = 1.5
_WAVE_MODERATE_FT = 3.0
_WAVE_ROUGH_FT = 5.0
_WAVE_HEAVY_FT = 7.0

# Verdict score bands (score >= threshold → label).
_VERDICT_EXCELLENT = 80
_VERDICT_GOOD = 64
_VERDICT_FAIR = 48
_VERDICT_CHALLENGING = 32

# ---------------------------------------------------------------------------
# Canonical coast derivation
# ---------------------------------------------------------------------------

def _derive_coast(location: Optional[dict[str, Any]]) -> Optional[str]:
    """Return the canonical coast string for a location, or ``None`` if unknown.

    Mapping of ``conditions_region`` prefix → coast:

        ``"pacific*"``  → ``"west"``
        ``"hawaii*"``   → ``"hawaii"``
        ``"atlantic*"`` → ``"east"``
        ``"gulf*"``     → ``"east"``
        missing / empty / unrecognised → ``None``

    ``None`` means the coast could not be determined from the location's
    ``conditions_region`` field.  All callers **must** treat ``None`` as "show
    no species" rather than falling back to a default coast; silently leaking
    species from the wrong region is worse than showing an empty list.

    This is the single authoritative coast-derivation function.  Do not
    re-implement the ``conditions_region`` → coast mapping elsewhere.
    """
    cr = (location or {}).get("conditions_region", "")
    if cr.startswith("pacific"):
        return "west"
    if cr.startswith("hawaii"):
        return "hawaii"
    if cr.startswith("atlantic") or cr.startswith("gulf"):
        return "east"
    return None

def score_conditions(
    wind_range: Optional[tuple[float, float]],
    wave_range: Optional[tuple[float, float]],
    wind_dir: str = "",
    water_temp_f: Optional[float] = None,
    is_live_temp: bool = False,
    tide_state: str = "",
    tides: Optional[list[dict[str, Any]]] = None,
    sunrise: Optional[datetime] = None,
    sunset: Optional[datetime] = None,
    now: Optional[datetime] = None,
    solunar: Optional[dict[str, Any]] = None,
    coast: str = "east",
    fishing_types: Optional[list[str]] = None,
    max_wind_kt: Optional[float] = None,
    max_wave_ft: Optional[float] = None,
) -> dict[str, Any]:
    """Score fishability from all available marine + astronomical signals.

    Returns a dict with:

    - ``score``   — 0-100 go/no-go index (``None`` when conditions are unknown)
    - ``verdict`` — 5-tier label: Excellent / Good / Fair / Challenging / Poor
    - ``factors`` — ordered plain-language drivers (most impactful first), e.g.
      ``["Flat surf (1-2 ft)", "Light wind (4-8 kt)", "Major feeding window"]``
    - ``summary`` — the top factors joined into a one-line explanation
    - ``exceeds`` — warnings when conditions exceed the angler's personal
      ``max_wind_kt`` / ``max_wave_ft`` comfort thresholds (empty when unset)

    When *fishing_types* is supplied the score is adjusted to reflect what
    actually matters for each method — e.g. kayak anglers are penalised
    more steeply for wind/waves, fly anglers for wind, wade/bridge anglers
    get a partial offset on the open-ocean wave penalty.  When *max_wind_kt*
    or *max_wave_ft* are supplied, exceeding them applies an extra penalty so
    the verdict reflects the angler's stated tolerance.
    """
    if wind_range is None or wave_range is None:
        return {
            "score": None,
            "verdict": "Unknown",
            "factors": [],
            "summary": "",
            "exceeds": [],
        }

    score = _SCORE_BASELINE
    wind_max = wind_range[1]
    wave_max = wave_range[1]

    # (impact, phrase) pairs — sorted by absolute impact to surface the
    # dominant drivers in the explanation regardless of sign.
    factors: list[tuple[float, str]] = []
    wind_lbl = f"{int(wind_range[0])}-{int(wind_range[1])} kt"
    wave_lbl = f"{int(wave_range[0])}-{int(wave_range[1])} ft"

    # Wind speed (primary safety + fishability signal)
    if wind_max <= _WIND_LIGHT_KT:
        score += 14
        factors.append((14, f"Light wind ({wind_lbl})"))
    elif wind_max <= _WIND_MODERATE_LOW_KT:
        score += 8
        factors.append((8, f"Moderate wind ({wind_lbl})"))
    elif wind_max <= _WIND_MODERATE_HIGH_KT:
        score += 2
        factors.append((2, f"Breezy ({wind_lbl})"))
    elif wind_max <= _WIND_STRONG_KT:
        score -= 8
        factors.append((-8, f"Strong wind ({wind_lbl})"))
    elif wind_max <= _WIND_VERY_STRONG_KT:
        score -= 16
        factors.append((-16, f"Very strong wind ({wind_lbl})"))
    else:
        score -= 26
        factors.append((-26, f"Gale-force wind ({wind_lbl})"))

    # Wave height (primary surf-access signal)
    if wave_max <= _WAVE_FLAT_FT:
        score += 10
        factors.append((10, f"Flat surf ({wave_lbl})"))
    elif wave_max <= _WAVE_MODERATE_FT:
        score += 6
        factors.append((6, f"Light surf ({wave_lbl})"))
    elif wave_max <= _WAVE_ROUGH_FT:
        score -= 4
        factors.append((-4, f"Moderate surf ({wave_lbl})"))
    elif wave_max <= _WAVE_HEAVY_FT:
        score -= 12
        factors.append((-12, f"Rough surf ({wave_lbl})"))
    else:
        score -= 22
        factors.append((-22, f"Heavy surf ({wave_lbl})"))

    # Wind direction heuristic by coast (offshore usually cleaner water).
    if wind_dir:
        if coast == "west":
            offshore_dirs = _OFFSHORE_DIRS_WEST
            onshore_dirs = _ONSHORE_DIRS_WEST
        else:
            offshore_dirs = _OFFSHORE_DIRS_EAST
            onshore_dirs = _ONSHORE_DIRS_EAST
        if wind_dir in offshore_dirs:
            score += 4
            factors.append((4, f"Clean offshore wind ({wind_dir})"))
        elif wind_dir in onshore_dirs:
            score -= 4
            factors.append((-4, f"Onshore wind ({wind_dir}) — murkier water"))

    # Water temp comfort/activity proxy (still species-specific elsewhere).
    if water_temp_f is not None:
        if 58 <= water_temp_f <= 78:
            score += 6
            factors.append((6, f"Ideal water temp ({water_temp_f:.0f}°F)"))
        elif 50 <= water_temp_f < 58 or 78 < water_temp_f <= 84:
            score += 2
            factors.append((2, f"Fair water temp ({water_temp_f:.0f}°F)"))
        else:
            score -= 4
            factors.append((-4, f"Off-peak water temp ({water_temp_f:.0f}°F)"))
        if is_live_temp:
            score += 1

    # Tide state and tide range (bigger movement often improves feeding windows).
    if tide_state in {"Rising", "Falling"}:
        score += 2
        factors.append((2, f"Moving tide ({tide_state.lower()})"))
    if tides:
        heights = [
            float(t.get("height", 0.0)) for t in tides if t.get("height") is not None
        ]
        if heights:
            tide_range = max(heights) - min(heights)
            if tide_range >= 4:
                score += 5
                factors.append((5, "Big tidal swing"))
            elif tide_range >= 2:
                score += 2

        # Bonus near tide turn windows (high/low slack periods often productive).
        if now:
            nearest_delta = None
            for t in tides:
                th = t.get("hour")
                if th is None:
                    continue
                tide_dt = now.replace(
                    hour=int(th) % 24,
                    minute=int((float(th) % 1) * 60),
                    second=0,
                    microsecond=0,
                )
                delta = abs((tide_dt - now).total_seconds())
                nearest_delta = (
                    delta if nearest_delta is None else min(nearest_delta, delta)
                )
            if nearest_delta is not None and nearest_delta <= 90 * 60:
                score += 3
                factors.append((3, "Near a tide turn"))

    # Light windows around sunrise/sunset are usually better bite periods.
    if now and sunrise and sunset:
        if abs((now - sunrise).total_seconds()) <= 2 * 3600:
            score += 4
            factors.append((4, "Dawn bite window"))
        if abs((sunset - now).total_seconds()) <= 2 * 3600:
            score += 4
            factors.append((4, "Dusk bite window"))

    # Solunar quality + illumination.
    if solunar:
        rating_bonus = {
            "Excellent": 8,
            "Good": 4,
            "Fair": 0,
            "Poor": -6,
        }
        s_rating = solunar.get("rating", "")
        score += rating_bonus.get(s_rating, 0)
        if s_rating == "Excellent":
            factors.append((8, "Major feeding window (solunar)"))
        elif s_rating == "Good":
            factors.append((4, "Good solunar period"))
        elif s_rating == "Poor":
            factors.append((-6, "Weak solunar period"))

        illum = solunar.get("illumination_pct")
        if isinstance(illum, (int, float)):
            if 40 <= illum <= 85:
                score += 2
            elif illum < 10 or illum > 95:
                score += 1

    # --- Fishing-type-specific score modifiers ---
    # Applied after base scoring so they shift the verdict relative to the
    # generic baseline rather than replacing it entirely.
    if fishing_types:
        ft = set(fishing_types)

        # Kayak — wind and wave height are safety signals, not just fishability.
        # Lower thresholds than the generic model.
        if "kayak" in ft:
            if wind_max >= 20:
                score -= 16  # Severe: do-not-launch territory
            elif wind_max >= 15:
                score -= 10  # Significant: stay near shore
            elif wind_max >= 10:
                score -= 4  # Noticeable: plan exit route
            if wave_max >= 3:
                score -= 10  # Rough chop for a kayak
            elif wave_max >= 2:
                score -= 4

        # Fly fishing — casting ability is the primary constraint.
        # Wind dominates; wave height is mostly irrelevant inshore.
        if "fly" in ft:
            if wind_max >= 20:
                score -= 12  # Near-impossible casting
            elif wind_max >= 15:
                score -= 8  # Very difficult
            elif wind_max >= 10:
                score -= 3  # Challenging but manageable
            # Wave height at sea doesn't affect inshore flats/wade fly fishing;
            # partially cancel the generic wave penalty.
            if wave_max < 4:
                score += 5

        # Wade fishing — sheltered inshore, so open-ocean wave height largely
        # irrelevant.  Wind still matters for clarity and casting.
        if "wade" in ft and "surf" not in ft:
            if wave_max < 4:
                score += 4  # Calm flats/bay conditions unaffected by offshore swell
            # Slight bonus for calm wind (ideal sight-fishing conditions)
            if wind_max <= 8:
                score += 3

        # Jetty / rock structure — wave surge is a safety issue at lower heights
        # than the generic model treats as dangerous.
        if "jetty" in ft:
            if wave_max >= 4:
                score -= 12  # Unsafe on rocks
            elif wave_max >= 2:
                score -= 5  # Slippery, caution required

        # Bridge / causeway — open-ocean wave height is mostly irrelevant;
        # tidal current (already captured via tide_state) is what matters.
        if "bridge" in ft:
            if wave_max < 4:
                score += 4  # Ocean swell doesn't affect protected tidal channels
            # Extra bonus for a moving tide (current activates the bite)
            if tide_state in ("Rising", "Falling"):
                score += 4

        # Charter / head boat — passenger comfort and sea-state are key.
        # Penalise rougher conditions more than the generic model does.
        if "charter" in ft:
            if wave_max >= 6:
                score -= 10  # Dangerous / trip-cancellation territory
            elif wave_max >= 4:
                score -= 5  # Rough — many passengers will struggle
            if wind_max >= 20:
                score -= 6  # Gale-force additions beyond the base penalty

        # Offshore (private boat, not charter) — similar to charter but anglers
        # are generally more experienced; slightly less aggressive penalty.
        if "offshore" in ft and "charter" not in ft:
            if wave_max >= 6:
                score -= 6
            elif wave_max >= 4:
                score -= 2

    # --- Angler comfort thresholds ---
    # When the user has stated a personal limit, exceeding it both penalises the
    # score and surfaces an explicit warning so a technically-"Fair" day is
    # flagged as above their comfort zone.
    exceeds: list[str] = []
    if max_wind_kt is not None and wind_max > max_wind_kt:
        score -= 12
        warning = f"Wind {int(wind_max)} kt over your {int(max_wind_kt)} kt limit"
        exceeds.append(warning)
        factors.append((-12, warning))
    if max_wave_ft is not None and wave_max > max_wave_ft:
        score -= 12
        warning = f"Surf {int(wave_max)} ft over your {int(max_wave_ft)} ft limit"
        exceeds.append(warning)
        factors.append((-12, warning))

    score = max(0, min(100, score))
    if score >= _VERDICT_EXCELLENT:
        verdict = "Excellent"
    elif score >= _VERDICT_GOOD:
        verdict = "Good"
    elif score >= _VERDICT_FAIR:
        verdict = "Fair"
    elif score >= _VERDICT_CHALLENGING:
        verdict = "Challenging"
    else:
        verdict = "Poor"

    # Order drivers by absolute impact so the explanation leads with what
    # matters most.  Threshold warnings always sort to the front (impact 12+).
    factors.sort(key=lambda f: -abs(f[0]))
    factor_phrases = [phrase for _, phrase in factors]
    summary = ", ".join(factor_phrases[:4])

    return {
        "score": int(round(score)),
        "verdict": verdict,
        "factors": factor_phrases,
        "summary": summary,
        "exceeds": exceeds,
    }

def classify_conditions(
    wind_range: Optional[tuple[float, float]],
    wave_range: Optional[tuple[float, float]],
    wind_dir: str = "",
    water_temp_f: Optional[float] = None,
    is_live_temp: bool = False,
    tide_state: str = "",
    tides: Optional[list[dict[str, Any]]] = None,
    sunrise: Optional[datetime] = None,
    sunset: Optional[datetime] = None,
    now: Optional[datetime] = None,
    solunar: Optional[dict[str, Any]] = None,
    coast: str = "east",
    fishing_types: Optional[list[str]] = None,
    max_wind_kt: Optional[float] = None,
    max_wave_ft: Optional[float] = None,
) -> str:
    """Return just the 5-tier fishability verdict label.

    Thin backward-compatible wrapper around :func:`score_conditions` for
    callers that only need the label (e.g. the multi-day outlook).
    """
    return score_conditions(
        wind_range,
        wave_range,
        wind_dir=wind_dir,
        water_temp_f=water_temp_f,
        is_live_temp=is_live_temp,
        tide_state=tide_state,
        tides=tides,
        sunrise=sunrise,
        sunset=sunset,
        now=now,
        solunar=solunar,
        coast=coast,
        fishing_types=fishing_types,
        max_wind_kt=max_wind_kt,
        max_wave_ft=max_wave_ft,
    )["verdict"]

def build_multiday_outlook(
    now: datetime,
    location: Optional[dict[str, Any]] = None,
    fishing_types: Optional[list[str]] = None,
    targets: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Build a 3-day outlook with conditions and fishability.

    Each day includes: day_label, wind summary, wave estimate,
    water temp estimate, and a fishability verdict.

    If *fishing_types* or *targets* are provided (from the user's profile),
    the per-day top-species list is filtered to match.
    """
    loc_lat = (location or {}).get("lat", _LAT)
    loc_lng = (location or {}).get("lng", _LNG)
    loc_zone = (location or {}).get("nws_zone", "")
    tz_name = (location or {}).get("timezone", "America/New_York")
    tz = _safe_zone(tz_name)

    # Try NWS extended forecast for wind data; falls back to marine zone
    # forecast (loc_zone) for offshore/pier coordinates where the gridpoint
    # API returns an error.
    nws_periods = _fetch_nws_extended(loc_lat, loc_lng, zone=loc_zone)

    def _estimate_wave_range_from_wind(
        day_wind_range: Optional[tuple[float, float]],
    ) -> Optional[tuple[float, float]]:
        """Estimate wave range from forecast wind when marine seas are unavailable."""
        if not day_wind_range:
            return None
        avg_kt = (day_wind_range[0] + day_wind_range[1]) / 2
        low_ft = max(1.0, float(math.floor(avg_kt / 6)))
        high_ft = max(low_ft + 1.0, float(math.ceil(avg_kt / 4)))
        high_ft = min(high_ft, 12.0)
        return (low_ft, high_ft)

    _profile_filter = _build_profile_filter(fishing_types, targets)
    coast = _derive_coast(location)
    _coast_species = _SPECIES_BY_COAST.get(coast, []) if coast else []
    outlook_fish_region = (location or {}).get("fish_region", "")
    _monthly_temps = get_monthly_water_temps(location) if location else None
    wind_coast = "west" if coast == "west" else "east"

    days = []
    for offset_days in range(1, 4):  # tomorrow, day after, day 3
        future = now + timedelta(days=offset_days)
        future_month = future.month

        day_label = future.strftime("%A")  # e.g., "Thursday"
        date_label = future.strftime("%b %d")  # e.g., "Feb 27"

        # --- Wind from NWS or fallback ---
        wind_str = ""
        wind_range = None
        wind_dir_day = ""
        day_period = None
        if nws_periods:
            for p in nws_periods:
                if not p.get("isDaytime"):
                    continue

                start_raw = p.get("startTime")
                if start_raw:
                    try:
                        period_dt = datetime.fromisoformat(start_raw)
                        if period_dt.astimezone(tz).date() == future.date():
                            day_period = p
                            break
                        # startTime parsed but belongs to a different day; skip
                        # the name-based fallback so we don't match the wrong period.
                        continue
                    except ValueError:
                        logger.debug(
                            "Unable to parse NWS period startTime: %s", start_raw
                        )

                # Fallback for API payloads that omit startTime (or when
                # parsing failed): match by the period's human-readable name.
                if p.get("name", "").lower().startswith(day_label[:3].lower()):
                    day_period = p
                    break

        if day_period:
            ws = day_period.get("windSpeed", "")
            wd = day_period.get("windDirection", "")
            wind_dir_day = wd or ""
            m = re.search(r"(\d+)(?:\s*to\s*(\d+))?\s*mph", ws, re.IGNORECASE)
            if m:
                low_mph = float(m.group(1))
                high_mph = float(m.group(2)) if m.group(2) else low_mph
                low_kt = round(low_mph * _MPH_TO_KNOTS)
                high_kt = round(high_mph * _MPH_TO_KNOTS)
                wind_range = (low_kt, high_kt)
                wind_str = (
                    f"{wd} {low_kt}-{high_kt} kt" if wd else f"{low_kt}-{high_kt} kt"
                )
            else:
                # Marine zone periods report wind in knots inside detailedForecast
                # text rather than in a separate windSpeed field.
                marine_text = day_period.get("detailedForecast", "")
                kt_m = re.search(
                    r"(\d+)(?:\s*to\s*(\d+))?\s*(?:kt|knots?)",
                    marine_text,
                    re.IGNORECASE,
                )
                if kt_m:
                    low_kt = round(float(kt_m.group(1)))
                    high_kt = round(float(kt_m.group(2))) if kt_m.group(2) else low_kt
                    wind_range = (low_kt, high_kt)
                    if not wd:
                        dir_m = re.search(
                            r"(north(?:east|west)?|south(?:east|west)?|east|west"
                            r"|NE|NW|SE|SW|N|E|S|W)\s+wind",
                            marine_text,
                            re.IGNORECASE,
                        )
                        if dir_m:
                            raw = dir_m.group(1)
                            wd = _DIR_MAP.get(raw.lower(), raw.upper())
                            wind_dir_day = wd
                    wind_str = (
                        f"{wd} {low_kt}-{high_kt} kt"
                        if wd
                        else f"{low_kt}-{high_kt} kt"
                    )

        if not wind_str:
            # Use regional fallback
            fb_wind, fb_waves, fb_dir = (
                get_fallback_conditions(
                    location or {},
                    future_month,
                )
                if location
                else (
                    MONTHLY_AVG_WIND[future_month],
                    MONTHLY_AVG_WAVES[future_month],
                    MONTHLY_AVG_WIND_DIR[future_month],
                )
            )
            if isinstance(fb_wind, tuple):
                wind_str = f"{fb_dir} {int(fb_wind[0])}-{int(fb_wind[1])} kt"
                wind_range = (float(fb_wind[0]), float(fb_wind[1]))
                wind_dir_day = fb_dir

        # --- Wave estimate ---
        wave_str = ""
        wave_range = None
        if day_period:
            marine_text = day_period.get("detailedForecast", "")
            sea_match = re.search(
                r"(?:seas?|waves?)\s*(?:around\s+)?(\d+)(?:\s*to\s*(\d+))?\s*(?:ft|feet|foot)",
                marine_text,
                re.IGNORECASE,
            )
            if sea_match:
                low_ft = float(sea_match.group(1))
                high_ft = float(sea_match.group(2)) if sea_match.group(2) else low_ft
                wave_range = (low_ft, high_ft)
                wave_str = f"{int(low_ft)}-{int(high_ft)} ft"

        if not wave_range and wind_range:
            est_wave_range = _estimate_wave_range_from_wind(wind_range)
            if est_wave_range:
                wave_range = est_wave_range
                wave_str = f"{int(est_wave_range[0])}-{int(est_wave_range[1])} ft"

        if location:
            if not wave_range:
                fb_wind_r, fb_wave_r, _ = get_fallback_conditions(
                    location, future_month
                )
                wave_str = f"{int(fb_wave_r[0])}-{int(fb_wave_r[1])} ft"
                wave_range = fb_wave_r
        else:
            if not wave_range:
                wave_avg = MONTHLY_AVG_WAVES.get(future_month, (1, 3))
                wave_str = f"{int(wave_avg[0])}-{int(wave_avg[1])} ft"
                wave_range = wave_avg

        # --- Region + water temperature context ---
        if _monthly_temps is not None:
            future_water_temp = float(_monthly_temps[future_month])
        else:
            future_water_temp = float(MONTHLY_AVG_WATER_TEMP_F[future_month])

        # --- Fishability verdict ---
        if wind_range and wave_range:
            future_sunrise = None
            future_sunset = None
            future_solunar = None
            try:
                future_sunrise, future_sunset = _sun_times(
                    future, loc_lat, loc_lng, tz_name
                )
            except Exception:
                logger.debug("Sun times unavailable for %s", future, exc_info=True)
            try:
                future_solunar = compute_solunar_times(
                    future, loc_lat, loc_lng, tz_name
                )
            except Exception:
                logger.debug("Solunar times unavailable for %s", future, exc_info=True)
            _day_score = score_conditions(
                wind_range,
                wave_range,
                wind_dir=wind_dir_day,
                water_temp_f=future_water_temp,
                now=future.replace(hour=12, minute=0, second=0, microsecond=0),
                sunrise=future_sunrise,
                sunset=future_sunset,
                solunar=future_solunar,
                coast=coast or "east",
                fishing_types=fishing_types,
            )
            verdict = _day_score["verdict"]
            day_score = _day_score["score"]
        else:
            verdict = "Unknown"
            day_score = None

        # --- Top species for this day ---
        _day_cond_modifier = _build_conditions_modifier(
            wind_dir_day or None, wind_range, wave_range, 12, wind_coast
        )
        top_species_names: list[str] = []
        species_scores: list[tuple[str, float]] = []
        for sp in _coast_species:
            if (
                outlook_fish_region
                and "regions" in sp
                and outlook_fish_region not in sp["regions"]
            ):
                continue
            if not _profile_filter(sp["name"]):
                continue
            s = _score_species(
                sp,
                future_month,
                future_water_temp,
                _cond_modifier=_day_cond_modifier,
            )
            if s > 20:
                species_scores.append((sp["name"], s))
        species_scores.sort(key=_operator.itemgetter(1), reverse=True)
        top_species_names = [name for name, _ in species_scores[:5]]

        days.append(
            {
                "day": day_label,
                "date": date_label,
                "wind": wind_str,
                "waves": wave_str,
                "verdict": verdict,
                "score": day_score,
                "top_species": top_species_names,
            }
        )

    return days

# -- Spot-specific fishing tips based on conditions -------------------------

def build_spot_tips(
    wind_range: Optional[tuple[float, float]] = None,
    wave_range: Optional[tuple[float, float]] = None,
    wind_dir: str = "",
    hour: int = 12,
    month: int = 6,
    coast: str = "east",
    tide_state: str = "",
    fishing_types: Optional[list[str]] = None,
    experience: str = "",
    water_quality: Optional[dict[str, Any]] = None,
    recent_rain_in: Optional[float] = None,
) -> list[dict[str, str]]:
    """Generate 3-5 actionable fishing tips based on current conditions.

    Each tip has an 'icon' (emoji-free label), 'title', and 'detail'.
    When *fishing_types* is supplied, type-specific tips are prepended so
    they occupy the most prominent slots.

    If *water_quality* is supplied (from EPA WQP via services/datagov.py),
    dissolved-oxygen and enterococcus warnings are injected when thresholds
    are breached.
    """
    tips: list[dict[str, str]] = []

    # Recent-rain turbidity warning — heavy runoff muddies inlets and the surf
    # zone. Surfaced first since it changes bait/color/location strategy.
    if recent_rain_in is not None and recent_rain_in >= 0.5:
        if recent_rain_in >= 1.0:
            tips.append(
                {
                    "icon": "water",
                    "title": "Muddy Water After Heavy Rain",
                    "detail": (
                        f"About {recent_rain_in:.1f}\" of rain recently — inlets and the "
                        "surf zone will be stained and full of runoff. Fish scent-heavy "
                        "cut/natural bait over lures, work the cleaner edges where muddy "
                        "and clear water meet, and bump up to darker, high-vibration "
                        "presentations fish can find by feel."
                    ),
                }
            )
        else:
            tips.append(
                {
                    "icon": "water",
                    "title": "Some Runoff From Recent Rain",
                    "detail": (
                        f"Around {recent_rain_in:.1f}\" of recent rain may cloud the water "
                        "near inlets and creek mouths. Lean on scent and contrast — "
                        "chartreuse or dark baits — and target the clearer water just "
                        "outside the stained plume."
                    ),
                }
            )

    # Water quality warnings — injected first so they appear prominently.
    if water_quality and water_quality.get("available"):
        do_val = water_quality.get("do_mg_l")
        entero_flag = water_quality.get("enterococcus_flag", "")
        if do_val is not None and do_val < 5.0:
            tips.append(
                {
                    "icon": "warning",
                    "title": "Low Dissolved Oxygen Warning",
                    "detail": (
                        f"Measured DO is {do_val:.1f} mg/L — below the 5 mg/L threshold "
                        "where many species experience stress. Fish may be lethargic or "
                        "schooled in oxygenated shallows near inflows and wave-broken water. "
                        "Expect reduced feeding activity until conditions improve."
                    ),
                }
            )
        if entero_flag == "advisory":
            tips.append(
                {
                    "icon": "warning",
                    "title": "Beach Water Quality Advisory",
                    "detail": (
                        "Enterococcus bacteria levels exceed the EPA recreational threshold "
                        "(>104 CFU/100 mL) at nearby monitoring stations. Check local health "
                        "department advisories before wading or swimming. Fish consumption "
                        "advisories may also be in effect — verify with your state agency."
                    ),
                }
            )
    ft = set(fishing_types or [])

    # Wind-based tips
    avg_wind = 0.0
    if wind_range:
        avg_wind = (wind_range[0] + wind_range[1]) / 2

    if avg_wind > 20:
        tips.append(
            {
                "icon": "wind",
                "title": "Heavy Wind Strategy",
                "detail": "Use heavier sinkers (4-6 oz) to hold bottom. "
                "Fish the lee side of piers and jetties for calmer pockets. "
                "Shorten leaders to reduce tangles.",
            }
        )
    elif avg_wind > 12:
        tips.append(
            {
                "icon": "wind",
                "title": "Moderate Wind",
                "detail": "Standard 2-4 oz sinkers should hold. Wind chop stirs up bait — "
                "fish are often more active. Cast at an angle to the wind for better distance.",
            }
        )
    elif avg_wind < 6:
        tips.append(
            {
                "icon": "wind",
                "title": "Calm Conditions",
                "detail": "Light tackle shines today. Use lighter leaders and smaller presentations. "
                "Fish may be more line-shy in clear, calm water.",
            }
        )

    # Wave-based tips
    avg_wave = 0.0
    if wave_range:
        avg_wave = (wave_range[0] + wave_range[1]) / 2

    if avg_wave > 4:
        tips.append(
            {
                "icon": "waves",
                "title": "Heavy Surf",
                "detail": "Fish the troughs between sandbars where fish shelter from wave energy. "
                "Pyramid sinkers grip sandy bottoms better than egg sinkers in surf.",
            }
        )
    elif avg_wave > 2:
        tips.append(
            {
                "icon": "waves",
                "title": "Moderate Surf",
                "detail": "Cast beyond the breakers to the second sandbar. "
                "The stirred-up sand exposes sand fleas and crabs, drawing fish to feed.",
            }
        )
    elif avg_wave < 1.5:
        tips.append(
            {
                "icon": "waves",
                "title": "Flat Surf",
                "detail": "Fish closer to structure — jetties, pilings, and rocky outcrops. "
                "Clear water means lighter line and natural-colored baits work best.",
            }
        )

    # Tide-based tips
    if tide_state == "Rising":
        tips.append(
            {
                "icon": "tide",
                "title": "Rising Tide Tactics",
                "detail": "Incoming water pushes bait toward shore — position yourself "
                "at points where current funnels through cuts and inlets. "
                "Fish the first and second troughs as water fills them.",
            }
        )
    elif tide_state == "Falling":
        tips.append(
            {
                "icon": "tide",
                "title": "Falling Tide Tactics",
                "detail": "Outgoing water concentrates baitfish at channel mouths and drain points. "
                "Set up where water flows out from marshes and estuaries — "
                "predators stack up to ambush departing bait.",
            }
        )

    # Time-of-day tips
    if 5 <= hour <= 7:
        tips.append(
            {
                "icon": "time",
                "title": "Early Bird Advantage",
                "detail": "Dawn is prime time — get lines in the water before sunrise. "
                "Topwater lures and live bait under corks excel in the low-light bite window.",
            }
        )
    elif 17 <= hour <= 20:
        tips.append(
            {
                "icon": "time",
                "title": "Evening Bite",
                "detail": "Fish feed aggressively before dark. Switch to darker-colored lures as "
                "light fades — fish rely more on silhouette and vibration at dusk.",
            }
        )
    elif 10 <= hour <= 14:
        tips.append(
            {
                "icon": "time",
                "title": "Midday Approach",
                "detail": "Fish deeper structure and shaded areas during bright sun. "
                "Piers cast shadows that attract baitfish — focus on the shadow line.",
            }
        )

    # Seasonal tips
    if month in (11, 12, 1, 2):
        tips.append(
            {
                "icon": "season",
                "title": "Cold Water Tips",
                "detail": "Slow your presentation — cold fish won't chase fast baits. "
                "Fish the warmest part of the day (10 AM - 3 PM) when water warms slightly. "
                "Bottom rigs with cut bait outperform artificials in winter.",
            }
        )
    elif month in (6, 7, 8):
        tips.append(
            {
                "icon": "season",
                "title": "Summer Patterns",
                "detail": "Early morning and late evening are most productive — avoid the midday heat. "
                "Live bait stays livelier in a bucket with an aerator. "
                "Night fishing produces excellent catches in warm months.",
            }
        )

    # --- Fishing-type-specific tips (prepended to be prominent) ---
    type_tips: list[dict[str, str]] = []

    if "bridge" in ft:
        avg_wind = ((wind_range[0] + wind_range[1]) / 2) if wind_range else 0
        if tide_state == "Rising":
            type_tips.append(
                {
                    "icon": "tide",
                    "title": "Bridge: Incoming Tide Position",
                    "detail": "Set up on the upcurrent side of the bridge. Cast into the current and "
                    "let bait sweep through the shadow line between pilings — snook and "
                    "tarpon stage in the current seam right at the light/dark boundary.",
                }
            )
        elif tide_state == "Falling":
            type_tips.append(
                {
                    "icon": "tide",
                    "title": "Bridge: Outgoing Tide Position",
                    "detail": "Move to the downcurrent side of pilings. Sheepshead, jacks, and "
                    "snapper hold here waiting for bait swept off the bridge structure. "
                    "A live shrimp or small jig bouncing through is hard to beat.",
                }
            )
        else:
            type_tips.append(
                {
                    "icon": "tide",
                    "title": "Bridge: Slack Tide Tactics",
                    "detail": "Slack tide around bridges often slows action; fish the shadow lines "
                    "under lights with a slow-sinking live bait. Night bridge fishing "
                    "under LED lights is premier for snook — target the edge of the light cone.",
                }
            )

    if "jetty" in ft:
        avg_wave = ((wave_range[0] + wave_range[1]) / 2) if wave_range else 0
        if avg_wave >= 2:
            type_tips.append(
                {
                    "icon": "waves",
                    "title": "Jetty: Work the Wash",
                    "detail": "Wave surge on the jetty rocks activates feeding — "
                    "sheepshead and tautog move tight to the base as crabs and "
                    "worms get dislodged. Fish a knocker rig right against the rocks "
                    "and be patient: let the bait sit.",
                }
            )
        elif tide_state in ("Rising", "Falling"):
            type_tips.append(
                {
                    "icon": "tide",
                    "title": "Jetty Tip Position",
                    "detail": "The tip of the jetty on a moving tide is the prime station. "
                    "Bait schools funnel through the inlet mouth — predators (cobia, "
                    "tarpon, jacks, striped bass) stack up here to intercept them. "
                    "Bring a heavy jig or live bait under a float.",
                }
            )

    if "wade" in ft:
        avg_wind = ((wind_range[0] + wind_range[1]) / 2) if wind_range else 0
        if avg_wind > 12:
            type_tips.append(
                {
                    "icon": "wind",
                    "title": "Wade: Wind and Clarity",
                    "detail": "Wind chop reduces water clarity on the flats — switch from "
                    "sight-fishing to scented soft plastics or live shrimp under a "
                    "popping cork. Position yourself with the wind at your back to "
                    "maximize casting distance.",
                }
            )
        else:
            type_tips.append(
                {
                    "icon": "waves",
                    "title": "Wade: Sight-Fishing Conditions",
                    "detail": "Low wind means excellent flat visibility. "
                    "Wear neutral colors and move slowly — push minimal water. "
                    "Look for tailing reds, wakes, push-waves, and nervous water "
                    "along grass edges and shell bars.",
                }
            )

    if "kayak" in ft:
        avg_wind = ((wind_range[0] + wind_range[1]) / 2) if wind_range else 0
        if avg_wind > 12:
            type_tips.append(
                {
                    "icon": "wind",
                    "title": "Kayak: Wind Strategy",
                    "detail": "Paddle into the wind first so you have an easier return trip "
                    "when you're fatigued. Hug the shoreline to stay in wind shadow "
                    "and avoid open-water crossings. Consider anchoring with a "
                    "stake-out pole in protected coves.",
                }
            )
        else:
            type_tips.append(
                {
                    "icon": "tide",
                    "title": "Kayak: Drift Tactics",
                    "detail": "Use the tidal current to drift-fish structure without burning "
                    "energy paddling. Anchor upcurrent of your target, cast downcurrent, "
                    "and let bait sweep naturally through the zone. Back-country "
                    "creek mouths on a falling tide concentrate fish.",
                }
            )

    if "fly" in ft:
        avg_wind = ((wind_range[0] + wind_range[1]) / 2) if wind_range else 0
        if avg_wind > 15:
            type_tips.append(
                {
                    "icon": "wind",
                    "title": "Fly: High Wind Adjustments",
                    "detail": "Shorten your leader to 7-9 ft in high wind so the fly turns "
                    "over cleanly. Use a heavier fly (weighted Clouser) that punches "
                    "through wind. Cast at 45° to the wind rather than directly into "
                    "or with it — the crosswind cast is most efficient.",
                }
            )
        else:
            type_tips.append(
                {
                    "icon": "waves",
                    "title": "Fly: Sight-Casting Setup",
                    "detail": "Ideal conditions for sight-fishing. Strip out 30-40 ft of fly "
                    "line before wading in — false casts stir up bottom. Pre-load "
                    "your cast and lead the fish by 3-6 ft so the fly sinks to eye "
                    "level before beginning your strip retrieve.",
                }
            )

    if "charter" in ft:
        avg_wave = ((wave_range[0] + wave_range[1]) / 2) if wave_range else 0
        if avg_wave >= 4:
            type_tips.append(
                {
                    "icon": "waves",
                    "title": "Charter: Rough Weather Tips",
                    "detail": "Take seasick medication the night before (Meclizine / Bonine). "
                    "Stay on deck, keep eyes on the horizon, and stay hydrated. "
                    "Stand near the stern where motion is least. The captain may "
                    "adjust plans to sheltered nearshore structure on rough days.",
                }
            )
        else:
            type_tips.append(
                {
                    "icon": "tide",
                    "title": "Charter: Maximize Your Trip",
                    "detail": "Follow the mate's lead on hook baiting and technique — they "
                    "know what's working. Ask the captain what species are running "
                    "before you leave the dock. Tip the mate well: they rig, bait, "
                    "gaff, and fillet fish all day.",
                }
            )

    # ── Experience-level tip ────────────────────────────────────────────────
    if experience == "beginner":
        tips.append(
            {
                "icon": "beginner",
                "title": "Getting Started",
                "detail": "Start with a simple bottom rig (fish-finder or hi-lo) baited with "
                "cut shrimp or sand fleas. Cast straight out, set the rod in a "
                "spike, and keep your drag slightly loose so fish can run without "
                "breaking off. Circle hooks set themselves — don't jerk the rod, "
                "just reel steadily when you feel weight.",
            }
        )
    elif experience == "experienced":
        tips.append(
            {
                "icon": "advanced",
                "title": "Read the Water",
                "detail": "Look for color changes, current seams, nervous bait, birds diving, "
                "or dark patches indicating structure. The best spot on any beach "
                "or pier shifts hourly with current and light — keep moving until "
                "you find active fish, then stay put.",
            }
        )

    # Prepend type-specific tips, then fill remaining slots with generic tips
    return (type_tips + tips)[: max(5, len(type_tips) + 2)]

def build_bite_alerts(
    verdict: str,
    species: list[dict[str, Any]],
    pressure: Optional[dict[str, Any]] = None,
    tide_state: str = "",
) -> list[dict[str, str]]:
    """Generate bite alert notifications when conditions are especially good."""
    alerts: list[dict[str, str]] = []

    # Hot species alert
    hot_species = [sp["name"] for sp in species if sp.get("activity") == "Hot"]
    if len(hot_species) == 1:
        alerts.append(
            {
                "type": "hot",
                "title": f"{hot_species[0]} is on fire!",
                "message": "Conditions are dialed in for this species. Get your lines in the water.",
            }
        )

    # Excellent conditions alert
    if verdict == "Excellent":
        alerts.append(
            {
                "type": "excellent",
                "title": "Excellent fishing day!",
                "message": "Wind, waves, and temperatures are all in the sweet spot. Don't miss this one.",
            }
        )

    # Falling pressure trigger
    if pressure and "falling" in pressure.get("trend", "").lower():
        p_val = pressure.get("pressure_mb", 0)
        if p_val and float(p_val) < 1010:
            alerts.append(
                {
                    "type": "pressure",
                    "title": "Pre-front feeding window",
                    "message": "Barometric pressure is dropping below 1010 mb — fish often feed aggressively before incoming weather.",
                }
            )

    return alerts[:3]

def pick_best_fishing_day(
    today_verdict: str,
    outlook: list[dict[str, Any]],
    today_score: Optional[int] = None,
) -> dict[str, str]:
    """Analyze today and 3-day outlook to recommend the best day to fish.

    Ranks by verdict tier first (so an Excellent day always beats a Good one),
    then by the 0-100 go/no-go score within a tier — so two "Good" days are
    separated by their actual scores — and finally by active-species count.

    Returns a dict with 'best_day', 'verdict', and 'recommendation'.
    """
    verdict_scores = {
        "Excellent": 5,
        "Good": 4,
        "Fair": 3,
        "Challenging": 2,
        "Poor": 1,
        "Unknown": 2,
    }

    def _combined(verdict: str, score: Optional[int], n_species: int) -> float:
        # Tier dominates (×1000); numeric score breaks ties within a tier;
        # species count is the final, smallest tiebreaker.
        tier = verdict_scores.get(verdict, 2) * 1000
        num = score if isinstance(score, (int, float)) else verdict_scores.get(verdict, 2) * 20
        return tier + num + min(n_species, 5) * 0.1

    best_day = "Today"
    best_tier = verdict_scores.get(today_verdict, 2)
    best_score = _combined(today_verdict, today_score, 0)
    best_verdict = today_verdict

    for day in outlook:
        v = day.get("verdict", "Unknown")
        s = _combined(v, day.get("score"), len(day.get("top_species", [])))
        if s > best_score:
            best_score = s
            best_tier = verdict_scores.get(v, 2)
            best_day = day["day"]
            best_verdict = v

    if best_day == "Today":
        if best_tier >= 4:
            recommendation = "Today looks great — get out there!"
        elif best_tier >= 3:
            recommendation = "Decent day today, but conditions are fishable."
        else:
            recommendation = "Tough day today. Check back tomorrow."
    else:
        if best_tier >= 4:
            recommendation = f"{best_day} has the best forecast — plan your trip then."
        else:
            recommendation = (
                f"{best_day} looks slightly better, but all days are similar."
            )

    return {
        "best_day": best_day,
        "verdict": best_verdict,
        "recommendation": recommendation,
    }

def build_gear_checklist(
    species: list[dict[str, Any]],
    wind_range: Optional[tuple[float, float]] = None,
    wave_range: Optional[tuple[float, float]] = None,
    hour: int = 12,
    water_temp: float = 65.0,
    weather: Optional[dict[str, Any]] = None,
    fishing_types: Optional[list[str]] = None,
) -> list[dict[str, str]]:
    """Generate a conditions-aware packing list for a fishing trip."""
    items: list[dict[str, str]] = []
    categories_seen: set[str] = set()
    ft = set(fishing_types or [])

    def _add(category: str, item: str, reason: str = "") -> None:
        key = f"{category}:{item}"
        if key not in categories_seen:
            categories_seen.add(key)
            items.append({"category": category, "item": item, "reason": reason})

    # ---- Fishing-type-specific essentials (shown first) ----
    if "kayak" in ft:
        _add("Kayak", "PFD (life jacket)", "Required by law when underway")
        _add("Kayak", "Paddle + spare paddle leash", "")
        _add("Kayak", "Anchor trolley or stake-out pole", "Keep position in current")
        _add("Kayak", "Waterproof dry bag", "Protect phone, keys, and food")
        _add("Kayak", "Rod holders (2 minimum)", "Hands-free paddling between spots")
        _add("Kayak", "Bilge pump or sponge", "Bail water quickly")
        _add("Kayak", "VHF radio or waterproof phone case", "Emergency communication")

    if "jetty" in ft:
        _add(
            "Jetty",
            "Cleated or studded wading boots",
            "Grip on wet, algae-covered rocks",
        )
        _add("Jetty", "Wading staff or walking stick", "Balance on uneven rock surface")
        _add("Jetty", "Rod holder spike", "Free hands while watching the surf")

    if "wade" in ft:
        _add(
            "Wade",
            "Wading boots with felt or rubber soles",
            "Grip on slippery substrate",
        )
        _add("Wade", "Waders or wetsuit (water temp dependent)", "")
        _add("Wade", "Wading staff", "Stability in current")
        _add("Wade", "Polarized sunglasses", "Essential for sight-fishing")
        _add("Wade", "Sun buff / neck gaiter", "Protection while scanning flats")
        _add("Wade", "Stripping basket or waist pack", "Keep fly line off water")

    if "fly" in ft:
        _add("Fly", "Fly rod (8–12 wt depending on target species)", "")
        _add("Fly", "Fly line matched to rod weight", "")
        _add("Fly", "Fluorocarbon tippet (various sizes)", "")
        _add("Fly", "Fly box with appropriate patterns", "")
        _add("Fly", "Stripping basket", "Manage line in wind and current")
        _add("Fly", "Polarized sunglasses", "Sight-fishing essential")
        _add("Fly", "Line cleaner/conditioner", "Maintain floating line")

    if "bridge" in ft:
        _add(
            "Bridge",
            "Drop net or cast net for landing fish",
            "No beach to walk fish in",
        )
        _add("Bridge", "Heavy bucktail jigs or live bait rigs", "Current fishing")
        _add("Bridge", "Headlamp", "Night bridge fishing is highly productive")
        _add(
            "Bridge",
            "Reflective vest or light-colored clothing",
            "Visibility to vehicles",
        )

    if "charter" in ft:
        _add(
            "Charter",
            "Motion sickness medication (taken night before)",
            "Prevention is key",
        )
        _add("Charter", "Non-slip deck shoes", "Required on most charter boats")
        _add("Charter", "Waterproof sunscreen SPF 50+", "Offshore sun is intense")
        _add("Charter", "Polarized sunglasses", "Spot color changes and weed lines")
        _add("Charter", "Cooler / ice for catch", "Coordinate with captain beforehand")
        _add("Charter", "Light rain jacket", "Offshore weather changes quickly")

    if "offshore" in ft and "charter" not in ft:
        _add("Offshore", "Heavy-duty tackle (50–80 lb class)", "")
        _add("Offshore", "Safety harness for heavy trolling", "Prevent rod pull")
        _add("Offshore", "Offshore first aid kit", "Distance from help")
        _add("Offshore", "VHF radio and EPIRB / PLB", "Required safety equipment")

    # ---- Always bring ----
    _add("Essentials", "Rod & reel (medium-heavy for surf)", "")
    _add("Essentials", "Tackle box with hooks, sinkers, swivels", "")
    _add("Essentials", "Bait cooler with ice", "")
    _add("Essentials", "Pliers & line cutter", "")
    _add("Essentials", "Fishing license", "Required in most states")

    # ---- Conditions-based ----
    max_wind = wind_range[1] if wind_range else 10
    max_wave = wave_range[1] if wave_range else 2

    if max_wind >= 15:
        _add(
            "Conditions", "Heavy sinkers (4-6 oz)", "Strong wind requires extra weight"
        )
        _add("Conditions", "Sand spike or rod holder", "Keep rods secure in high wind")

    if max_wave >= 4:
        _add("Conditions", "Waders or waterproof boots", "Heavy surf will splash")
        _add(
            "Conditions", "Extra sinkers (pyramid style)", "Holds bottom in rough surf"
        )

    if hour < 6 or hour >= 19:
        _add("Conditions", "Headlamp (red light mode)", "Preserve night vision")
        _add(
            "Conditions",
            "Glow sticks or light-up bobbers",
            "Track your line in the dark",
        )

    if 10 <= hour <= 16:
        _add("Conditions", "Sunscreen SPF 50+", "Peak UV hours")
        _add("Conditions", "Polarized sunglasses", "Reduce glare, spot fish")
        _add("Conditions", "Hat with brim or neck flap", "Sun protection")

    # ---- Weather-based ----
    air_temp = None
    if weather:
        air_temp = weather.get("air_temp_f")
    if air_temp is not None:
        if float(air_temp) < 50:
            _add("Weather", "Layered clothing / thermal base", f"Air temp {air_temp}°F")
            _add("Weather", "Hand warmers", "Keep fingers nimble for knots")
            _add("Weather", "Thermos with hot drink", "Stay warm on the pier")
        elif float(air_temp) > 85:
            _add(
                "Weather",
                "Extra water (1 gal minimum)",
                f"Air temp {air_temp}°F — stay hydrated",
            )
            _add("Weather", "Cooling towel", "Beat the heat")

    # ---- Species-based ----
    has_shark = any("shark" in sp.get("name", "").lower() for sp in species[:10])
    has_king = any("king" in sp.get("name", "").lower() for sp in species[:10])
    has_flounder = any("flounder" in sp.get("name", "").lower() for sp in species[:10])

    if has_shark:
        _add("Species", "Wire leader (single-strand)", "Shark teeth cut mono/fluoro")
        _add("Species", "Heavy-duty dehooking tool", "Safe shark handling")

    if has_king:
        _add(
            "Species", "Wire or heavy fluoro leader (60+ lb)", "Kings have sharp teeth"
        )
        _add("Species", "Stinger rig components", "Standard for kingfish")

    if has_flounder:
        _add("Species", "Bucktail jig (white/chartreuse)", "Top flounder lure")

    # ---- Convenience ----
    _add("Convenience", "5-gallon bucket", "Bait storage, seat, catch bucket")
    _add("Convenience", "Towel / rags", "Clean hands between baiting")
    _add("Convenience", "Trash bag", "Leave no trace")

    return items

def build_conditions_explainer(
    wind_range: Optional[tuple[float, float]] = None,
    wave_range: Optional[tuple[float, float]] = None,
    wind_dir: Optional[str] = None,
    water_temp: float = 65.0,
    pressure: Optional[dict[str, Any]] = None,
    tide_state: str = "",
    coast: str = "east",
) -> list[dict[str, str]]:
    """Translate raw marine conditions into fishing-relevant plain English."""
    bullets: list[dict[str, str]] = []

    # Wind analysis
    if wind_range:
        avg_wind = (wind_range[0] + wind_range[1]) / 2
        if avg_wind <= 8:
            bullets.append(
                {
                    "label": "Wind",
                    "text": "Light winds make for calm conditions — great for float rigs and sight casting.",
                    "impact": "positive",
                }
            )
        elif avg_wind <= 15:
            bullets.append(
                {
                    "label": "Wind",
                    "text": "Moderate breeze stirs up bait and adds turbidity. Good for surf fishing — fish move closer to feed.",
                    "impact": "positive",
                }
            )
        elif avg_wind <= 22:
            bullets.append(
                {
                    "label": "Wind",
                    "text": "Strong winds make casting difficult and waves choppy. Use heavier sinkers and shorter leaders.",
                    "impact": "neutral",
                }
            )
        else:
            bullets.append(
                {
                    "label": "Wind",
                    "text": "Near-gale conditions — tough fishing day. If you go, fish sheltered areas with heavy tackle.",
                    "impact": "negative",
                }
            )

    # Wind direction meaning
    if wind_dir:
        onshore_dirs = {
            "east": {"E", "ENE", "ESE", "SE", "NE"},
            "west": {"W", "WNW", "WSW", "SW", "NW"},
        }
        dirs = onshore_dirs.get(coast, onshore_dirs["east"])
        if wind_dir in dirs:
            bullets.append(
                {
                    "label": "Direction",
                    "text": f"{wind_dir} wind pushes bait and murky water shoreward — predators follow to feed along the beach.",
                    "impact": "positive",
                }
            )
        else:
            bullets.append(
                {
                    "label": "Direction",
                    "text": f"{wind_dir} (offshore) wind flattens the surf and clears the water. Better for sight fishing, tougher for surf bait fishing.",
                    "impact": "neutral",
                }
            )

    # Wave analysis
    if wave_range:
        avg_wave = (wave_range[0] + wave_range[1]) / 2
        if avg_wave <= 2:
            bullets.append(
                {
                    "label": "Waves",
                    "text": "Flat to slight seas — ideal for pier fishing and wading. Fish may be less active in clear, calm water.",
                    "impact": "neutral",
                }
            )
        elif avg_wave <= 4:
            bullets.append(
                {
                    "label": "Waves",
                    "text": "Moderate surf churns up sand fleas, crabs, and baitfish — prime conditions for the surf zone.",
                    "impact": "positive",
                }
            )
        else:
            bullets.append(
                {
                    "label": "Waves",
                    "text": "Heavy surf concentrates bait in troughs between sandbars. Big fish feed hard but conditions are challenging.",
                    "impact": "neutral",
                }
            )

    # Pressure
    if pressure:
        trend = pressure.get("trend", "").lower()
        if "falling" in trend:
            bullets.append(
                {
                    "label": "Pressure",
                    "text": "Falling pressure triggers a feeding frenzy — fish sense the change and eat aggressively before a front.",
                    "impact": "positive",
                }
            )
        elif "rising" in trend:
            bullets.append(
                {
                    "label": "Pressure",
                    "text": "Rising pressure often means post-front clear skies. Fishing may be slow at first but improves as it stabilizes.",
                    "impact": "neutral",
                }
            )

    # Tide state
    if tide_state:
        if tide_state == "Rising":
            bullets.append(
                {
                    "label": "Tide",
                    "text": "Incoming tide floods channels and flats with bait — one of the best times to fish from shore.",
                    "impact": "positive",
                }
            )
        else:
            bullets.append(
                {
                    "label": "Tide",
                    "text": "Outgoing tide funnels bait through inlets and cuts — position yourself where current concentrates.",
                    "impact": "positive",
                }
            )

    return bullets[:5]

def build_safety_checklist(
    wind_range: Optional[tuple[float, float]] = None,
    wave_range: Optional[tuple[float, float]] = None,
    hour: int = 12,
    alerts: Optional[list[dict[str, str]]] = None,
    fishing_types: Optional[list[str]] = None,
    water_temp: Optional[float] = None,
) -> list[dict[str, str]]:
    """Build a conditions-aware safety checklist for surf/pier fishing."""
    items: list[dict[str, str]] = []
    ft = set(fishing_types or [])

    # Always show basics
    items.append(
        {
            "text": "Tell someone your fishing plan and expected return time",
            "icon": "info",
        }
    )

    max_wave = (
        (wave_range[1] if isinstance(wave_range, tuple) else 3) if wave_range else 0
    )
    max_wind = (
        (wind_range[1] if isinstance(wind_range, tuple) else 10) if wind_range else 0
    )

    # --- Kayak-specific safety (highest priority) ---
    if "kayak" in ft:
        if max_wind >= 20:
            items.append(
                {
                    "text": "Small craft advisory conditions — do not launch a kayak in 20+ kt winds",
                    "icon": "warning",
                }
            )
        elif max_wind >= 15:
            items.append(
                {
                    "text": "Elevated wind for kayak — stay close to shore and have a shore-exit plan",
                    "icon": "caution",
                }
            )
        if max_wave >= 3:
            items.append(
                {
                    "text": "Rough chop for a kayak — consider postponing or fishing protected water",
                    "icon": "caution",
                }
            )
        items.append(
            {
                "text": "Wear your PFD at all times when on the water — required by law when underway",
                "icon": "info",
            }
        )

    # --- Jetty/rock-specific safety ---
    if "jetty" in ft:
        if max_wave >= 4:
            items.append(
                {
                    "text": "Dangerous wave surge on jetty rocks — stay off exposed structure, waves may wash over",
                    "icon": "warning",
                }
            )
        elif max_wave >= 2:
            items.append(
                {
                    "text": "Wave surge on jetty — wear cleated/gripped footwear and never turn your back to the water",
                    "icon": "caution",
                }
            )

    # --- Wade fishing-specific safety ---
    if "wade" in ft:
        if water_temp is not None and water_temp < 55:
            items.append(
                {
                    "text": f"Cold water wading ({water_temp:.0f}°F) — cold shock risk; use a wading staff and stay shallow",
                    "icon": "warning",
                }
            )
        elif water_temp is not None and water_temp < 65:
            items.append(
                {
                    "text": f"Cool water wading ({water_temp:.0f}°F) — neoprene waders recommended",
                    "icon": "caution",
                }
            )
        items.append(
            {
                "text": "Never wade alone in tidal current — always have a buddy or shore spotter",
                "icon": "info",
            }
        )

    # --- Wave-based warnings (surf/general) ---
    if wave_range:
        if max_wave >= 6:
            items.append(
                {
                    "text": "High surf advisory — stay off jetties and rock structures",
                    "icon": "warning",
                }
            )
            items.append(
                {
                    "text": "Rip current risk is elevated — never wade beyond knee depth",
                    "icon": "warning",
                }
            )
        elif max_wave >= 4:
            items.append(
                {
                    "text": "Moderate surf — watch for sneaker waves and keep gear secured",
                    "icon": "caution",
                }
            )

        # Heavy surf PFD reminder for shore/structure anglers — being swept off
        # rocks/jetties or by sneaker waves is a leading surf-fishing fatality
        # cause. Kayakers already get a dedicated PFD reminder above.
        if max_wave >= 5 and "kayak" not in ft:
            items.append(
                {
                    "text": "Heavy surf — wear an inflatable PFD/life vest, especially on "
                    "jetties or rocks where a wave can sweep you in",
                    "icon": "warning",
                }
            )

    # --- Wind-based ---
    if wind_range:
        if max_wind >= 25:
            items.append(
                {
                    "text": "Gale-force winds — consider postponing your trip",
                    "icon": "warning",
                }
            )
        elif max_wind >= 15 and "kayak" not in ft:
            items.append(
                {
                    "text": "Strong winds — secure coolers/gear and watch for blown tackle",
                    "icon": "caution",
                }
            )

    # --- Fly fishing cast-safety note ---
    if "fly" in ft and max_wind >= 15:
        items.append(
            {
                "text": "High wind makes fly casting difficult and dangerous — watch your backcast for bystanders",
                "icon": "caution",
            }
        )

    # --- Bridge/night fishing ---
    if "bridge" in ft and (hour < 6 or hour >= 20):
        items.append(
            {
                "text": "Bridge night fishing — use a headlamp, wear light-colored clothing, and stay behind railings",
                "icon": "info",
            }
        )

    # Time-based
    if hour < 6 or hour >= 20:
        items.append(
            {
                "text": "Fishing in the dark — bring a headlamp and reflective gear",
                "icon": "info",
            }
        )
    if 10 <= hour <= 16:
        items.append(
            {
                "text": "Peak sun hours — wear sunscreen (SPF 50+), hat, and polarized glasses",
                "icon": "info",
            }
        )

    # Weather alerts
    if alerts:
        for alert in alerts:
            if "thunderstorm" in alert.get("event", "").lower():
                items.append(
                    {
                        "text": "Thunderstorm warning active — leave pier/water immediately if lightning is within 10 miles",
                        "icon": "warning",
                    }
                )
                break

    # General
    items.append(
        {
            "text": "Bring plenty of water — dehydration reduces reaction time",
            "icon": "info",
        }
    )
    items.append(
        {
            "text": "Know the emergency number for your pier/beach (usually posted at entrance)",
            "icon": "info",
        }
    )

    return items

def _uv_category(uv_index: float) -> dict[str, str]:
    if uv_index <= 2:
        return {
            "level": "Low",
            "advice": "Minimal sun risk; sunglasses still recommended.",
        }
    if uv_index <= 7:
        return {
            "level": "Moderate to High",
            "advice": "Use SPF 30+, hat, and lightweight sun shirt.",
        }
    return {
        "level": "Very High to Extreme",
        "advice": "Limit direct midday exposure and reapply SPF often.",
    }

def _estimate_uv_index(
    now: datetime,
    sunrise: Optional[datetime],
    sunset: Optional[datetime],
    lat: float = _LAT,
) -> float:
    if not sunrise or not sunset or now < sunrise or now > sunset:
        return 0.0
    daylight = max((sunset - sunrise).total_seconds(), 1)
    elapsed = (now - sunrise).total_seconds()
    pct = elapsed / daylight
    # Scale peak UV by latitude: lower latitudes (closer to equator) receive
    # more direct sunlight and therefore higher UV intensity at solar noon.
    peak = max(3.0, 11.0 - 0.1 * abs(lat))
    uv = peak * max(0.0, 1 - (2 * pct - 1) ** 2)
    return round(uv, 1)

def recompute_current_uv(location: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Compute UV index for the current time at the given location.

    Intended for refreshing stale cached UV values at page-render time so the
    displayed UV always reflects the *current* sun position for the selected
    location, not the moment the forecast was originally generated.
    """
    tz_name = (location or {}).get("timezone", "America/New_York")
    tz = _safe_zone(tz_name)
    now = datetime.now(tz)
    lat = (location or {}).get("lat", _LAT)
    lng = (location or {}).get("lng", _LNG)
    try:
        sunrise, sunset = _sun_times(now, lat, lng, tz_name)
    except Exception:
        sunrise, sunset = None, None
    uv_index = _estimate_uv_index(now, sunrise, sunset, lat)
    return {"index": uv_index, **_uv_category(uv_index)}

def _rip_risk_from_conditions(
    wave_range: Optional[tuple[float, float]], wind_range: Optional[tuple[float, float]]
) -> dict[str, str]:
    wave_max = wave_range[1] if wave_range else 0.0
    wind_max = wind_range[1] if wind_range else 0.0
    if wave_max >= 5 or wind_max >= 20:
        return {
            "level": "High",
            "guidance": "Strong rip current risk. Stay out of the surf and fish from stable structure.",
        }
    if wave_max >= 3 or wind_max >= 14:
        return {
            "level": "Moderate",
            "guidance": "Rip currents possible, especially near piers/jetties. Keep exits in sight.",
        }
    return {
        "level": "Low",
        "guidance": "Risk still exists near piers and cuts. Never fish or swim alone in surf.",
    }

def _dew_point_f(
    temp_f: Optional[float], humidity_pct: Optional[float]
) -> Optional[float]:
    if temp_f is None or humidity_pct is None or humidity_pct <= 0:
        return None
    temp_c = (temp_f - 32) * 5 / 9
    rh = max(1.0, min(float(humidity_pct), 100.0))
    a = 17.27
    b = 237.7
    alpha = ((a * temp_c) / (b + temp_c)) + math.log(rh / 100.0)
    dew_c = (b * alpha) / (a - alpha)
    return round(dew_c * 9 / 5 + 32, 1)

def _heat_index_f(
    temp_f: Optional[float], humidity_pct: Optional[float]
) -> Optional[float]:
    if temp_f is None or humidity_pct is None:
        return None
    t = float(temp_f)
    rh = float(humidity_pct)
    if t < 80 or rh < 40:
        return None
    hi = (
        -42.379
        + 2.04901523 * t
        + 10.14333127 * rh
        - 0.22475541 * t * rh
        - 0.00683783 * t**2
        - 0.05481717 * rh**2
        + 0.00122874 * t**2 * rh
        + 0.00085282 * t * rh**2
        - 0.00000199 * t**2 * rh**2
    )
    return round(hi, 1)

def _wind_chill_f(temp_f: Optional[float], wind_kt: Optional[float]) -> Optional[float]:
    if temp_f is None or wind_kt is None:
        return None
    t = float(temp_f)
    wind_mph = float(wind_kt) * _KT_TO_MPH
    if t > 50 or wind_mph < 3:
        return None
    wc = 35.74 + 0.6215 * t - 35.75 * (wind_mph**0.16) + 0.4275 * t * (wind_mph**0.16)
    return round(wc, 1)

def _build_pier_info(location: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Return location-aware planning details without requiring APIs."""
    loc = location or {}
    state = loc.get("state", "")
    coast_region = loc.get("conditions_region", "")
    default_hours = "Open daily; check seasonal maintenance closures"
    if state in {"FL", "HI", "CA"}:
        default_hours = "Sunrise to sunset (many piers extend hours in summer)"
    elif state in {"NJ", "NY", "MA", "RI", "CT"}:
        default_hours = "Dawn to dusk; winter weather closures possible"

    amenities = [
        "Restrooms nearby",
        "Public parking",
        "Fish-cleaning area varies by pier",
    ]
    if coast_region.startswith("pacific"):
        amenities.append("Kelp/rock structure nearby")
    elif coast_region.startswith("gulf"):
        amenities.append("Baitfish schools common near lights and passes")
    else:
        amenities.append("Tidal cuts and sandbars nearby")

    return {
        "hours": loc.get("pier_hours", default_hours),
        "fees": loc.get(
            "pier_fees", "Fees vary by municipality; verify before travel."
        ),
        "contact": loc.get(
            "pier_contact", f"{loc.get('name', 'Local')} parks/marina office"
        ),
        "amenities": loc.get("amenities", amenities),
        "rules": loc.get(
            "rules",
            ["No alcohol on pier", "Respect casting lanes", "Check local drone policy"],
        ),
    }

def _build_education_cards(
    profile: dict[str, Any],
    uv: dict[str, Any],
    rip_current_risk: dict[str, str],
) -> list[dict[str, str]]:
    """Build educational/safety cards tailored to setup/profile choices."""
    experience = (profile or {}).get("experience", "")
    fishing_types = set((profile or {}).get("fishing_types") or [])
    cards = [
        {
            "title": "Twilight windows",
            "text": "Civil twilight often provides low-light feeding before sunrise and after sunset; many surf bites improve in this window.",
        },
        {
            "title": "UV safety",
            "text": f"UV is {uv.get('level', 'Unknown')} today. {uv.get('advice', 'Use sun protection and hydration breaks.')}",
        },
        {
            "title": "Rip-current awareness",
            "text": f"Current risk: {rip_current_risk.get('level', 'Unknown')}. {rip_current_risk.get('guidance', '')}",
        },
        {
            "title": "Catch-and-release",
            "text": "Use wet hands, keep fish in water when possible, and support the belly to reduce post-release stress.",
        },
    ]
    if experience == "beginner":
        cards.append(
            {
                "title": "Beginner tip",
                "text": "Start with bottom rigs and short casts. Focus on reading current seams before trying long-distance casting.",
            }
        )
    if "pier" in fishing_types:
        cards.append(
            {
                "title": "Pier etiquette",
                "text": "Call out when casting, avoid crossing lines, and net fish quickly to keep traffic moving safely.",
            }
        )
    if "jetty" in fishing_types:
        cards.append(
            {
                "title": "Jetty safety",
                "text": "Never turn your back to the water on jetty rocks — sneaker waves are the #1 hazard. Fish the incoming tide for best access to bait schools pushed against the rocks.",
            }
        )
        cards.append(
            {
                "title": "Jetty technique",
                "text": "Work the downcurrent side of the jetty tip at tide changes. Sheepshead and tautog hold tight to structure; drop vertically with a knocker or Carolina rig for best results.",
            }
        )
    if "bridge" in fishing_types:
        cards.append(
            {
                "title": "Bridge fishing tips",
                "text": "Position on the downcurrent side of pilings — fish face into current and ambush bait swept around the structure. Night fishing under bridge lights is premier for snook and striped bass in season.",
            }
        )
        cards.append(
            {
                "title": "Bridge current reading",
                "text": "The bite at bridges is most reliable during the first hour of a moving tide. Slack water often kills the bite; plan your session around current change windows.",
            }
        )
    if "wade" in fishing_types:
        cards.append(
            {
                "title": "Wade fishing approach",
                "text": "Move slowly and push minimal water — wade fish are highly aware of pressure waves. Fish into the sun when possible so your shadow falls behind you, not over fish.",
            }
        )
        cards.append(
            {
                "title": "Sight-fishing basics",
                "text": "Look for wakes, tails, push-wakes, and nervous water. Polarized glasses are non-negotiable. Present your bait ahead of the fish's travel path — never cast directly at the fish.",
            }
        )
    if "kayak" in fishing_types:
        cards.append(
            {
                "title": "Kayak fishing spots",
                "text": "Kayaks excel at accessing spots between shore and offshore — tidal creeks, backcountry flats, and nearshore structure. Scout low-tide terrain to understand where fish hold at high tide.",
            }
        )
        cards.append(
            {
                "title": "Kayak drift fishing",
                "text": "Use a stake-out pole or anchor trolley to position perpendicular to structure. Drift fishing with the tidal current and a jig or live bait is one of the most effective kayak techniques.",
            }
        )
    if "charter" in fishing_types:
        cards.append(
            {
                "title": "Charter boat tips",
                "text": "Arrive early, tip the mate (they rig, bait, and gaff fish), and follow the captain's instructions on which side of the boat to fish. Listen for the 'lines in / lines out' call.",
            }
        )
        cards.append(
            {
                "title": "What to ask the captain",
                "text": "Before the trip: ask what species are biting, what gear the boat provides, and whether the trip is live bait or jigging focused. After the trip: mates can often fillet your catch for a tip.",
            }
        )
    if "fly" in fishing_types:
        cards.append(
            {
                "title": "Saltwater fly selection",
                "text": "Match the primary baitfish — Clouser Minnows for stripers and redfish, Crab/EP flies for permit and bonefish, weedless Toad flies for snook in grass. Size down in clear water.",
            }
        )
        cards.append(
            {
                "title": "Wind management",
                "text": "In saltwater, wind is your biggest challenge. Cast at a 45° angle to the wind (not into it). Haul early and keep your backcast tight. A stiff rod with a weight-forward line handles wind best.",
            }
        )
    return cards

def _seasonality_highlights(forecast: dict[str, Any]) -> list[dict[str, str]]:
    """Summarize active calendar windows for top species with regulation hints."""
    highlights: list[dict[str, str]] = []
    species_by_name = {sp.get("name"): sp for sp in forecast.get("species", [])}
    for row in (forecast.get("calendar") or [])[:5]:
        active = [
            m["abbr"]
            for m in row.get("months", [])
            if m.get("level") in {"peak", "good"}
        ]
        if not active:
            continue
        reg = (species_by_name.get(row.get("name"), {}) or {}).get("regulation", {})
        note = ""
        if reg:
            parts = [reg.get("min_size", ""), reg.get("bag_limit", "")]
            note = " · ".join([p for p in parts if p])
        highlights.append(
            {
                "species": row.get("name", ""),
                "season": f"Active: {', '.join(active[:6])}",
                "note": note,
            }
        )
    return highlights

def generate_forecast(
    location: Optional[dict[str, Any]] = None,
    profile: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Generate the complete fishing forecast.

    Fetches marine conditions and water temperature, classifies fishability,
    then dynamically determines which species are biting based on the current
    month and water temperature.  Rig recommendations are matched to active
    species.

    If ``profile`` is provided (from user's fishing profile), species are
    filtered to match the user's fishing style and target preferences.
    """
    tz_name = (location or {}).get("timezone", "America/New_York")
    tz = _safe_zone(tz_name)
    now = datetime.now(tz)
    month = now.month
    builder = ForecastBuilder()
    sources_used: list[str] = []
    fallbacks_triggered: list[str] = []
    location_id = (location or {}).get("id", "")
    started = time.perf_counter()
    logger.info(
        "forecast.start location_id=%s forecast_version=%s",
        location_id,
        FORECAST_VERSION,
    )

    loc_lat = (location or {}).get("lat", _LAT)
    loc_lng = (location or {}).get("lng", _LNG)
    coops_station = (location or {}).get("coops_station", WATER_TEMP_STATION)
    loc_state = (location or {}).get("state", "")

    # ── Parallel external fetch ────────────────────────────────────────────────
    # All network-bound calls are independent; submit them simultaneously so
    # per-source latency does not accumulate.  marine + water_temp are resolved
    # first since they gate species ranking (which runs CPU-only while the
    # remaining calls continue in the background).
    # Uses _FORECAST_POOL (module-level) to avoid per-request thread creation.
    _marine_fut = _FORECAST_POOL.submit(
        builder.marine_service.get_marine_forecast,
        month, location, sources_used, fallbacks_triggered,
    )
    _wtemp_fut = _FORECAST_POOL.submit(
        get_water_temp, month, location, sources_used, fallbacks_triggered,
    )
    _alerts_fut = _FORECAST_POOL.submit(
        builder.weather_service.get_weather_alerts, loc_lat, loc_lng
    )
    _state_alerts_fut = (
        _FORECAST_POOL.submit(builder.weather_service.get_state_alerts, loc_state)
        if loc_state else None
    )
    _pressure_fut = _FORECAST_POOL.submit(
        builder.buoy_service.get_barometric_pressure, location
    )
    _weather_fut = _FORECAST_POOL.submit(
        builder.weather_service.get_current_weather, loc_lat, loc_lng
    )
    _env_fut = _FORECAST_POOL.submit(
        builder.environment_service.get_coops_environmental, coops_station
    )
    _currents_fut = _FORECAST_POOL.submit(
        builder.environment_service.get_currents, coops_station, tz_name
    )
    _cur_obs_fut = _FORECAST_POOL.submit(
        builder.environment_service.get_current_observation, coops_station, tz_name
    )
    _tides_fut = _FORECAST_POOL.submit(
        builder.tide_service.get_tide_predictions, now, location, tz_name
    )
    _wq_fut = _FORECAST_POOL.submit(_get_wq, loc_lat, loc_lng)
    _fao_fut = _FORECAST_POOL.submit(_get_fao, loc_lat, loc_lng, [])

    # Resolve marine + water_temp first — they gate species ranking.
    try:
        wind_range, wave_range, wind_dir = _marine_fut.result(timeout=15)
    except Exception:
        logger.warning(
            "Marine forecast timed out for location_id=%s; using seasonal averages",
            location_id,
        )
        wind_range, wave_range, wind_dir = _seasonal_averages(month)
        fallbacks_triggered.append("marine_forecast_timeout")

    try:
        water_temp, temp_is_live = _wtemp_fut.result(timeout=12)
    except Exception:
        logger.warning(
            "Water temp unavailable for location_id=%s; using monthly average",
            location_id,
        )
        water_temp = float(MONTHLY_AVG_WATER_TEMP_F[month])
        temp_is_live = False
        fallbacks_triggered.append("water_temp_timeout")

    def format_range(r: Optional[tuple[float, float]], unit: str) -> str:
        if r is None:
            return "Unknown"
        low, high = r
        if low == high:
            return f"{low:.0f} {unit}"
        return f"{low:.0f}-{high:.0f} {unit}"

    sunrise, sunset, sun_str = builder.astro_service.get_sun_times(
        now, loc_lat, loc_lng, tz_name
    )

    wind_str = format_range(wind_range, "kt")
    if wind_dir and wind_str != "Unknown":
        wind_str = f"{wind_dir} {wind_str}"

    # Water temperature trend (compare to monthly avg and next month)
    temp_trend = ""
    temp_trend_detail = ""
    if location:
        monthly = get_monthly_water_temps(location)
        avg_this = monthly.get(month, water_temp)
        prev_month = month - 1 if month > 1 else 12
        next_month = month + 1 if month < 12 else 1
        avg_prev = monthly.get(prev_month, avg_this)
        avg_next = monthly.get(next_month, avg_this)
        seasonal_direction = avg_next - avg_prev  # positive = warming season
        diff = water_temp - avg_this
        if seasonal_direction > 1.5:
            temp_trend = "warming"
            temp_trend_detail = f"Warming trend — avg {avg_this:.0f}°F this month"
        elif seasonal_direction < -1.5:
            temp_trend = "cooling"
            temp_trend_detail = f"Cooling trend — avg {avg_this:.0f}°F this month"
        else:
            temp_trend = "stable"
            temp_trend_detail = f"Stable — avg {avg_this:.0f}°F this month"

        if abs(diff) >= 2:
            if diff > 0:
                temp_trend_detail += f", currently {diff:+.0f}°F above avg"
            else:
                temp_trend_detail += f", currently {diff:.0f}°F below avg"

    conditions = {
        "wind": wind_str,
        "wind_dir": wind_dir or "",
        "waves": format_range(wave_range, "ft"),
        "verdict": "Unknown",
        "water_temp_f": round(water_temp, 1),
        "water_temp_live": temp_is_live,
        "water_temp_trend": temp_trend,
        "water_temp_trend_detail": temp_trend_detail,
        "sunrise_sunset": sun_str,
    }

    # Determine coast for wind direction scoring and species filtering
    coast = _derive_coast(location) or "east"

    loc_fish_region = (location or {}).get("fish_region", "")
    profile = profile or {}
    _closures: list[dict[str, Any]] = []
    species = build_species_ranking(
        month,
        water_temp,
        wind_dir=wind_dir,
        wind_range=wind_range,
        wave_range=wave_range,
        hour=now.hour,
        coast=coast,
        state=loc_state,
        fishing_types=profile.get("fishing_types"),
        targets=profile.get("targets"),
        fish_region=loc_fish_region,
        closures_out=_closures,
    )
    # Tide state isn't resolved yet at this point in assembly, so the base
    # rig tips use wind/wave/temp only; personalize_forecast rebuilds these
    # with tide_state for anglers who have fishing types set.
    rig_recommendations = build_rig_recommendations(
        species,
        fishing_types=profile.get("fishing_types"),
        wind_range=wind_range,
        wave_range=wave_range,
        water_temp=water_temp,
    )

    loc_name = ""
    if location:
        loc_name = f"{location['name']}, {location['state']}"

    forecast: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "forecast_version": FORECAST_VERSION,
        "sources_used": sorted(set(sources_used)),
        "fallbacks_triggered": sorted(set(fallbacks_triggered)),
        "location_name": loc_name,
        "location_id": (location or {}).get("id", ""),
        "location_state": loc_state,
        "official_regulations_url": get_official_regulations_url(loc_state)
        if loc_state
        else "",
        "conditions": conditions,
        "species": species,
        "rig_recommendations": rig_recommendations,
        "bait_rankings": build_bait_ranking(species, month),
        "lure_recommendations": build_lure_recommendations(species, month),
        "pier_info": _build_pier_info(location),
    }

    # ── Collect remaining results from the parallel pool ──────────────────────
    # By the time species ranking finishes (above), most upstream calls have
    # likely already returned.  Each future uses an individual timeout so a
    # single slow source never delays the others.
    try:
        alerts = _alerts_fut.result(timeout=10) or []
    except Exception:
        alerts = []
        fallbacks_triggered.append("weather_alerts_unavailable")
    if alerts:
        forecast["alerts"] = alerts
        sources_used.append("NWS weather alerts")

    if _state_alerts_fut is not None:
        try:
            state_alerts = _state_alerts_fut.result(timeout=10) or []
        except Exception:
            state_alerts = []
        if state_alerts:
            # Deduplicate: remove state alerts already present in the point-based
            # alerts list (the point query is a subset of the state query).
            existing_events = {a.get("event") for a in alerts}
            state_alerts = [
                a for a in state_alerts if a.get("event") not in existing_events
            ]
            if state_alerts:
                forecast["state_alerts"] = state_alerts[:5]
                sources_used.append("NWS state alerts")

    try:
        pressure = _pressure_fut.result(timeout=10)
    except Exception:
        pressure = None
        fallbacks_triggered.append("barometric_pressure_unavailable")
    if pressure:
        forecast["pressure"] = pressure
        sources_used.append("NDBC barometric pressure")

    try:
        weather = _weather_fut.result(timeout=10)
    except Exception:
        weather = None
        fallbacks_triggered.append("current_weather_unavailable")
    if weather:
        forecast["weather"] = weather
        sources_used.append("NWS current weather")

    try:
        env_metrics = _env_fut.result(timeout=10) or {}
    except Exception:
        env_metrics = {}
        fallbacks_triggered.append("coops_environment_unavailable")
    if env_metrics:
        if weather:
            if "air_temp_f" not in env_metrics:
                env_metrics["air_temp_f"] = weather.get("air_temp_f")
            if "humidity_pct" not in env_metrics:
                env_metrics["humidity_pct"] = weather.get("humidity")
        forecast["environment"] = env_metrics
        sources_used.append("NOAA CO-OPS environmental")

    # Expose coordinates and state so templates and API consumers can use them
    # without digging into the location dict.
    forecast["lat"] = loc_lat
    forecast["lng"] = loc_lng
    forecast["state"] = loc_state

    try:
        _wq = _wq_fut.result(timeout=8)
    except Exception:
        _wq = {"available": False}
    if _wq.get("available"):
        forecast["water_quality"] = _wq
        sources_used.append("EPA Water Quality Portal")

    try:
        _fao = _fao_fut.result(timeout=8)
    except Exception:
        _fao = {}
    if _fao.get("fao_zone"):
        forecast["fao_enrichment"] = _fao
        sources_used.append("FAO GeoNetwork")

    # Propagate humidity into conditions so the template always has a single
    # reliable place to look, regardless of which data source provided it.
    _humidity = (forecast.get("environment") or {}).get("humidity_pct") or (
        forecast.get("weather") or {}
    ).get("humidity")
    if _humidity is not None:
        forecast["conditions"]["humidity"] = _humidity

    try:
        currents = _currents_fut.result(timeout=10) or []
    except Exception:
        currents = []
    try:
        current_observation = _cur_obs_fut.result(timeout=10)
    except Exception:
        current_observation = None
    if current_observation:
        currents = [current_observation, *currents]
        sources_used.append("NOAA currents observation")
    if currents:
        forecast["currents"] = currents
        sources_used.append("NOAA currents predictions")

    try:
        tide_data = _tides_fut.result(timeout=12) or {}
    except Exception:
        tide_data = {}
        fallbacks_triggered.append("tide_predictions_unavailable")
    if tide_data:
        forecast.update(tide_data)
        sources_used.append("NOAA tide predictions")

    # Solunar fishing times
    solunar = builder.astro_service.get_solunar_times(now, loc_lat, loc_lng, tz_name)
    if solunar:
        forecast["solunar"] = solunar
        sources_used.append("astronomy solunar")
    else:
        fallbacks_triggered.append("solunar_unavailable")

    twilight = builder.astro_service.get_twilight_times(now, loc_lat, loc_lng, tz_name)
    if twilight:
        forecast["twilight"] = twilight
        sources_used.append("astronomy twilight")

    lunar_details = builder.astro_service.get_lunar_details(now, loc_lng, tz_name)
    if lunar_details:
        forecast["lunar_details"] = lunar_details
        if solunar:
            forecast["lunar_details"]["illumination_pct"] = solunar.get(
                "illumination_pct"
            )
            forecast["lunar_details"]["phase"] = solunar.get("moon_phase")
        sources_used.append("astronomy lunar details")

    uv_index = _estimate_uv_index(now, sunrise, sunset, loc_lat)
    forecast["uv"] = {"index": uv_index, **_uv_category(uv_index)}
    forecast["rip_current_risk"] = _rip_risk_from_conditions(wave_range, wind_range)
    forecast["education"] = _build_education_cards(
        profile, forecast["uv"], forecast["rip_current_risk"]
    )

    humidity_for_dew = None
    temp_for_dew = None
    if forecast.get("environment"):
        humidity_for_dew = forecast["environment"].get("humidity_pct")
        temp_for_dew = forecast["environment"].get("air_temp_f")
    if forecast.get("weather"):
        humidity_for_dew = (
            humidity_for_dew
            if humidity_for_dew is not None
            else forecast["weather"].get("humidity")
        )
        temp_for_dew = (
            temp_for_dew
            if temp_for_dew is not None
            else forecast["weather"].get("air_temp_f")
        )
    dew_point = _dew_point_f(temp_for_dew, humidity_for_dew)
    derived_indices: dict[str, float] = {}
    if dew_point is not None:
        derived_indices["dew_point_f"] = dew_point

    est_heat_index = _heat_index_f(temp_for_dew, humidity_for_dew)
    if est_heat_index is not None and not (forecast.get("weather") or {}).get(
        "feels_like_f"
    ):
        derived_indices["heat_index_f"] = est_heat_index

    wind_ref = wind_range[1] if wind_range else None
    est_wind_chill = _wind_chill_f(temp_for_dew, wind_ref)
    if est_wind_chill is not None and not (forecast.get("weather") or {}).get(
        "wind_chill_f"
    ):
        derived_indices["wind_chill_f"] = est_wind_chill

    if derived_indices:
        forecast["derived_indices"] = derived_indices

    # Final fishability verdict + go/no-go index based on all available signals
    _fishability = score_conditions(
        wind_range,
        wave_range,
        wind_dir=wind_dir or "",
        water_temp_f=water_temp,
        is_live_temp=temp_is_live,
        tide_state=forecast.get("tide_state", ""),
        tides=forecast.get("tides"),
        sunrise=sunrise,
        sunset=sunset,
        now=now,
        solunar=solunar,
        coast=coast or "east",
    )
    conditions["verdict"] = _fishability["verdict"]
    conditions["fishability_score"] = _fishability["score"]
    conditions["fishability_factors"] = _fishability["factors"]
    conditions["summary"] = _fishability["summary"]

    # Multi-day outlook (3 days)
    try:
        outlook = build_multiday_outlook(now, location)
        if outlook:
            forecast["outlook"] = outlook
    except Exception:
        logger.warning(
            "build_multiday_outlook failed for location_id=%s",
            location_id,
            exc_info=True,
        )

    # Best day to fish (trip planner)
    if forecast.get("outlook"):
        forecast["best_day"] = pick_best_fishing_day(
            forecast["conditions"]["verdict"],
            forecast["outlook"],
            today_score=forecast["conditions"].get("fishability_score"),
        )

    # Species availability calendar — shows popular regional target species,
    # not day-to-day bite conditions.
    forecast["calendar"] = build_species_calendar(
        species, location, fish_region=loc_fish_region
    )
    forecast["seasonality"] = _seasonality_highlights(forecast)

    # Natural bait availability (bait DB only has "east"/"west" entries)
    bait_coast = "west" if coast == "west" else "east"
    forecast["natural_bait"] = build_natural_bait_chart(month, bait_coast)

    # Spawning report — species currently or nearly spawning based on
    # month and water temperature.  State is passed so legal status can be
    # surfaced (closed season, C&R-only, size/bag limits).
    forecast["spawning"] = build_spawning_report(
        month, water_temp, coast, state=loc_state
    )

    # Closure advisory: otherwise-relevant species that are off-limits this
    # month (out of season or prohibited), captured during species ranking at
    # no extra lookup cost. Deduped, highest-scoring first, capped at 4.
    if _closures:
        _seen: set[str] = set()
        _uniq = []
        for c in sorted(_closures, key=lambda x: -x.get("score", 0)):
            key = c["name"].lower()
            if key in _seen:
                continue
            _seen.add(key)
            _uniq.append(
                {
                    "name": c["name"],
                    "status": c["status"],
                    "season": c.get("season", ""),
                    "official_source": c.get("official_source", ""),
                }
            )
            if len(_uniq) >= 4:
                break
        if _uniq:
            forecast["closures"] = _uniq

    # Spot tips based on current conditions
    forecast["spot_tips"] = build_spot_tips(
        wind_range=wind_range,
        wave_range=wave_range,
        wind_dir=wind_dir or "",
        hour=now.hour,
        month=month,
        coast=coast,
        tide_state=forecast.get("tide_state", ""),
        water_quality=forecast.get("water_quality"),
        recent_rain_in=(forecast.get("weather") or {}).get("precip_recent_in"),
    )

    # Conditions explainer
    forecast["conditions_explainer"] = build_conditions_explainer(
        wind_range=wind_range,
        wave_range=wave_range,
        wind_dir=wind_dir,
        water_temp=water_temp,
        pressure=forecast.get("pressure"),
        tide_state=forecast.get("tide_state", ""),
        coast=coast,
    )

    # Bite alerts
    forecast["bite_alerts"] = build_bite_alerts(
        verdict=forecast["conditions"]["verdict"],
        species=species,
        pressure=forecast.get("pressure"),
        tide_state=forecast.get("tide_state", ""),
    )

    # Gear checklist
    forecast["gear_checklist"] = build_gear_checklist(
        species=species,
        wind_range=wind_range,
        wave_range=wave_range,
        hour=now.hour,
        water_temp=water_temp,
        weather=forecast.get("weather"),
    )

    # Safety checklist
    forecast["safety"] = build_safety_checklist(
        wind_range=wind_range,
        wave_range=wave_range,
        hour=now.hour,
        alerts=forecast.get("alerts"),
        water_temp=water_temp,
    )

    # Best fishing times (synthesize solunar + tides + sunrise/sunset)
    forecast["best_times"] = build_best_times(forecast)

    # 24-hour activity timeline
    forecast["activity_timeline"] = build_activity_timeline(forecast, now_hour=now.hour)

    # Add technique tips to each species
    t_state = forecast.get("tide_state", "")
    wind_strength = ""
    if wind_range:
        avg_wind = (wind_range[0] + wind_range[1]) / 2
        if avg_wind > 20:
            wind_strength = "strong"
        elif avg_wind > 10:
            wind_strength = "moderate"
        else:
            wind_strength = "light"
    for sp_entry in forecast["species"]:
        sp_entry["tip"] = _get_technique_tip(
            sp_entry["name"],
            hour=now.hour,
            tide_state=t_state,
            wind_strength=wind_strength,
        )

    forecast["sources_used"] = sorted(set(sources_used))
    forecast["fallbacks_triggered"] = sorted(set(fallbacks_triggered))
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "forecast.end location_id=%s forecast_version=%s duration_ms=%s sources=%s fallbacks=%s",
        location_id,
        FORECAST_VERSION,
        duration_ms,
        len(forecast["sources_used"]),
        len(forecast["fallbacks_triggered"]),
    )
    return forecast

# ---------------------------------------------------------------------------
# Profile personalization helpers
# ---------------------------------------------------------------------------

# Species name keywords → score boost per primary fishing goal.
# Uses lowercase substring matching so partial names work across regions.
_TROPHY_BOOSTS: dict[str, int] = {
    "tarpon": 14,
    "cobia": 12,
    "mahi-mahi": 12,
    "king mackerel": 10,
    "striped bass": 10,
    "greater amberjack": 10,
    "shark": 10,
    "black drum": 8,
    "red drum": 8,
    "red grouper": 8,
    "gag grouper": 8,
    "red snapper": 8,
    "snook": 8,
    "tripletail": 8,
    "wahoo": 10,
    "blackfin tuna": 10,
    "permit": 8,
}
_ACTION_BOOSTS: dict[str, int] = {
    "bluefish": 10,
    "spanish mackerel": 8,
    "jack crevalle": 8,
    "atlantic bonito": 8,
    "ladyfish": 8,
    "pompano": 7,
    "whiting": 6,
    "spot": 5,
    "atlantic croaker": 5,
    "barred surfperch": 6,
    "corbina": 6,
    "blue runner": 6,
}
_RELAXING_BOOSTS: dict[str, int] = {
    "whiting": 8,
    "spot": 8,
    "atlantic croaker": 8,
    "sheepshead": 7,
    "flounder": 6,
    "scup": 6,
    "pompano": 6,
    "speckled trout": 6,
    "sheephead": 7,
    "white croaker": 7,
    "surfperch": 6,
}
_GOAL_BOOST_MAP: dict[str, dict[str, int]] = {
    "trophy": _TROPHY_BOOSTS,
    "action": _ACTION_BOOSTS,
    "relaxing": _RELAXING_BOOSTS,
    # "exploring" — no boost, keep the natural species mix
}

# Condition tolerance verdict shifts.
# rough = willing to fish hard conditions → upgrade marginal verdicts
# calm  = prefers flat water → downgrade marginal verdicts
_TOLERANCE_SHIFT: dict[str, dict[str, str]] = {
    "rough": {"Poor": "Challenging", "Challenging": "Fair"},
    "calm": {"Fair": "Challenging", "Challenging": "Poor"},
}

# Score bump for species the angler has previously landed at this location.
# Modest by design — a local track record is a nudge, not an override.
_CAUGHT_HERE_BOOST = 8

def _coerce_threshold(value: Any) -> Optional[float]:
    """Parse a user-supplied comfort threshold to a float, or ``None``.

    Accepts numbers or numeric strings; rejects blanks, bools, and junk so a
    malformed profile value never blows up the go/no-go index calculation.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None

def _apply_primary_goal_boost(
    species: list[dict[str, Any]], primary_goal: str
) -> list[dict[str, Any]]:
    """Re-rank species list based on the angler's primary fishing goal."""
    boosts = _GOAL_BOOST_MAP.get(primary_goal)
    if not boosts:
        return species
    for sp in species:
        name_lc = sp.get("name", "").lower()
        for keyword, pts in boosts.items():
            if keyword in name_lc:
                sp["score"] = min(100, sp.get("score", 0) + pts)
                break
    species.sort(key=lambda x: -x.get("score", 0))
    for i, sp in enumerate(species):
        sp["rank"] = i + 1
    return species

def personalize_forecast(
    forecast: dict[str, Any],
    profile: dict[str, Any],
    location: Optional[dict[str, Any]] = None,
    caught_species: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Apply profile-based personalization to a cached forecast.

    Re-runs species ranking with profile filters and rebuilds the
    species-dependent sections (rigs, baits, bite alerts, gear checklist,
    calendar).  Conditions data, tides, weather, etc. remain unchanged.

    *caught_species* (lowercased names the user has logged at this location)
    gives a modest rank boost to proven local producers.

    Results are cached in _PERSONALIZE_CACHE keyed by
    (loc_id, generated_at, hour, profile_tuple, caught_species) so repeat
    renders within the same hour return immediately without re-running
    species scoring.
    """
    caught_lc = frozenset(caught_species or ())
    cache_key = _profile_cache_key(forecast, location, profile) + (caught_lc,)
    cached = _PERSONALIZE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    fishing_types = profile.get("fishing_types")
    targets = profile.get("targets")
    experience = profile.get("experience") or ""
    live_bait = profile.get("live_bait") or ""
    cut_bait = profile.get("cut_bait") or ""
    lures = profile.get("lures") or ""
    preferred_times = profile.get("preferred_times") or []
    primary_goal = profile.get("primary_goal") or ""
    condition_tolerance = profile.get("condition_tolerance") or ""
    tide_preference = profile.get("tide_preference") or ""
    catch_release = profile.get("catch_release") or ""
    max_wind_kt = _coerce_threshold(profile.get("max_wind_kt"))
    max_wave_ft = _coerce_threshold(profile.get("max_wave_ft"))

    has_type_prefs = bool(fishing_types or targets)
    has_gear_prefs = bool(experience or live_bait or cut_bait or lures)
    has_threshold_prefs = max_wind_kt is not None or max_wave_ft is not None
    if (
        not has_type_prefs
        and not has_gear_prefs
        and not has_threshold_prefs
        and not caught_lc
    ):
        return forecast

    tz_name = (location or {}).get("timezone", "America/New_York")
    tz = _safe_zone(tz_name)
    now = datetime.now(tz)
    month = now.month

    # Extract conditions from cached forecast for re-scoring
    conds = forecast.get("conditions", {})
    water_temp = conds.get("water_temp_f", 70)
    wind_dir = conds.get("wind_dir") or None

    # Parse wind/wave ranges from formatted strings
    def _parse_range(s: str) -> Optional[tuple[float, float]]:
        if not s or s == "Unknown":
            return None
        # Remove direction prefix and unit suffix
        parts = s.split()
        nums = []
        for p in parts:
            # Handle "5-10" format
            if "-" in p:
                try:
                    lo, hi = p.split("-")
                    return (float(lo), float(hi))
                except ValueError:
                    pass
            try:
                nums.append(float(p))
            except ValueError:
                pass
        if len(nums) == 1:
            return (nums[0], nums[0])
        if len(nums) >= 2:
            return (nums[0], nums[1])
        return None

    wind_str = conds.get("wind", "")
    wave_str = conds.get("waves", "")
    wind_range = _parse_range(wind_str)
    wave_range = _parse_range(wave_str)

    coast = _derive_coast(location)
    loc_state = (location or {}).get("state", "")
    loc_fish_region = (location or {}).get("fish_region", "")

    # Re-rank species only when fishing type or target preferences are provided.
    # When only experience/bait prefs are set, keep existing cached species but
    # still rebuild rigs and tips with the gear/experience-aware logic below.
    if has_type_prefs:
        species = build_species_ranking(
            month,
            water_temp,
            wind_dir=wind_dir,
            wind_range=wind_range,
            wave_range=wave_range,
            hour=now.hour,
            coast=coast,
            state=loc_state,
            fishing_types=fishing_types,
            targets=targets,
            fish_region=loc_fish_region,
        )
    else:
        # Shallow-copy each species dict so we don't mutate cached objects
        # when we add per-session tip fields below.
        species = [dict(s) for s in (forecast.get("species") or [])]

    # Add technique tips
    t_state = forecast.get("tide_state", "")
    wind_strength = ""
    if wind_range:
        avg_wind = (wind_range[0] + wind_range[1]) / 2
        if avg_wind > 20:
            wind_strength = "strong"
        elif avg_wind > 10:
            wind_strength = "moderate"
        else:
            wind_strength = "light"
    for sp_entry in species:
        sp_entry["tip"] = _get_technique_tip(
            sp_entry["name"],
            hour=now.hour,
            tide_state=t_state,
            wind_strength=wind_strength,
        )

    # Apply primary-goal score adjustments so trophy hunters see big gamefish
    # first, action anglers see prolific biters, and relaxing anglers see
    # abundant easy-to-catch species ranked higher.
    if primary_goal:
        species = _apply_primary_goal_boost(species, primary_goal)

    # Proven-here boost: species the angler has actually landed at this spot
    # get a modest bump and a flag, so local track record nudges the ranking.
    if caught_lc:
        for sp in species:
            if sp.get("name", "").lower() in caught_lc:
                sp["score"] = min(100, sp.get("score", 0) + _CAUGHT_HERE_BOOST)
                sp["caught_here"] = True
        species.sort(key=lambda x: -x.get("score", 0))
        for i, sp in enumerate(species):
            sp["rank"] = i + 1

    # Rebuild species-dependent sections
    forecast = dict(forecast)  # shallow copy to avoid mutating cache
    forecast["species"] = species
    forecast["rig_recommendations"] = build_rig_recommendations(
        species,
        fishing_types=fishing_types,
        experience=experience,
        live_bait=live_bait,
        cut_bait=cut_bait,
        lures=lures,
        wind_range=wind_range,
        wave_range=wave_range,
        water_temp=water_temp,
        tide_state=t_state,
    )
    # Annotate rigs with a circle-hook tip for catch-and-release anglers.
    # Only fires when the rig doesn't already specify a circle hook.
    if catch_release in ("always", "sometimes"):
        for rig in forecast["rig_recommendations"]:
            hook = rig.get("hook", "").lower()
            if "circle" not in hook and "barbless" not in hook:
                rig["cr_tip"] = (
                    "Circle hook recommended — easier releases with less injury"
                )
    forecast["bait_rankings"] = build_bait_ranking(species, month)
    forecast["lure_recommendations"] = build_lure_recommendations(species, month)
    forecast["calendar"] = build_species_calendar(
        species,
        location,
        fish_region=loc_fish_region,
    )
    forecast["bite_alerts"] = build_bite_alerts(
        verdict=conds.get("verdict", "Fair"),
        species=species,
        pressure=forecast.get("pressure"),
        tide_state=t_state,
    )
    forecast["gear_checklist"] = build_gear_checklist(
        species=species,
        wind_range=wind_range,
        wave_range=wave_range,
        hour=now.hour,
        water_temp=water_temp,
        weather=forecast.get("weather"),
        fishing_types=fishing_types,
    )

    # Rebuild safety checklist with profile fishing types
    forecast["safety"] = build_safety_checklist(
        wind_range=wind_range,
        wave_range=wave_range,
        hour=now.hour,
        alerts=forecast.get("alerts"),
        fishing_types=fishing_types,
        water_temp=water_temp,
    )

    # Rebuild 3-day outlook with profile-filtered species
    outlook = build_multiday_outlook(
        now,
        location,
        fishing_types=fishing_types,
        targets=targets,
    )
    if outlook:
        forecast["outlook"] = outlook
        _conds = forecast.get("conditions", {})
        forecast["best_day"] = pick_best_fishing_day(
            _conds.get("verdict", "Fair"),
            outlook,
            today_score=_conds.get("fishability_score_for_angler")
            or _conds.get("fishability_score"),
        )

    # Rebuild spot tips with type-specific content
    tide_state_val = forecast.get("tide_state", "")
    loc_coast = (location or {}).get("coast", "east")
    forecast["spot_tips"] = build_spot_tips(
        wind_range=wind_range,
        wave_range=wave_range,
        wind_dir=wind_dir or "",
        hour=now.hour,
        month=month,
        coast=loc_coast,
        tide_state=tide_state_val,
        fishing_types=fishing_types,
        experience=experience,
        water_quality=forecast.get("water_quality"),
        recent_rain_in=(forecast.get("weather") or {}).get("precip_recent_in"),
    )

    # Rebuild best fishing times with type-specific windows, boosting
    # windows that fall in the angler's preferred fishing time slots and
    # preferred tide phase.
    forecast["best_times"] = build_best_times(
        forecast,
        fishing_types=fishing_types,
        preferred_times=preferred_times,
        tide_preference=tide_preference,
    )

    # Recalculate the go/no-go index with this angler's method and personal
    # comfort thresholds so the dashboard shows a rating and explanation tuned
    # to how *they* fish, not just the generic baseline.
    if (fishing_types or has_threshold_prefs) and wind_range and wave_range:
        adjusted = score_conditions(
            wind_range,
            wave_range,
            wind_dir=wind_dir or "",
            water_temp_f=water_temp,
            tide_state=forecast.get("tide_state", ""),
            tides=forecast.get("tides"),
            now=now,
            solunar=forecast.get("solunar"),
            coast=loc_coast,
            fishing_types=fishing_types,
            max_wind_kt=max_wind_kt,
            max_wave_ft=max_wave_ft,
        )
        # Store as separate fields so the template can show both the generic
        # verdict and the method-specific one without breaking cached data.
        updated_conditions = dict(forecast.get("conditions", {}))
        updated_conditions["verdict_for_type"] = adjusted["verdict"]
        updated_conditions["fishability_score_for_angler"] = adjusted["score"]
        updated_conditions["fishability_factors"] = adjusted["factors"]
        updated_conditions["summary"] = adjusted["summary"]
        updated_conditions["exceeds_thresholds"] = adjusted["exceeds"]
        forecast["conditions"] = updated_conditions

    # Condition tolerance: shift the effective verdict for this angler's
    # comfort zone. "rough" anglers see a one-step upgrade on marginal days;
    # "calm" anglers see a one-step downgrade so they know today may be above
    # their threshold even if conditions are technically "Fair".
    if condition_tolerance:
        ref_verdict = forecast.get("conditions", {}).get(
            "verdict_for_type"
        ) or forecast.get("conditions", {}).get("verdict", "")
        angler_verdict = _TOLERANCE_SHIFT.get(condition_tolerance, {}).get(
            ref_verdict, ref_verdict
        )
        if angler_verdict and angler_verdict != ref_verdict:
            cond_copy = dict(forecast.get("conditions", {}))
            cond_copy["verdict_for_angler"] = angler_verdict
            forecast["conditions"] = cond_copy

    if len(_PERSONALIZE_CACHE) >= _PERSONALIZE_CACHE_MAX:
        _PERSONALIZE_CACHE.pop(next(iter(_PERSONALIZE_CACHE)))
    _PERSONALIZE_CACHE[cache_key] = forecast
    return forecast

def _parse_time_str(s: str) -> float:
    """Parse a time string like '6:32 AM' to decimal hour."""
    try:
        s = s.strip().upper()
        parts = s.replace(":", " ").replace("AM", "").replace("PM", "").split()
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        is_pm = "PM" in s
        if h == 12:
            h = 0 if not is_pm else 12
        elif is_pm:
            h += 12
        return h + m / 60.0
    except Exception:
        return 12.0

def build_best_times(
    forecast: dict[str, Any],
    fishing_types: Optional[list[str]] = None,
    preferred_times: Optional[list[str]] = None,
    tide_preference: str = "",
) -> list[dict[str, str]]:
    """Build a list of recommended fishing windows.

    Combines solunar major/minor periods with tide change windows and
    low-light periods (dawn/dusk) to suggest the best 2-3 windows.
    Each entry: {"window": "5:30 - 7:30 AM", "reason": "...", "quality": "Prime"}

    When *fishing_types* includes bridge or jetty, low-tide current-change
    windows are added as prime current-reversal opportunities.

    When *preferred_times* is provided, windows that fall in the angler's
    available time slots receive a score boost so they rank higher.

    When *tide_preference* is provided, windows near the preferred tide event
    (high/low) or during the preferred tidal phase (incoming/outgoing) are
    boosted so the user sees the windows that match their preferred conditions.
    """
    ft = set(fishing_types or [])
    windows: list[dict[str, Any]] = []

    # Sunrise/sunset windows (dawn and dusk are prime fishing)
    sun_str = forecast.get("conditions", {}).get("sunrise_sunset", "")
    if "/" in sun_str:
        parts = sun_str.split("/")
        sunrise_str = parts[0].strip()
        sunset_str = parts[1].strip()
        sr_h = _parse_time_str(sunrise_str)
        ss_h = _parse_time_str(sunset_str)

        # Dawn window: 30 min before to 60 min after sunrise
        dawn_start = sr_h - 0.5
        dawn_end = sr_h + 1.0
        windows.append(
            {
                "start_h": dawn_start,
                "end_h": dawn_end,
                "label": f"{sunrise_str} window",
                "reason": "Dawn — fish feed actively in low light",
                "score": 3,
            }
        )

        # Dusk window: 60 min before to 30 min after sunset
        dusk_start = ss_h - 1.0
        dusk_end = ss_h + 0.5
        windows.append(
            {
                "start_h": dusk_start,
                "end_h": dusk_end,
                "label": f"{sunset_str} window",
                "reason": "Dusk — prime low-light feeding period",
                "score": 3,
            }
        )

    # Solunar major periods
    solunar = forecast.get("solunar", {})
    for mp in solunar.get("major_periods", []):
        s_h = _parse_time_str(mp["start"])
        e_h = _parse_time_str(mp["end"])
        windows.append(
            {
                "start_h": s_h,
                "end_h": e_h,
                "label": f"{mp['start']} – {mp['end']}",
                "reason": "Solunar major — peak lunar activity",
                "score": 4,
            }
        )

    for mp in solunar.get("minor_periods", []):
        s_h = _parse_time_str(mp["start"])
        e_h = _parse_time_str(mp["end"])
        windows.append(
            {
                "start_h": s_h,
                "end_h": e_h,
                "label": f"{mp['start']} – {mp['end']}",
                "reason": "Solunar minor — elevated activity",
                "score": 2,
            }
        )

    # Tide change windows (best fishing near high tides)
    tides = forecast.get("tides", [])
    for t in tides:
        if t.get("type") == "High":
            t_h = t.get("hour", _parse_time_str(t["time"]))
            windows.append(
                {
                    "start_h": t_h - 1.0,
                    "end_h": t_h + 1.0,
                    "label": f"{t['time']}",
                    "reason": "Incoming high tide pushes bait into range",
                    "score": 2,
                }
            )

    # Bridge / jetty: low-tide current reversal is a prime window.
    # As the tide bottoms out and reverses, fish stack at inlet mouths
    # and bridge pilings waiting for bait to sweep through.
    if "bridge" in ft or "jetty" in ft:
        for t in tides:
            if t.get("type") == "Low":
                t_h = t.get("hour", _parse_time_str(t["time"]))
                windows.append(
                    {
                        "start_h": t_h - 0.5,
                        "end_h": t_h + 1.5,
                        "label": f"{t['time']}",
                        "reason": (
                            "Current reversal at low tide — fish stack at "
                            "bridge pilings and jetty tips as flow changes direction"
                        ),
                        "score": 3,  # High value for current-structure fishing
                    }
                )

    # Bridge: night window bonus — bridge lights activate the bite after dark
    if "bridge" in ft:
        for night_h in (21.0, 22.0):  # 9-10 PM window
            windows.append(
                {
                    "start_h": night_h,
                    "end_h": night_h + 2.0,
                    "label": "After Dark",
                    "reason": "Bridge light edges after dark — snook and tarpon stage under LED lights",
                    "score": 3,
                }
            )

    if not windows:
        return []

    # Boost windows that fall within the angler's preferred fishing time slots.
    # This surfaces windows the angler can actually attend rather than windows
    # they'd have to skip (e.g. a 3 AM solunar major for a morning-only angler).
    if preferred_times:
        _PREF_HOUR_RANGES = {
            "dawn": (3.5, 7.0),
            "morning": (7.0, 12.0),
            "afternoon": (12.0, 17.0),
            "evening": (17.0, 21.0),
            "night": (21.0, 27.5),  # spans midnight; check mid >= 21 or mid < 3.5
        }
        for w in windows:
            mid = (w["start_h"] + w["end_h"]) / 2.0
            for pt in preferred_times:
                lo, hi = _PREF_HOUR_RANGES.get(pt, (0.0, 0.0))
                in_range = lo <= mid < hi
                if pt == "night" and not in_range:
                    in_range = mid >= 21.0 or mid < 3.5
                if in_range:
                    w["score"] += 2
                    break

    # Boost windows that match the angler's preferred tide phase or event.
    # "high"/"low" boost windows within 1.5 hrs of the corresponding tide event.
    # "incoming"/"outgoing" boost windows that occur during the appropriate
    # phase (between Low→High or High→Low respectively) based on tide schedule.
    if tide_preference and tide_preference != "any":
        tides_for_pref = forecast.get("tides", [])
        sorted_tides = sorted(
            tides_for_pref,
            key=lambda t: t.get("hour", _parse_time_str(t.get("time", "0:00"))),
        )
        for w in windows:
            mid = (w["start_h"] + w["end_h"]) / 2.0
            if tide_preference == "high":
                for t in sorted_tides:
                    if t.get("type") == "High":
                        t_h = t.get("hour", _parse_time_str(t.get("time", "0:00")))
                        if abs(mid - t_h) <= 1.5:
                            w["score"] += 2
                            break
            elif tide_preference == "low":
                for t in sorted_tides:
                    if t.get("type") == "Low":
                        t_h = t.get("hour", _parse_time_str(t.get("time", "0:00")))
                        if abs(mid - t_h) <= 1.5:
                            w["score"] += 2
                            break
            elif tide_preference in ("incoming", "outgoing"):
                # Determine phase: find the most recent tide event before mid
                prev_type = None
                for t in sorted_tides:
                    t_h = t.get("hour", _parse_time_str(t.get("time", "0:00")))
                    if t_h <= mid:
                        prev_type = t.get("type")
                    else:
                        break
                # incoming = rising (after Low), outgoing = falling (after High)
                if tide_preference == "incoming" and prev_type == "Low":
                    w["score"] += 2
                elif tide_preference == "outgoing" and prev_type == "High":
                    w["score"] += 2

    # Score each window: boost when multiple factors overlap
    # Check for overlaps between windows
    scored_windows: list[dict[str, Any]] = []
    for w in windows:
        overlap_bonus = 0
        for other in windows:
            if other is w:
                continue
            # Check if windows overlap
            if w["start_h"] < other["end_h"] and w["end_h"] > other["start_h"]:
                overlap_bonus += other["score"]
        w["total_score"] = w["score"] + overlap_bonus
        scored_windows.append(w)

    # Sort by total score and pick the best 3 non-overlapping windows
    scored_windows.sort(key=lambda x: x["total_score"], reverse=True)
    selected: list[dict[str, str]] = []
    used_hours: list[tuple[float, float]] = []
    for w in scored_windows:
        # Skip if too close to an already-selected window
        skip = False
        for uh_s, uh_e in used_hours:
            if w["start_h"] < uh_e + 0.5 and w["end_h"] > uh_s - 0.5:
                skip = True
                break
        if skip:
            continue

        total = w["total_score"]
        if total >= 6:
            quality = "Prime"
        elif total >= 4:
            quality = "Good"
        else:
            quality = "Fair"

        selected.append(
            {
                "window": w["label"],
                "reason": w["reason"],
                "quality": quality,
            }
        )
        used_hours.append((w["start_h"], w["end_h"]))
        if len(selected) >= 3:
            break

    return selected

def build_activity_timeline(
    forecast: dict[str, Any], now_hour: int = -1
) -> list[dict[str, Any]]:
    """Build a 24-hour fish activity timeline (one value per hour).

    Each entry: {"hour": 0-23, "label": "12 AM", "level": 0-100,
                  "tag": "low/med/high/prime", "is_now": bool,
                  "peak": bool, "reason": str}
    Combines solunar periods, tide changes, and dawn/dusk to estimate activity.
    Dampens bars when wind conditions are rough.
    """
    activity = [15.0] * 24
    reason_parts: list[list[str]] = [[] for _ in range(24)]
    # Overlay rails aligned to the same 24 hourly columns as the bars.
    feeding_tag = [""] * 24  # "major" / "minor" — solunar feeding bands
    sun_events: dict[int, str] = {}  # hour -> "sunrise" / "sunset"
    tide_events: dict[int, dict[str, str]] = {}  # hour -> {type, time}

    # Dawn/dusk boost
    sun_str = forecast.get("conditions", {}).get("sunrise_sunset", "")
    if "/" in sun_str:
        parts = sun_str.split("/")
        sr_h = _parse_time_str(parts[0].strip())
        ss_h = _parse_time_str(parts[1].strip())
        sun_events[int(round(sr_h)) % 24] = "sunrise"
        sun_events[int(round(ss_h)) % 24] = "sunset"

        for h in range(24):
            dist = abs(h - sr_h)
            if dist < 1.5:
                boost = 30 * max(0, 1 - dist / 1.5)
                activity[h] += boost
                if boost > 8:
                    reason_parts[h].append("Dawn")
            dist = abs(h - ss_h)
            if dist < 1.5:
                boost = 30 * max(0, 1 - dist / 1.5)
                activity[h] += boost
                if boost > 8:
                    reason_parts[h].append("Dusk")

    # Solunar periods
    solunar = forecast.get("solunar", {})
    for mp in solunar.get("major_periods", []):
        s_h = _parse_time_str(mp["start"])
        e_h = _parse_time_str(mp["end"])
        for h in range(24):
            if s_h <= h <= e_h:
                activity[h] += 35
                reason_parts[h].append("Major solunar")
                feeding_tag[h] = "major"
            elif abs(h - s_h) < 1 or abs(h - e_h) < 1:
                activity[h] += 15

    for mp in solunar.get("minor_periods", []):
        s_h = _parse_time_str(mp["start"])
        e_h = _parse_time_str(mp["end"])
        for h in range(24):
            if s_h <= h <= e_h:
                activity[h] += 20
                reason_parts[h].append("Minor solunar")
                # Don't downgrade an hour already inside a major band.
                if feeding_tag[h] != "major":
                    feeding_tag[h] = "minor"
            elif abs(h - s_h) < 1 or abs(h - e_h) < 1:
                activity[h] += 8

    # Tide change windows
    tides = forecast.get("tides", [])
    for t in tides:
        t_h = t.get("hour", _parse_time_str(t.get("time", "12:00 PM")))
        t_type = t.get("type", "")
        if t_type in ("High", "Low"):
            tide_events[int(round(float(t_h))) % 24] = {
                "type": t_type,
                "time": t.get("time", ""),
            }
        for h in range(24):
            dist = abs(h - t_h)
            if dist < 2:
                boost = 20 if t_type == "High" else 12
                activity[h] += boost * max(0, 1 - dist / 2)
                if dist < 1:
                    reason_parts[h].append(
                        "High tide" if t_type == "High" else "Low tide"
                    )

    # Night penalty (fish less active 11 PM – 4 AM)
    for h in [23, 0, 1, 2, 3, 4]:
        activity[h] *= 0.7

    # Bright-moon nocturnal feeding: a near-full moon keeps fish active after
    # dark, partially offsetting the night penalty.
    illum = (solunar or {}).get("illumination_pct")
    if isinstance(illum, (int, float)) and illum >= 70:
        for h in [21, 22, 23, 0, 1, 2, 3]:
            activity[h] *= 1.25

    # Normalize relative to today's peak
    max_val = max(activity) if max(activity) > 0 else 1
    normalized = [min(100, int(activity[h] / max_val * 100)) for h in range(24)]

    # Wind condition multiplier: rough conditions reduce the activity ceiling
    wind_str = forecast.get("conditions", {}).get("wind", "")
    wind_speed = 0
    for tok in wind_str.split():
        if tok and tok[0].isdigit():
            try:
                wind_speed = int(tok.split("-")[0])
                break
            except ValueError:
                pass
    if wind_speed >= 25:
        condition_mult = 0.65
    elif wind_speed >= 15:
        condition_mult = 0.82
    else:
        condition_mult = 1.0

    # Barometric-pressure trend multiplier: a falling glass (especially a low,
    # pre-front reading) triggers aggressive feeding; a high post-front rise
    # or a too-stable bluebird high slows the bite.  Applied day-wide since we
    # only have a current reading, not an hourly pressure forecast.
    pressure_mult = 1.0
    pressure = forecast.get("pressure")
    if pressure:
        p_trend = str(pressure.get("trend", "")).lower()
        try:
            p_mb = float(pressure.get("pressure_mb") or 0) or None
        except (TypeError, ValueError):
            p_mb = None
        if "falling" in p_trend:
            pressure_mult = 1.10 if (p_mb is not None and p_mb < 1010) else 1.05
        elif "rising" in p_trend:
            pressure_mult = 0.90 if (p_mb is not None and p_mb > 1020) else 0.95
        elif p_mb is not None and p_mb > 1025:
            pressure_mult = 0.95  # stagnant high — bluebird slowdown

    levels = [min(100, int(v * condition_mult * pressure_mult)) for v in normalized]
    peak_level = max(levels) if levels else 0

    labels_12h = [
        "12 AM",
        "1 AM",
        "2 AM",
        "3 AM",
        "4 AM",
        "5 AM",
        "6 AM",
        "7 AM",
        "8 AM",
        "9 AM",
        "10 AM",
        "11 AM",
        "12 PM",
        "1 PM",
        "2 PM",
        "3 PM",
        "4 PM",
        "5 PM",
        "6 PM",
        "7 PM",
        "8 PM",
        "9 PM",
        "10 PM",
        "11 PM",
    ]
    short_labels = [
        "12a",
        "1a",
        "2a",
        "3a",
        "4a",
        "5a",
        "6a",
        "7a",
        "8a",
        "9a",
        "10a",
        "11a",
        "12p",
        "1p",
        "2p",
        "3p",
        "4p",
        "5p",
        "6p",
        "7p",
        "8p",
        "9p",
        "10p",
        "11p",
    ]

    timeline = []
    for h in range(24):
        level = levels[h]
        if level >= 75:
            tag = "prime"
        elif level >= 50:
            tag = "high"
        elif level >= 30:
            tag = "med"
        else:
            tag = "low"

        seen: set[str] = set()
        deduped = []
        for p in reason_parts[h]:
            if p not in seen:
                seen.add(p)
                deduped.append(p)
        reason = " · ".join(deduped)

        tide_evt = tide_events.get(h)
        timeline.append(
            {
                "hour": h,
                "label": labels_12h[h],
                "short_label": short_labels[h],
                "level": level,
                "tag": tag,
                "is_now": h == now_hour,
                "peak": level == peak_level and level >= 50,
                "reason": reason,
                # Overlay rail metadata (aligned to the same hourly columns).
                "sun": sun_events.get(h, ""),
                "tide": (tide_evt or {}).get("type", "").lower(),
                "tide_time": (tide_evt or {}).get("time", ""),
                "feeding": feeding_tag[h],
            }
        )

    return timeline

def build_share_text(forecast: dict[str, Any]) -> str:
    """Build a plain-text summary of the forecast for sharing."""
    lines = []
    loc = forecast.get("location_name", "")
    if loc:
        lines.append(f"Fishing Forecast — {loc}")
    else:
        lines.append("Fishing Forecast")

    c = forecast.get("conditions", {})
    verdict = c.get("verdict", "")
    if verdict:
        lines.append(f"Verdict: {verdict}")

    wind = c.get("wind", "")
    waves = c.get("waves", "")
    temp = c.get("water_temp_f", "")
    if wind:
        lines.append(f"Wind: {wind}")
    if waves:
        lines.append(f"Waves: {waves}")
    if temp:
        lines.append(f"Water: {temp}°F")

    species = forecast.get("species", [])
    if species:
        top = species[:5]
        lines.append("")
        lines.append("Top Species:")
        for sp in top:
            activity = sp.get("activity", "")
            tag = f" [{activity}]" if activity else ""
            lines.append(f"  • {sp['name']}{tag}")

    return "\n".join(lines)

def build_trip_setup(
    forecast: dict[str, Any],
    profile: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Build a personalised tackle/bait card for today's trip.

    Combines the user's gear preferences (lures / live bait / cut bait) with
    the top-ranked species data and current conditions to produce a short,
    actionable list of exactly what to pack and how to fish it today.

    Returns None when there's not enough species data to be useful.
    """
    profile = profile or {}
    species_list = forecast.get("species") or []
    if not species_list:
        return None

    # ── Gear preferences ──────────────────────────────────────────────────
    wants_lures = profile.get("lures") not in ("no",)
    wants_live = profile.get("live_bait") not in ("no",)
    wants_cut = profile.get("cut_bait") not in ("no",)
    wants_bait = wants_live or wants_cut
    has_profile = bool(profile)

    # When no profile exists, show everything so first-time users get value.
    if not has_profile:
        wants_lures = wants_bait = True

    # ── Pick working species (Hot/Active, up to 3) ────────────────────────
    working = [s for s in species_list[:6] if s.get("activity") in ("Hot", "Active")][
        :3
    ]
    if not working:
        working = species_list[:2]

    # ── Conditions context ────────────────────────────────────────────────
    cond = forecast.get("conditions", {})
    water_temp: Optional[float] = cond.get("water_temp_f")
    waves_str: str = cond.get("waves") or ""
    wind_str: str = cond.get("wind") or ""
    tide_state: str = (forecast.get("tide_state") or "").lower()

    # Parse max wave height (e.g. "2-4 ft" → 4)
    max_wave = 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*ft", waves_str)
    if m:
        max_wave = float(m.group(1))

    # Parse max wind knots (e.g. "S 16-21 kt" → 21)
    max_wind = 0.0
    wind_nums = re.findall(r"\d+", wind_str)
    if wind_nums:
        max_wind = float(max(int(x) for x in wind_nums))

    murky = max_wave >= 3 or max_wind >= 18
    cold_water = water_temp is not None and water_temp < 58
    warm_water = water_temp is not None and water_temp > 72

    # ── Sunrise/sunset for topwater window detection ───────────────────────
    now_hour = datetime.now().hour
    is_low_light = now_hour < 8 or now_hour >= 18

    # ── Harvest unique values from species ────────────────────────────────
    def _tokens(field: str) -> list[str]:
        """Collect unique comma-separated tokens across the working species."""
        seen: list[str] = []
        seen_lower: set[str] = set()
        for sp in working:
            for tok in (sp.get(field) or "").split(","):
                tok = tok.strip()
                tl = tok.lower()
                if tok and tl not in seen_lower:
                    seen_lower.add(tl)
                    seen.append(tok)
        return seen

    raw_lures = _tokens("lures")
    raw_bait = _tokens("bait")
    raw_rig = _tokens("rig")

    # ── Build rows ─────────────────────────────────────────────────────────
    rows: list[dict[str, str]] = []

    # -- Lure suggestions --
    if wants_lures and raw_lures:
        lure_picks = raw_lures[:4]
        # Prepend topwater if conditions favour it
        if (
            is_low_light
            and warm_water
            and not any("topwater" in lure.lower() for lure in lure_picks)
        ):
            lure_picks = ["Topwater plugs (early/late)"] + lure_picks[:3]
        rows.append({"icon": "lure", "label": "Lures", "value": ", ".join(lure_picks)})

    # -- Lure color / action tip --
    if wants_lures:
        if murky:
            rows.append(
                {
                    "icon": "color",
                    "label": "Color/action",
                    "value": "Chartreuse, white, or bright orange \u2014 murky water today",
                }
            )
        elif cold_water:
            rows.append(
                {
                    "icon": "color",
                    "label": "Color/action",
                    "value": "Natural colors, slow-roll or dead-stick \u2014 cold water slows metabolism",
                }
            )
        elif warm_water and is_low_light:
            rows.append(
                {
                    "icon": "color",
                    "label": "Color/action",
                    "value": "Dark silhouettes (black/purple) for low-light topwater",
                }
            )

    # -- Bait suggestions --
    if wants_bait and raw_bait:
        rows.append({"icon": "bait", "label": "Bait", "value": ", ".join(raw_bait[:4])})

    # -- Rig --
    if raw_rig:
        rows.append({"icon": "rig", "label": "Rig", "value": ", ".join(raw_rig[:2])})

    # -- Hook and sinker from primary species --
    primary = working[0]
    if primary.get("hook_size"):
        rows.append({"icon": "hook", "label": "Hook", "value": primary["hook_size"]})
    if primary.get("sinker"):
        sinker = primary["sinker"]
        if murky and "pyramid" not in sinker.lower():
            sinker += " \u2014 go heavier in rough surf"
        rows.append({"icon": "weight", "label": "Weight", "value": sinker})

    # -- Fishing-type-specific tackle tips --
    ft = set(profile.get("fishing_types") or [])
    if "fly" in ft:
        if murky:
            rows.append(
                {
                    "icon": "tip",
                    "label": "Fly tip",
                    "value": "Murky water — use large, dark Clouser or Deceiver patterns with rattle beads; fish sense vibration over sight",
                }
            )
        elif cold_water:
            rows.append(
                {
                    "icon": "tip",
                    "label": "Fly tip",
                    "value": "Cold water — slow down your strip retrieve; a near-dead-drift crab or shrimp pattern often outfishes a fast retrieve",
                }
            )
        else:
            rows.append(
                {
                    "icon": "tip",
                    "label": "Fly tip",
                    "value": "Lead fish by 3–6 feet; let the fly sink to eye level before starting your strip retrieve",
                }
            )
    if "wade" in ft:
        if murky:
            rows.append(
                {
                    "icon": "tip",
                    "label": "Wade tip",
                    "value": "Low visibility — switch to scented soft plastics or live shrimp; rely on noise (rattles, popping cork) over sight-fishing",
                }
            )
        else:
            rows.append(
                {
                    "icon": "tip",
                    "label": "Wade tip",
                    "value": "Clear water sight-fishing — crouch low on approach, cast past the fish and bring the lure through the zone",
                }
            )
    if "kayak" in ft:
        rows.append(
            {
                "icon": "tip",
                "label": "Kayak tip",
                "value": "Anchor upcurrent of your target structure and drift baits naturally into the strike zone rather than casting into the current",
            }
        )
    if "bridge" in ft:
        if tide_state == "rising":
            rows.append(
                {
                    "icon": "tip",
                    "label": "Bridge tip",
                    "value": "Incoming tide — position on the upcurrent side; fish sweep bait through shadow lines under the bridge",
                }
            )
        elif tide_state == "falling":
            rows.append(
                {
                    "icon": "tip",
                    "label": "Bridge tip",
                    "value": "Outgoing tide — position on the downcurrent side of pilings; fish face into current waiting for bait to come to them",
                }
            )
        else:
            rows.append(
                {
                    "icon": "tip",
                    "label": "Bridge tip",
                    "value": "Fish the shadow lines where light meets darkness; snook and tarpon ambush prey along these edges after dark",
                }
            )
    if "jetty" in ft:
        rows.append(
            {
                "icon": "tip",
                "label": "Jetty tip",
                "value": "Work the tip of the jetty on an incoming tide for the best mix of species; predators stage here to intercept bait flushed out of the inlet",
            }
        )

    # -- Conditions-driven tackle tip --
    if tide_state == "rising":
        rows.append(
            {
                "icon": "tip",
                "label": "Tide tip",
                "value": "Incoming tide \u2014 work bait close to the wash; predators follow the surge",
            }
        )
    elif tide_state == "falling":
        rows.append(
            {
                "icon": "tip",
                "label": "Tide tip",
                "value": "Falling tide \u2014 focus on deeper holes and channels as water drains",
            }
        )

    # -- Species tip --
    for sp in working:
        if sp.get("tip"):
            rows.append({"icon": "tip", "label": "Pro tip", "value": sp["tip"]})
            break

    if not rows:
        return None

    # ── Headline ──────────────────────────────────────────────────────────
    sp_names = [sp["name"].split(" (")[0] for sp in working[:2]]
    if has_profile and not wants_lures and wants_bait:
        headline = "Bait setup for " + " & ".join(sp_names)
    elif has_profile and wants_lures and not wants_bait:
        headline = "Lure setup for " + " & ".join(sp_names)
    else:
        headline = "Tackle for " + " & ".join(sp_names)

    return {
        "headline": headline,
        "rows": rows,
        "species_names": [sp["name"] for sp in working],
    }


