"""Forecast assembly, conditions analysis, and advisory features."""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo

from locations import get_fallback_conditions, get_monthly_water_temps

from services.astro import _sun_times, compute_lunar_details, compute_solunar_times, compute_twilight_times
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
    fetch_water_temperature,
    get_water_temp,
)
from services.nws import (
    NWS_MARINE_ZONE,
    _MPH_TO_KNOTS,
    _try_nws_forecast,
    _try_nws_gridpoint,
    _fetch_nws_extended,
    fetch_current_weather,
    fetch_state_alerts,
    fetch_weather_alerts,
    parse_conditions,
)
from domain.species import (
    SPECIES_DB,
    _get_technique_tip,
    _score_species,
    _species_matches_profile,
    build_bait_ranking,
    build_natural_bait_chart,
    build_rig_recommendations,
    build_species_calendar,
    build_species_ranking,
)

logger = logging.getLogger(__name__)

FORECAST_VERSION = "v1.0.0"

# Generic mid-Atlantic historical monthly averages used as the absolute
# last resort when no location is set.
MONTHLY_AVG_WIND: Dict[int, Tuple[float, float]] = {
    1: (8, 15), 2: (8, 16), 3: (9, 16), 4: (8, 15), 5: (7, 13), 6: (6, 12),
    7: (5, 11), 8: (5, 11), 9: (7, 14), 10: (7, 14), 11: (7, 14), 12: (8, 15),
}
MONTHLY_AVG_WAVES: Dict[int, Tuple[float, float]] = {
    1: (2, 4), 2: (2, 4), 3: (2, 4), 4: (1, 3), 5: (1, 3), 6: (1, 2),
    7: (1, 2), 8: (1, 2), 9: (2, 4), 10: (2, 4), 11: (2, 4), 12: (2, 4),
}
MONTHLY_AVG_WIND_DIR: Dict[int, str] = {
    1: "NW", 2: "NW", 3: "SW", 4: "SW", 5: "SW", 6: "SW",
    7: "SW", 8: "SW", 9: "NE", 10: "NE", 11: "NW", 12: "NW",
}

# Default coordinates
_LAT = 34.2104
_LNG = -77.7964

# -- Source 5: Seasonal averages (ALWAYS succeeds) --------------------------

def _seasonal_averages(month: int) -> Tuple[Tuple[float, float], Tuple[float, float], str]:
    """Historical monthly averages -- the last resort. Never fails."""
    return (
        MONTHLY_AVG_WIND[month],
        MONTHLY_AVG_WAVES[month],
        MONTHLY_AVG_WIND_DIR[month],
    )



# -- Combined fetcher -------------------------------------------------------

def get_marine_conditions(
    month: int,
    location: Optional[Dict[str, Any]] = None,
    sources_used: Optional[List[str]] = None,
    fallbacks_triggered: Optional[List[str]] = None,
) -> Tuple[Tuple[float, float], Tuple[float, float], str]:
    """Get marine conditions, trying every source until we have full data.

    Returns (wind_range, wave_range, wind_dir).  Guaranteed to never return
    None for any field -- seasonal averages fill any remaining gaps.
    """
    nws_zone = (location or {}).get("nws_zone", NWS_MARINE_ZONE)
    ndbc_list = (location or {}).get("ndbc_stations", [s[0] for s in NDBC_STATIONS])
    coops_id = (location or {}).get("coops_station", WATER_TEMP_STATION)
    loc_lat = (location or {}).get("lat", _LAT)
    loc_lng = (location or {}).get("lng", _LNG)

    wind_range: Optional[Tuple[float, float]] = None
    wave_range: Optional[Tuple[float, float]] = None
    wind_dir: Optional[str] = None

    sources: List[Tuple[str, Any]] = [
        ("NWS zone forecast", lambda: _try_nws_forecast(nws_zone)),
    ]
    for sid in ndbc_list:
        sources.append((f"NDBC {sid}", lambda s=sid: _try_ndbc_station(s)))
    sources.append(("NOAA CO-OPS wind", lambda: _try_coops_wind(coops_id)))
    sources.append(("NWS gridpoint forecast", lambda: _try_nws_gridpoint(loc_lat, loc_lng)))

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
            logger.debug("%s unavailable: %s", name, exc)

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
        location: Optional[Dict[str, Any]] = None,
        sources_used: Optional[List[str]] = None,
        fallbacks_triggered: Optional[List[str]] = None,
    ) -> Tuple[Tuple[float, float], Tuple[float, float], str]:
        return self._run_with_retries(
            "marine forecast",
            lambda: get_marine_conditions(month, location, sources_used=sources_used, fallbacks_triggered=fallbacks_triggered),
            default=_seasonal_averages(month),
        )


class TidePredictionService(ExternalDataService):
    def get_tide_predictions(
        self,
        now: datetime,
        location: Optional[Dict[str, Any]] = None,
        tz_name: str = "America/New_York",
    ) -> Dict[str, Any]:
        coops_id = (location or {}).get("coops_station", WATER_TEMP_STATION)
        tides = self._run_with_retries(
            "tide predictions",
            lambda: fetch_tide_predictions(coops_id, tz_name),
            default=[],
        ) or []
        if not tides:
            return {}

        today_date_str = now.strftime("%Y%m%d")
        today_tides = [t for t in tides if t.get("date_str") == today_date_str]
        result: Dict[str, Any] = {"tides": today_tides}
        chart_json = build_tide_chart_svg(today_tides)
        if chart_json:
            result["tide_chart"] = chart_json

        current_hour = now.hour + now.minute / 60
        tide_state = ""
        for i in range(len(tides) - 1):
            t_now = tides[i].get("hour", 0)
            t_next = tides[i + 1].get("hour", 24)
            if t_now <= current_hour < t_next:
                tide_state = "Rising" if tides[i + 1].get("type") == "High" else "Falling"
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
    def get_barometric_pressure(self, location: Optional[Dict[str, Any]] = None) -> Optional[float]:
        return self._run_with_retries(
            "barometric pressure",
            lambda: fetch_barometric_pressure(location),
            default=None,
        )


class WeatherDataService(ExternalDataService):
    def get_weather_alerts(self, lat: float, lng: float) -> List[Dict[str, Any]]:
        return self._run_with_retries(
            "weather alerts",
            lambda: fetch_weather_alerts(lat, lng),
            default=[],
        ) or []

    def get_state_alerts(self, state_code: str) -> List[Dict[str, Any]]:
        return self._run_with_retries(
            "state weather alerts",
            lambda: fetch_state_alerts(state_code),
            default=[],
        ) or []

    def get_current_weather(self, lat: float, lng: float) -> Optional[Dict[str, Any]]:
        return self._run_with_retries(
            "current weather",
            lambda: fetch_current_weather(lat, lng),
            default=None,
        )


class EnvironmentalDataService(ExternalDataService):
    def get_coops_environmental(self, station_id: str) -> Dict[str, float]:
        return self._run_with_retries(
            "coops environmental",
            lambda: fetch_coops_environmental_metrics(station_id),
            default={},
        ) or {}

    def get_currents(self, station_id: str, tz_name: str) -> List[Dict[str, str]]:
        return self._run_with_retries(
            "currents predictions",
            lambda: fetch_currents_predictions(station_id, tz_name),
            default=[],
        ) or []

    def get_current_observation(self, station_id: str, tz_name: str) -> Optional[Dict[str, str]]:
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
    ) -> Tuple[Optional[datetime], Optional[datetime], str]:
        values = self._run_with_retries(
            "sun times",
            lambda: _sun_times(now, lat, lng, tz_name),
            default=(None, None),
        )
        sunrise, sunset = values
        if sunrise and sunset:
            return sunrise, sunset, f"{sunrise.strftime('%-I:%M %p')} / {sunset.strftime('%-I:%M %p')}"
        return None, None, "Unavailable"

    def get_solunar_times(self, now: datetime, lat: float, lng: float, tz_name: str) -> Dict[str, Any]:
        return self._run_with_retries(
            "solunar",
            lambda: compute_solunar_times(now, lat, lng, tz_name),
            default={},
        ) or {}

    def get_twilight_times(self, now: datetime, lat: float, lng: float, tz_name: str) -> Dict[str, str]:
        return self._run_with_retries(
            "twilight",
            lambda: compute_twilight_times(now, lat, lng, tz_name),
            default={},
        ) or {}

    def get_lunar_details(self, now: datetime, lng: float, tz_name: str) -> Dict[str, Any]:
        return self._run_with_retries(
            "lunar details",
            lambda: compute_lunar_details(now, lng, tz_name),
            default={},
        ) or {}


class ForecastBuilder:
    """Central forecast orchestrator for external services + domain assembly."""

    def __init__(self) -> None:
        self.marine_service = MarineForecastService()
        self.tide_service = TidePredictionService()
        self.buoy_service = BuoyDataService()
        self.weather_service = WeatherDataService()
        self.environment_service = EnvironmentalDataService()
        self.astro_service = AstronomyService()


def classify_conditions(
    wind_range: Optional[Tuple[float, float]],
    wave_range: Optional[Tuple[float, float]],
    wind_dir: str = "",
    water_temp_f: Optional[float] = None,
    is_live_temp: bool = False,
    tide_state: str = "",
    tides: Optional[List[Dict[str, Any]]] = None,
    sunrise: Optional[datetime] = None,
    sunset: Optional[datetime] = None,
    now: Optional[datetime] = None,
    solunar: Optional[Dict[str, Any]] = None,
    coast: str = "east",
) -> str:
    """Classify fishability using all available marine + astronomical signals.

    Produces a 5-tier verdict: Excellent / Good / Fair / Challenging / Poor.
    """
    if wind_range is None or wave_range is None:
        return "Unknown"

    score = 50.0
    wind_max = wind_range[1]
    wave_max = wave_range[1]

    # Wind speed (primary safety + fishability signal)
    if wind_max <= 8:
        score += 14
    elif wind_max <= 12:
        score += 8
    elif wind_max <= 16:
        score += 2
    elif wind_max <= 20:
        score -= 8
    elif wind_max <= 25:
        score -= 16
    else:
        score -= 26

    # Wave height (primary surf-access signal)
    if wave_max <= 1.5:
        score += 10
    elif wave_max <= 3:
        score += 6
    elif wave_max <= 5:
        score -= 4
    elif wave_max <= 7:
        score -= 12
    else:
        score -= 22

    # Wind direction heuristic by coast (offshore usually cleaner water).
    if wind_dir:
        offshore_dirs = {"W", "NW", "NNW", "N", "WNW"}
        onshore_dirs = {"E", "ENE", "ESE", "NE", "SE"}
        if coast == "west":
            offshore_dirs = {"E", "ENE", "ESE", "NE", "SE"}
            onshore_dirs = {"W", "NW", "NNW", "N", "WNW"}
        if wind_dir in offshore_dirs:
            score += 4
        elif wind_dir in onshore_dirs:
            score -= 4

    # Water temp comfort/activity proxy (still species-specific elsewhere).
    if water_temp_f is not None:
        if 58 <= water_temp_f <= 78:
            score += 6
        elif 50 <= water_temp_f < 58 or 78 < water_temp_f <= 84:
            score += 2
        else:
            score -= 4
        if is_live_temp:
            score += 1

    # Tide state and tide range (bigger movement often improves feeding windows).
    if tide_state in {"Rising", "Falling"}:
        score += 2
    if tides:
        heights = [float(t.get("height", 0.0)) for t in tides if t.get("height") is not None]
        if heights:
            tide_range = max(heights) - min(heights)
            if tide_range >= 4:
                score += 5
            elif tide_range >= 2:
                score += 2

        # Bonus near tide turn windows (high/low slack periods often productive).
        if now:
            nearest_delta = None
            for t in tides:
                th = t.get("hour")
                if th is None:
                    continue
                tide_dt = now.replace(hour=int(th) % 24, minute=int((float(th) % 1) * 60), second=0, microsecond=0)
                delta = abs((tide_dt - now).total_seconds())
                nearest_delta = delta if nearest_delta is None else min(nearest_delta, delta)
            if nearest_delta is not None and nearest_delta <= 90 * 60:
                score += 3

    # Light windows around sunrise/sunset are usually better bite periods.
    if now and sunrise and sunset:
        if abs((now - sunrise).total_seconds()) <= 2 * 3600:
            score += 4
        if abs((sunset - now).total_seconds()) <= 2 * 3600:
            score += 4

    # Solunar quality + illumination.
    if solunar:
        rating_bonus = {
            "Excellent": 8,
            "Good": 4,
            "Fair": 0,
            "Poor": -6,
        }
        score += rating_bonus.get(solunar.get("rating", ""), 0)

        illum = solunar.get("illumination_pct")
        if isinstance(illum, (int, float)):
            if 40 <= illum <= 85:
                score += 2
            elif illum < 10 or illum > 95:
                score += 1

    score = max(0, min(100, score))
    if score >= 80:
        return "Excellent"
    if score >= 64:
        return "Good"
    if score >= 48:
        return "Fair"
    if score >= 32:
        return "Challenging"
    return "Poor"



def build_multiday_outlook(
    now: datetime,
    location: Optional[Dict[str, Any]] = None,
    fishing_types: Optional[List[str]] = None,
    targets: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Build a 3-day outlook with conditions and fishability.

    Each day includes: day_label, wind summary, wave estimate,
    water temp estimate, and a fishability verdict.

    If *fishing_types* or *targets* are provided (from the user's profile),
    the per-day top-species list is filtered to match.
    """
    loc_lat = (location or {}).get("lat", _LAT)
    loc_lng = (location or {}).get("lng", _LNG)
    tz_name = (location or {}).get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)

    # Try NWS extended forecast for wind data
    nws_periods = _fetch_nws_extended(loc_lat, loc_lng)

    def _estimate_wave_range_from_wind(day_wind_range: Optional[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
        """Estimate wave range from forecast wind when marine seas are unavailable."""
        if not day_wind_range:
            return None
        avg_kt = (day_wind_range[0] + day_wind_range[1]) / 2
        low_ft = max(1.0, float(math.floor(avg_kt / 6)))
        high_ft = max(low_ft + 1.0, float(math.ceil(avg_kt / 4)))
        high_ft = min(high_ft, 12.0)
        return (low_ft, high_ft)

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
                    except ValueError:
                        logger.debug("Unable to parse NWS period startTime: %s", start_raw)

                # Fallback for API payloads that omit startTime.
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
                wind_str = f"{wd} {low_kt}-{high_kt} kt" if wd else f"{low_kt}-{high_kt} kt"

        if not wind_str:
            # Use regional fallback
            fb_wind, fb_waves, fb_dir = get_fallback_conditions(
                location or {}, future_month,
            ) if location else (
                MONTHLY_AVG_WIND[future_month],
                MONTHLY_AVG_WAVES[future_month],
                MONTHLY_AVG_WIND_DIR[future_month],
            )
            if isinstance(fb_wind, tuple):
                wind_str = f"{fb_dir} {int(fb_wind[0])}-{int(fb_wind[1])} kt"
                wind_range = fb_wind
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
                fb_wind_r, fb_wave_r, _ = get_fallback_conditions(location, future_month)
                wave_str = f"{int(fb_wave_r[0])}-{int(fb_wave_r[1])} ft"
                wave_range = fb_wave_r
        else:
            if not wave_range:
                wave_avg = MONTHLY_AVG_WAVES.get(future_month, (1, 3))
                wave_str = f"{int(wave_avg[0])}-{int(wave_avg[1])} ft"
                wave_range = wave_avg

        # --- Region + water temperature context ---
        cr = (location or {}).get("conditions_region", "atlantic_mid")
        coast = "west" if cr.startswith("pacific") else ("hawaii" if cr.startswith("hawaii") else "east")
        if location:
            monthly_temps = get_monthly_water_temps(location)
            future_water_temp = float(monthly_temps[future_month])
        else:
            future_water_temp = float(MONTHLY_AVG_WATER_TEMP_F[future_month])

        # --- Fishability verdict ---
        if wind_range and wave_range:
            future_sunrise = None
            future_sunset = None
            future_solunar = None
            try:
                future_sunrise, future_sunset = _sun_times(future, loc_lat, loc_lng, tz_name)
            except Exception:
                pass
            try:
                future_solunar = compute_solunar_times(future, loc_lat, loc_lng, tz_name)
            except Exception:
                pass
            verdict = classify_conditions(
                wind_range,
                wave_range,
                wind_dir=wind_dir_day,
                water_temp_f=future_water_temp,
                now=future.replace(hour=12, minute=0, second=0, microsecond=0),
                sunrise=future_sunrise,
                sunset=future_sunset,
                solunar=future_solunar,
                coast=coast,
            )
        else:
            verdict = "Unknown"

        # --- Top species for this day ---
        wind_coast = "west" if coast == "west" else "east"
        outlook_fish_region = (location or {}).get("fish_region", "")
        top_species_names: List[str] = []
        species_scores: List[Tuple[str, float]] = []
        for sp in SPECIES_DB:
            if sp.get("coast", "east") != coast:
                continue
            if outlook_fish_region and "regions" in sp and outlook_fish_region not in sp["regions"]:
                continue
            if not _species_matches_profile(sp["name"], fishing_types, targets):
                continue
            s = _score_species(
                sp, future_month, future_water_temp,
                wind_dir=None,
                wind_range=wind_range,
                wave_range=wave_range,
                hour=12,
                coast=wind_coast,
            )
            if s > 20:
                species_scores.append((sp["name"], s))
        species_scores.sort(key=lambda x: x[1], reverse=True)
        top_species_names = [name for name, _ in species_scores[:5]]

        days.append({
            "day": day_label,
            "date": date_label,
            "wind": wind_str,
            "waves": wave_str,
            "verdict": verdict,
            "top_species": top_species_names,
        })

    return days


# -- Spot-specific fishing tips based on conditions -------------------------

def build_spot_tips(
    wind_range: Optional[Tuple[float, float]] = None,
    wave_range: Optional[Tuple[float, float]] = None,
    wind_dir: str = "",
    hour: int = 12,
    month: int = 6,
    coast: str = "east",
    tide_state: str = "",
) -> List[Dict[str, str]]:
    """Generate 3-5 actionable fishing tips based on current conditions.

    Each tip has an 'icon' (emoji-free label), 'title', and 'detail'.
    """
    tips: List[Dict[str, str]] = []

    # Wind-based tips
    avg_wind = 0.0
    if wind_range:
        avg_wind = (wind_range[0] + wind_range[1]) / 2

    if avg_wind > 20:
        tips.append({
            "icon": "wind", "title": "Heavy Wind Strategy",
            "detail": "Use heavier sinkers (4-6 oz) to hold bottom. "
                      "Fish the lee side of piers and jetties for calmer pockets. "
                      "Shorten leaders to reduce tangles.",
        })
    elif avg_wind > 12:
        tips.append({
            "icon": "wind", "title": "Moderate Wind",
            "detail": "Standard 2-4 oz sinkers should hold. Wind chop stirs up bait — "
                      "fish are often more active. Cast at an angle to the wind for better distance.",
        })
    elif avg_wind < 6:
        tips.append({
            "icon": "wind", "title": "Calm Conditions",
            "detail": "Light tackle shines today. Use lighter leaders and smaller presentations. "
                      "Fish may be more line-shy in clear, calm water.",
        })

    # Wave-based tips
    avg_wave = 0.0
    if wave_range:
        avg_wave = (wave_range[0] + wave_range[1]) / 2

    if avg_wave > 4:
        tips.append({
            "icon": "waves", "title": "Heavy Surf",
            "detail": "Fish the troughs between sandbars where fish shelter from wave energy. "
                      "Pyramid sinkers grip sandy bottoms better than egg sinkers in surf.",
        })
    elif avg_wave > 2:
        tips.append({
            "icon": "waves", "title": "Moderate Surf",
            "detail": "Cast beyond the breakers to the second sandbar. "
                      "The stirred-up sand exposes sand fleas and crabs, drawing fish to feed.",
        })
    elif avg_wave < 1.5:
        tips.append({
            "icon": "waves", "title": "Flat Surf",
            "detail": "Fish closer to structure — jetties, pilings, and rocky outcrops. "
                      "Clear water means lighter line and natural-colored baits work best.",
        })

    # Tide-based tips
    if tide_state == "Rising":
        tips.append({
            "icon": "tide", "title": "Rising Tide Tactics",
            "detail": "Incoming water pushes bait toward shore — position yourself "
                      "at points where current funnels through cuts and inlets. "
                      "Fish the first and second troughs as water fills them.",
        })
    elif tide_state == "Falling":
        tips.append({
            "icon": "tide", "title": "Falling Tide Tactics",
            "detail": "Outgoing water concentrates baitfish at channel mouths and drain points. "
                      "Set up where water flows out from marshes and estuaries — "
                      "predators stack up to ambush departing bait.",
        })

    # Time-of-day tips
    if 5 <= hour <= 7:
        tips.append({
            "icon": "time", "title": "Early Bird Advantage",
            "detail": "Dawn is prime time — get lines in the water before sunrise. "
                      "Topwater lures and live bait under corks excel in the low-light bite window.",
        })
    elif 17 <= hour <= 20:
        tips.append({
            "icon": "time", "title": "Evening Bite",
            "detail": "Fish feed aggressively before dark. Switch to darker-colored lures as "
                      "light fades — fish rely more on silhouette and vibration at dusk.",
        })
    elif 10 <= hour <= 14:
        tips.append({
            "icon": "time", "title": "Midday Approach",
            "detail": "Fish deeper structure and shaded areas during bright sun. "
                      "Piers cast shadows that attract baitfish — focus on the shadow line.",
        })

    # Seasonal tips
    if month in (11, 12, 1, 2):
        tips.append({
            "icon": "season", "title": "Cold Water Tips",
            "detail": "Slow your presentation — cold fish won't chase fast baits. "
                      "Fish the warmest part of the day (10 AM - 3 PM) when water warms slightly. "
                      "Bottom rigs with cut bait outperform artificials in winter.",
        })
    elif month in (6, 7, 8):
        tips.append({
            "icon": "season", "title": "Summer Patterns",
            "detail": "Early morning and late evening are most productive — avoid the midday heat. "
                      "Live bait stays livelier in a bucket with an aerator. "
                      "Night fishing produces excellent catches in warm months.",
        })

    return tips[:5]


def build_bite_alerts(
    verdict: str,
    species: List[Dict[str, Any]],
    pressure: Optional[Dict[str, Any]] = None,
    tide_state: str = "",
) -> List[Dict[str, str]]:
    """Generate bite alert notifications when conditions are especially good."""
    alerts: List[Dict[str, str]] = []

    # Hot species alert
    hot_species = [sp["name"] for sp in species if sp.get("activity") == "Hot"]
    if hot_species:
        if len(hot_species) >= 3:
            alerts.append({
                "type": "hot",
                "title": "Multiple species on fire!",
                "message": f"{', '.join(hot_species[:3])} are all rated HOT right now. This is a rare alignment of conditions.",
            })
        elif len(hot_species) == 1:
            alerts.append({
                "type": "hot",
                "title": f"{hot_species[0]} is on fire!",
                "message": "Conditions are dialed in for this species. Get your lines in the water.",
            })

    # Excellent conditions alert
    if verdict == "Excellent":
        alerts.append({
            "type": "excellent",
            "title": "Excellent fishing day!",
            "message": "Wind, waves, and temperatures are all in the sweet spot. Don't miss this one.",
        })

    # Falling pressure trigger
    if pressure and "falling" in pressure.get("trend", "").lower():
        p_val = pressure.get("pressure_mb", 0)
        if p_val and float(p_val) < 1010:
            alerts.append({
                "type": "pressure",
                "title": "Pre-front feeding window",
                "message": "Barometric pressure is dropping below 1010 mb — fish often feed aggressively before incoming weather.",
            })

    # Incoming tide + dawn/dusk
    if tide_state == "Rising":
        alerts.append({
            "type": "tide",
            "title": "Incoming tide active",
            "message": "Rising water pushes bait toward shore. Prime time for surf and pier fishing.",
        })

    return alerts[:3]


def pick_best_fishing_day(
    today_verdict: str,
    outlook: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Analyze today and 3-day outlook to recommend the best day to fish.

    Returns a dict with 'best_day', 'reason', and 'recommendation'.
    """
    # Score mapping for verdicts
    verdict_scores = {
        "Excellent": 5,
        "Good": 4,
        "Fair": 3,
        "Challenging": 2,
        "Poor": 1,
        "Unknown": 2,
    }

    best_day = "Today"
    best_score = verdict_scores.get(today_verdict, 2)
    best_verdict = today_verdict

    for day in outlook:
        v = day.get("verdict", "Unknown")
        s = verdict_scores.get(v, 2)
        n_species = len(day.get("top_species", []))
        # Bonus for having more active species
        s += min(n_species * 0.2, 1.0)
        if s > best_score:
            best_score = s
            best_day = day["day"]
            best_verdict = v

    if best_day == "Today":
        if best_score >= 4:
            recommendation = "Today looks great — get out there!"
        elif best_score >= 3:
            recommendation = "Decent day today, but conditions are fishable."
        else:
            recommendation = "Tough day today. Check back tomorrow."
    else:
        if best_score >= 4:
            recommendation = f"{best_day} has the best forecast — plan your trip then."
        else:
            recommendation = f"{best_day} looks slightly better, but all days are similar."

    return {
        "best_day": best_day,
        "verdict": best_verdict,
        "recommendation": recommendation,
    }


def build_gear_checklist(
    species: List[Dict[str, Any]],
    wind_range: Optional[Tuple[float, float]] = None,
    wave_range: Optional[Tuple[float, float]] = None,
    hour: int = 12,
    water_temp: float = 65.0,
    weather: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """Generate a conditions-aware packing list for a fishing trip."""
    items: List[Dict[str, str]] = []
    categories_seen: set = set()

    def _add(category: str, item: str, reason: str = "") -> None:
        key = f"{category}:{item}"
        if key not in categories_seen:
            categories_seen.add(key)
            items.append({"category": category, "item": item, "reason": reason})

    # ---- Always bring ----
    _add("Essentials", "Rod & reel (medium-heavy for surf)", "")
    _add("Essentials", "Tackle box with hooks, sinkers, swivels", "")
    _add("Essentials", "Bait cooler with ice", "")
    _add("Essentials", "Pliers & line cutter", "")
    _add("Essentials", "Fishing license", "Required in most states")

    # ---- Conditions-based ----
    max_wind = (wind_range[1] if wind_range else 10)
    max_wave = (wave_range[1] if wave_range else 2)

    if max_wind >= 15:
        _add("Conditions", "Heavy sinkers (4-6 oz)", "Strong wind requires extra weight")
        _add("Conditions", "Sand spike or rod holder", "Keep rods secure in high wind")

    if max_wave >= 4:
        _add("Conditions", "Waders or waterproof boots", "Heavy surf will splash")
        _add("Conditions", "Extra sinkers (pyramid style)", "Holds bottom in rough surf")

    if hour < 6 or hour >= 19:
        _add("Conditions", "Headlamp (red light mode)", "Preserve night vision")
        _add("Conditions", "Glow sticks or light-up bobbers", "Track your line in the dark")

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
            _add("Weather", "Extra water (1 gal minimum)", f"Air temp {air_temp}°F — stay hydrated")
            _add("Weather", "Cooling towel", "Beat the heat")

    # ---- Species-based ----
    has_shark = any("shark" in sp.get("name", "").lower() for sp in species[:10])
    has_king = any("king" in sp.get("name", "").lower() for sp in species[:10])
    has_flounder = any("flounder" in sp.get("name", "").lower() for sp in species[:10])

    if has_shark:
        _add("Species", "Wire leader (single-strand)", "Shark teeth cut mono/fluoro")
        _add("Species", "Heavy-duty dehooking tool", "Safe shark handling")

    if has_king:
        _add("Species", "Wire or heavy fluoro leader (60+ lb)", "Kings have sharp teeth")
        _add("Species", "Stinger rig components", "Standard for kingfish")

    if has_flounder:
        _add("Species", "Bucktail jig (white/chartreuse)", "Top flounder lure")

    # ---- Convenience ----
    _add("Convenience", "5-gallon bucket", "Bait storage, seat, catch bucket")
    _add("Convenience", "Towel / rags", "Clean hands between baiting")
    _add("Convenience", "Trash bag", "Leave no trace")

    return items


def build_conditions_explainer(
    wind_range: Optional[Tuple[float, float]] = None,
    wave_range: Optional[Tuple[float, float]] = None,
    wind_dir: Optional[str] = None,
    water_temp: float = 65.0,
    pressure: Optional[Dict[str, Any]] = None,
    tide_state: str = "",
    coast: str = "east",
) -> List[Dict[str, str]]:
    """Translate raw marine conditions into fishing-relevant plain English."""
    bullets: List[Dict[str, str]] = []

    # Wind analysis
    if wind_range:
        avg_wind = (wind_range[0] + wind_range[1]) / 2
        if avg_wind <= 8:
            bullets.append({
                "label": "Wind",
                "text": "Light winds make for calm conditions — great for float rigs and sight casting.",
                "impact": "positive",
            })
        elif avg_wind <= 15:
            bullets.append({
                "label": "Wind",
                "text": "Moderate breeze stirs up bait and adds turbidity. Good for surf fishing — fish move closer to feed.",
                "impact": "positive",
            })
        elif avg_wind <= 22:
            bullets.append({
                "label": "Wind",
                "text": "Strong winds make casting difficult and waves choppy. Use heavier sinkers and shorter leaders.",
                "impact": "neutral",
            })
        else:
            bullets.append({
                "label": "Wind",
                "text": "Near-gale conditions — tough fishing day. If you go, fish sheltered areas with heavy tackle.",
                "impact": "negative",
            })

    # Wind direction meaning
    if wind_dir:
        onshore_dirs = {
            "east": {"E", "ENE", "ESE", "SE", "NE"},
            "west": {"W", "WNW", "WSW", "SW", "NW"},
        }
        dirs = onshore_dirs.get(coast, onshore_dirs["east"])
        if wind_dir in dirs:
            bullets.append({
                "label": "Direction",
                "text": f"{wind_dir} wind pushes bait and murky water shoreward — predators follow to feed along the beach.",
                "impact": "positive",
            })
        else:
            bullets.append({
                "label": "Direction",
                "text": f"{wind_dir} (offshore) wind flattens the surf and clears the water. Better for sight fishing, tougher for surf bait fishing.",
                "impact": "neutral",
            })

    # Wave analysis
    if wave_range:
        avg_wave = (wave_range[0] + wave_range[1]) / 2
        if avg_wave <= 2:
            bullets.append({
                "label": "Waves",
                "text": "Flat to slight seas — ideal for pier fishing and wading. Fish may be less active in clear, calm water.",
                "impact": "neutral",
            })
        elif avg_wave <= 4:
            bullets.append({
                "label": "Waves",
                "text": "Moderate surf churns up sand fleas, crabs, and baitfish — prime conditions for the surf zone.",
                "impact": "positive",
            })
        else:
            bullets.append({
                "label": "Waves",
                "text": "Heavy surf concentrates bait in troughs between sandbars. Big fish feed hard but conditions are challenging.",
                "impact": "neutral",
            })

    # Pressure
    if pressure:
        trend = pressure.get("trend", "").lower()
        if "falling" in trend:
            bullets.append({
                "label": "Pressure",
                "text": "Falling pressure triggers a feeding frenzy — fish sense the change and eat aggressively before a front.",
                "impact": "positive",
            })
        elif "rising" in trend:
            bullets.append({
                "label": "Pressure",
                "text": "Rising pressure often means post-front clear skies. Fishing may be slow at first but improves as it stabilizes.",
                "impact": "neutral",
            })

    # Tide state
    if tide_state:
        if tide_state == "Rising":
            bullets.append({
                "label": "Tide",
                "text": "Incoming tide floods channels and flats with bait — one of the best times to fish from shore.",
                "impact": "positive",
            })
        else:
            bullets.append({
                "label": "Tide",
                "text": "Outgoing tide funnels bait through inlets and cuts — position yourself where current concentrates.",
                "impact": "positive",
            })

    return bullets[:5]


def build_safety_checklist(
    wind_range: Optional[Tuple[float, float]] = None,
    wave_range: Optional[Tuple[float, float]] = None,
    hour: int = 12,
    alerts: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    """Build a conditions-aware safety checklist for surf/pier fishing."""
    items: List[Dict[str, str]] = []

    # Always show basics
    items.append({
        "text": "Tell someone your fishing plan and expected return time",
        "icon": "info",
    })

    # Wave-based warnings
    if wave_range:
        max_wave = wave_range[1] if isinstance(wave_range, tuple) else 3
        if max_wave >= 6:
            items.append({
                "text": "High surf advisory — stay off jetties and rock structures",
                "icon": "warning",
            })
            items.append({
                "text": "Rip current risk is elevated — never wade beyond knee depth",
                "icon": "warning",
            })
        elif max_wave >= 4:
            items.append({
                "text": "Moderate surf — watch for sneaker waves and keep gear secured",
                "icon": "caution",
            })

    # Wind-based
    if wind_range:
        max_wind = wind_range[1] if isinstance(wind_range, tuple) else 10
        if max_wind >= 25:
            items.append({
                "text": "Gale-force winds — consider postponing your trip",
                "icon": "warning",
            })
        elif max_wind >= 15:
            items.append({
                "text": "Strong winds — secure coolers/gear and watch for blown tackle",
                "icon": "caution",
            })

    # Time-based
    if hour < 6 or hour >= 20:
        items.append({
            "text": "Fishing in the dark — bring a headlamp and reflective gear",
            "icon": "info",
        })
    if 10 <= hour <= 16:
        items.append({
            "text": "Peak sun hours — wear sunscreen (SPF 50+), hat, and polarized glasses",
            "icon": "info",
        })

    # Weather alerts
    if alerts:
        for alert in alerts:
            severity = alert.get("severity", "").lower()
            if "thunderstorm" in alert.get("event", "").lower():
                items.append({
                    "text": "Thunderstorm warning active — leave pier/water immediately if lightning is within 10 miles",
                    "icon": "warning",
                })
                break

    # General
    items.append({
        "text": "Bring plenty of water — dehydration reduces reaction time",
        "icon": "info",
    })
    items.append({
        "text": "Know the emergency number for your pier/beach (usually posted at entrance)",
        "icon": "info",
    })

    return items



def _uv_category(uv_index: float) -> Dict[str, str]:
    if uv_index <= 2:
        return {"level": "Low", "advice": "Minimal sun risk; sunglasses still recommended."}
    if uv_index <= 7:
        return {"level": "Moderate to High", "advice": "Use SPF 30+, hat, and lightweight sun shirt."}
    return {"level": "Very High to Extreme", "advice": "Limit direct midday exposure and reapply SPF often."}


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


def recompute_current_uv(location: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compute UV index for the current time at the given location.

    Intended for refreshing stale cached UV values at page-render time so the
    displayed UV always reflects the *current* sun position for the selected
    location, not the moment the forecast was originally generated.
    """
    tz_name = (location or {}).get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    lat = (location or {}).get("lat", _LAT)
    lng = (location or {}).get("lng", _LNG)
    try:
        sunrise, sunset = _sun_times(now, lat, lng, tz_name)
    except Exception:
        sunrise, sunset = None, None
    uv_index = _estimate_uv_index(now, sunrise, sunset, lat)
    return {"index": uv_index, **_uv_category(uv_index)}


def _rip_risk_from_conditions(wave_range: Optional[Tuple[float, float]], wind_range: Optional[Tuple[float, float]]) -> Dict[str, str]:
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


def _dew_point_f(temp_f: Optional[float], humidity_pct: Optional[float]) -> Optional[float]:
    if temp_f is None or humidity_pct is None or humidity_pct <= 0:
        return None
    temp_c = (temp_f - 32) * 5 / 9
    rh = max(1.0, min(float(humidity_pct), 100.0))
    a = 17.27
    b = 237.7
    alpha = ((a * temp_c) / (b + temp_c)) + math.log(rh / 100.0)
    dew_c = (b * alpha) / (a - alpha)
    return round(dew_c * 9 / 5 + 32, 1)


def _heat_index_f(temp_f: Optional[float], humidity_pct: Optional[float]) -> Optional[float]:
    if temp_f is None or humidity_pct is None:
        return None
    t = float(temp_f)
    rh = float(humidity_pct)
    if t < 80 or rh < 40:
        return None
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
          - 0.00683783 * t ** 2 - 0.05481717 * rh ** 2 + 0.00122874 * t ** 2 * rh
          + 0.00085282 * t * rh ** 2 - 0.00000199 * t ** 2 * rh ** 2)
    return round(hi, 1)


def _wind_chill_f(temp_f: Optional[float], wind_kt: Optional[float]) -> Optional[float]:
    if temp_f is None or wind_kt is None:
        return None
    t = float(temp_f)
    wind_mph = float(wind_kt) * 1.15078
    if t > 50 or wind_mph < 3:
        return None
    wc = 35.74 + 0.6215 * t - 35.75 * (wind_mph ** 0.16) + 0.4275 * t * (wind_mph ** 0.16)
    return round(wc, 1)


def _build_pier_info(location: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return location-aware planning details without requiring APIs."""
    loc = location or {}
    state = loc.get("state", "")
    coast_region = loc.get("conditions_region", "")
    default_hours = "Open daily; check seasonal maintenance closures"
    if state in {"FL", "HI", "CA"}:
        default_hours = "Sunrise to sunset (many piers extend hours in summer)"
    elif state in {"NJ", "NY", "MA", "RI", "CT"}:
        default_hours = "Dawn to dusk; winter weather closures possible"

    amenities = ["Restrooms nearby", "Public parking", "Fish-cleaning area varies by pier"]
    if coast_region.startswith("pacific"):
        amenities.append("Kelp/rock structure nearby")
    elif coast_region.startswith("gulf"):
        amenities.append("Baitfish schools common near lights and passes")
    else:
        amenities.append("Tidal cuts and sandbars nearby")

    return {
        "hours": loc.get("pier_hours", default_hours),
        "fees": loc.get("pier_fees", "Fees vary by municipality; verify before travel."),
        "contact": loc.get("pier_contact", f"{loc.get('name', 'Local')} parks/marina office"),
        "amenities": loc.get("amenities", amenities),
        "rules": loc.get("rules", ["No alcohol on pier", "Respect casting lanes", "Check local drone policy"]),
    }


def _build_education_cards(
    profile: Dict[str, Any],
    uv: Dict[str, Any],
    rip_current_risk: Dict[str, str],
) -> List[Dict[str, str]]:
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
        cards.append({
            "title": "Beginner tip",
            "text": "Start with bottom rigs and short casts. Focus on reading current seams before trying long-distance casting.",
        })
    if "pier" in fishing_types:
        cards.append({
            "title": "Pier etiquette",
            "text": "Call out when casting, avoid crossing lines, and net fish quickly to keep traffic moving safely.",
        })
    return cards


def _seasonality_highlights(forecast: Dict[str, Any]) -> List[Dict[str, str]]:
    """Summarize active calendar windows for top species with regulation hints."""
    highlights: List[Dict[str, str]] = []
    species_by_name = {sp.get("name"): sp for sp in forecast.get("species", [])}
    for row in (forecast.get("calendar") or [])[:5]:
        active = [m["abbr"] for m in row.get("months", []) if m.get("level") in {"peak", "good"}]
        if not active:
            continue
        reg = (species_by_name.get(row.get("name"), {}) or {}).get("regulation", {})
        note = ""
        if reg:
            parts = [reg.get("min_size", ""), reg.get("bag_limit", "")]
            note = " · ".join([p for p in parts if p])
        highlights.append({
            "species": row.get("name", ""),
            "season": f"Active: {', '.join(active[:6])}",
            "note": note,
        })
    return highlights


def generate_forecast(
    location: Optional[Dict[str, Any]] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate the complete fishing forecast.

    Fetches marine conditions and water temperature, classifies fishability,
    then dynamically determines which species are biting based on the current
    month and water temperature.  Rig recommendations are matched to active
    species.

    If ``profile`` is provided (from user's fishing profile), species are
    filtered to match the user's fishing style and target preferences.
    """
    tz_name = (location or {}).get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    month = now.month
    builder = ForecastBuilder()
    sources_used: List[str] = []
    fallbacks_triggered: List[str] = []
    location_id = (location or {}).get("id", "")
    started = time.perf_counter()
    logger.info("forecast.start location_id=%s forecast_version=%s", location_id, FORECAST_VERSION)

    wind_range, wave_range, wind_dir = builder.marine_service.get_marine_forecast(
        month,
        location,
        sources_used=sources_used,
        fallbacks_triggered=fallbacks_triggered,
    )
    water_temp, temp_is_live = get_water_temp(
        month,
        location,
        sources_used=sources_used,
        fallbacks_triggered=fallbacks_triggered,
    )

    def format_range(r: Optional[Tuple[float, float]], unit: str) -> str:
        if r is None:
            return "Unknown"
        low, high = r
        if low == high:
            return f"{low:.0f} {unit}"
        return f"{low:.0f}-{high:.0f} {unit}"

    loc_lat = (location or {}).get("lat", _LAT)
    loc_lng = (location or {}).get("lng", _LNG)
    sunrise, sunset, sun_str = builder.astro_service.get_sun_times(now, loc_lat, loc_lng, tz_name)

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
    conditions_region = (location or {}).get("conditions_region", "atlantic_mid")
    coast = "west" if conditions_region.startswith("pacific") else ("hawaii" if conditions_region.startswith("hawaii") else "east")

    loc_state = (location or {}).get("state", "")
    loc_fish_region = (location or {}).get("fish_region", "")
    profile = profile or {}
    species = build_species_ranking(
        month, water_temp,
        wind_dir=wind_dir,
        wind_range=wind_range,
        wave_range=wave_range,
        hour=now.hour,
        coast=coast,
        state=loc_state,
        fishing_types=profile.get("fishing_types"),
        targets=profile.get("targets"),
        fish_region=loc_fish_region,
    )
    rig_recommendations = build_rig_recommendations(species)

    loc_name = ""
    if location:
        loc_name = f"{location['name']}, {location['state']}"

    forecast: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "forecast_version": FORECAST_VERSION,
        "sources_used": sorted(set(sources_used)),
        "fallbacks_triggered": sorted(set(fallbacks_triggered)),
        "location_name": loc_name,
        "location_id": (location or {}).get("id", ""),
        "location_state": loc_state,
        "conditions": conditions,
        "species": species,
        "rig_recommendations": rig_recommendations,
        "bait_rankings": build_bait_ranking(species, month),
        "pier_info": _build_pier_info(location),
    }

    alerts = builder.weather_service.get_weather_alerts(loc_lat, loc_lng)
    if alerts:
        forecast["alerts"] = alerts
        sources_used.append("NWS weather alerts")
    else:
        fallbacks_triggered.append("weather_alerts_unavailable")

    if loc_state:
        state_alerts = builder.weather_service.get_state_alerts(loc_state)
        if state_alerts:
            forecast["state_alerts"] = state_alerts[:5]
            sources_used.append("NWS state alerts")


    # Barometric pressure
    pressure = builder.buoy_service.get_barometric_pressure(location)
    if pressure:
        forecast["pressure"] = pressure
        sources_used.append("NDBC barometric pressure")
    else:
        fallbacks_triggered.append("barometric_pressure_unavailable")

    # Current weather (air temp, humidity)
    weather = builder.weather_service.get_current_weather(loc_lat, loc_lng)
    if weather:
        forecast["weather"] = weather
        sources_used.append("NWS current weather")
    else:
        fallbacks_triggered.append("current_weather_unavailable")

    coops_station = (location or {}).get("coops_station", WATER_TEMP_STATION)
    env_metrics = builder.environment_service.get_coops_environmental(coops_station)
    if env_metrics:
        if weather:
            env_metrics.setdefault("air_temp_f", weather.get("air_temp_f"))
            env_metrics.setdefault("humidity_pct", weather.get("humidity"))
        forecast["environment"] = env_metrics
        sources_used.append("NOAA CO-OPS environmental")
    else:
        fallbacks_triggered.append("coops_environment_unavailable")

    currents = builder.environment_service.get_currents(coops_station, tz_name)
    current_observation = builder.environment_service.get_current_observation(coops_station, tz_name)
    if current_observation:
        currents = [current_observation, *currents]
        sources_used.append("NOAA currents observation")
    if currents:
        forecast["currents"] = currents
        sources_used.append("NOAA currents predictions")


    # Tide predictions
    tide_data = builder.tide_service.get_tide_predictions(now, location, tz_name)
    if tide_data:
        forecast.update(tide_data)
        sources_used.append("NOAA tide predictions")
    else:
        fallbacks_triggered.append("tide_predictions_unavailable")


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
            forecast["lunar_details"]["illumination_pct"] = solunar.get("illumination_pct")
            forecast["lunar_details"]["phase"] = solunar.get("moon_phase")
        sources_used.append("astronomy lunar details")

    uv_index = _estimate_uv_index(now, sunrise, sunset, loc_lat)
    forecast["uv"] = {"index": uv_index, **_uv_category(uv_index)}
    forecast["rip_current_risk"] = _rip_risk_from_conditions(wave_range, wind_range)
    forecast["education"] = _build_education_cards(profile, forecast["uv"], forecast["rip_current_risk"])

    humidity_for_dew = None
    temp_for_dew = None
    if forecast.get("environment"):
        humidity_for_dew = forecast["environment"].get("humidity_pct")
        temp_for_dew = forecast["environment"].get("air_temp_f")
    if forecast.get("weather"):
        humidity_for_dew = humidity_for_dew if humidity_for_dew is not None else forecast["weather"].get("humidity")
        temp_for_dew = temp_for_dew if temp_for_dew is not None else forecast["weather"].get("air_temp_f")
    dew_point = _dew_point_f(temp_for_dew, humidity_for_dew)
    derived_indices: Dict[str, float] = {}
    if dew_point is not None:
        derived_indices["dew_point_f"] = dew_point

    est_heat_index = _heat_index_f(temp_for_dew, humidity_for_dew)
    if est_heat_index is not None and not (forecast.get("weather") or {}).get("feels_like_f"):
        derived_indices["heat_index_f"] = est_heat_index

    wind_ref = wind_range[1] if wind_range else None
    est_wind_chill = _wind_chill_f(temp_for_dew, wind_ref)
    if est_wind_chill is not None and not (forecast.get("weather") or {}).get("wind_chill_f"):
        derived_indices["wind_chill_f"] = est_wind_chill

    if derived_indices:
        forecast["derived_indices"] = derived_indices

    # Final fishability verdict based on all available signals
    conditions["verdict"] = classify_conditions(
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
        coast=coast,
    )

    # Multi-day outlook (3 days)
    try:
        outlook = build_multiday_outlook(now, location)
        if outlook:
            forecast["outlook"] = outlook
    except Exception:
        pass

    # Best day to fish (trip planner)
    if forecast.get("outlook"):
        forecast["best_day"] = pick_best_fishing_day(
            forecast["conditions"]["verdict"],
            forecast["outlook"],
        )

    # Species availability calendar — shows popular regional target species,
    # not day-to-day bite conditions.
    forecast["calendar"] = build_species_calendar(species, location, fish_region=loc_fish_region)
    forecast["seasonality"] = _seasonality_highlights(forecast)

    # Natural bait availability (bait DB only has "east"/"west" entries)
    bait_coast = "west" if coast == "west" else "east"
    forecast["natural_bait"] = build_natural_bait_chart(month, bait_coast)

    # Spot tips based on current conditions
    forecast["spot_tips"] = build_spot_tips(
        wind_range=wind_range, wave_range=wave_range,
        wind_dir=wind_dir or "", hour=now.hour, month=month,
        coast=coast, tide_state=forecast.get("tide_state", ""),
    )

    # Conditions explainer
    forecast["conditions_explainer"] = build_conditions_explainer(
        wind_range=wind_range, wave_range=wave_range,
        wind_dir=wind_dir, water_temp=water_temp,
        pressure=forecast.get("pressure"), tide_state=forecast.get("tide_state", ""),
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
        wind_range=wind_range, wave_range=wave_range,
        hour=now.hour, water_temp=water_temp,
        weather=forecast.get("weather"),
    )

    # Safety checklist
    forecast["safety"] = build_safety_checklist(
        wind_range=wind_range, wave_range=wave_range,
        hour=now.hour, alerts=forecast.get("alerts"),
    )

    # Best fishing times (synthesize solunar + tides + sunrise/sunset)
    forecast["best_times"] = build_best_times(forecast)

    # 24-hour activity timeline
    forecast["activity_timeline"] = build_activity_timeline(forecast)

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
            sp_entry["name"], hour=now.hour,
            tide_state=t_state, wind_strength=wind_strength,
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


def personalize_forecast(
    forecast: Dict[str, Any],
    profile: Dict[str, Any],
    location: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Apply profile-based personalization to a cached forecast.

    Re-runs species ranking with profile filters and rebuilds the
    species-dependent sections (rigs, baits, bite alerts, gear checklist,
    calendar).  Conditions data, tides, weather, etc. remain unchanged.
    """
    fishing_types = profile.get("fishing_types")
    targets = profile.get("targets")
    if not fishing_types and not targets:
        return forecast

    tz_name = (location or {}).get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    month = now.month

    # Extract conditions from cached forecast for re-scoring
    conds = forecast.get("conditions", {})
    water_temp = conds.get("water_temp_f", 70)
    wind_dir = conds.get("wind_dir") or None

    # Parse wind/wave ranges from formatted strings
    def _parse_range(s: str) -> Optional[Tuple[float, float]]:
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

    conditions_region = (location or {}).get("conditions_region", "atlantic_mid")
    coast = "west" if conditions_region.startswith("pacific") else ("hawaii" if conditions_region.startswith("hawaii") else "east")
    loc_state = (location or {}).get("state", "")
    loc_fish_region = (location or {}).get("fish_region", "")

    species = build_species_ranking(
        month, water_temp,
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
            sp_entry["name"], hour=now.hour,
            tide_state=t_state, wind_strength=wind_strength,
        )

    # Rebuild species-dependent sections
    forecast = dict(forecast)  # shallow copy to avoid mutating cache
    forecast["species"] = species
    forecast["rig_recommendations"] = build_rig_recommendations(species)
    forecast["bait_rankings"] = build_bait_ranking(species, month)
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
        wind_range=wind_range, wave_range=wave_range,
        hour=now.hour, water_temp=water_temp,
        weather=forecast.get("weather"),
    )

    # Rebuild 3-day outlook with profile-filtered species
    outlook = build_multiday_outlook(
        now, location,
        fishing_types=fishing_types,
        targets=targets,
    )
    if outlook:
        forecast["outlook"] = outlook
        forecast["best_day"] = pick_best_fishing_day(
            forecast.get("conditions", {}).get("verdict", "Fair"),
            outlook,
        )

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


def build_best_times(forecast: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build a list of recommended fishing windows.

    Combines solunar major/minor periods with tide change windows and
    low-light periods (dawn/dusk) to suggest the best 2-3 windows.
    Each entry: {"window": "5:30 - 7:30 AM", "reason": "...", "quality": "Prime"}
    """
    windows: List[Dict[str, Any]] = []

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
        windows.append({
            "start_h": dawn_start,
            "end_h": dawn_end,
            "label": f"{sunrise_str} window",
            "reason": "Dawn — fish feed actively in low light",
            "score": 3,
        })

        # Dusk window: 60 min before to 30 min after sunset
        dusk_start = ss_h - 1.0
        dusk_end = ss_h + 0.5
        windows.append({
            "start_h": dusk_start,
            "end_h": dusk_end,
            "label": f"{sunset_str} window",
            "reason": "Dusk — prime low-light feeding period",
            "score": 3,
        })

    # Solunar major periods
    solunar = forecast.get("solunar", {})
    for mp in solunar.get("major_periods", []):
        s_h = _parse_time_str(mp["start"])
        e_h = _parse_time_str(mp["end"])
        windows.append({
            "start_h": s_h,
            "end_h": e_h,
            "label": f"{mp['start']} – {mp['end']}",
            "reason": "Solunar major — peak lunar activity",
            "score": 4,
        })

    for mp in solunar.get("minor_periods", []):
        s_h = _parse_time_str(mp["start"])
        e_h = _parse_time_str(mp["end"])
        windows.append({
            "start_h": s_h,
            "end_h": e_h,
            "label": f"{mp['start']} – {mp['end']}",
            "reason": "Solunar minor — elevated activity",
            "score": 2,
        })

    # Tide change windows (best fishing near high tides)
    tides = forecast.get("tides", [])
    for t in tides:
        if t.get("type") == "High":
            t_h = t.get("hour", _parse_time_str(t["time"]))
            windows.append({
                "start_h": t_h - 1.0,
                "end_h": t_h + 1.0,
                "label": f"{t['time']}",
                "reason": "Incoming high tide pushes bait into range",
                "score": 2,
            })

    if not windows:
        return []

    # Score each window: boost when multiple factors overlap
    # Check for overlaps between windows
    scored_windows: List[Dict[str, Any]] = []
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
    selected: List[Dict[str, str]] = []
    used_hours: List[Tuple[float, float]] = []
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

        selected.append({
            "window": w["label"],
            "reason": w["reason"],
            "quality": quality,
        })
        used_hours.append((w["start_h"], w["end_h"]))
        if len(selected) >= 3:
            break

    return selected


def build_activity_timeline(forecast: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build a 24-hour fish activity timeline (one value per hour).

    Each entry: {"hour": 0-23, "label": "12 AM", "level": 0-100, "tag": "low/med/high/prime"}
    Combines solunar periods, tide changes, and dawn/dusk to estimate activity.
    """
    # Start with a flat baseline
    activity = [15.0] * 24  # baseline activity

    # Dawn/dusk boost
    sun_str = forecast.get("conditions", {}).get("sunrise_sunset", "")
    if "/" in sun_str:
        parts = sun_str.split("/")
        sr_h = _parse_time_str(parts[0].strip())
        ss_h = _parse_time_str(parts[1].strip())

        # Dawn: 1h before to 1.5h after sunrise
        for h in range(24):
            dist = abs(h - sr_h)
            if dist < 1.5:
                activity[h] += 30 * max(0, 1 - dist / 1.5)
            dist = abs(h - ss_h)
            if dist < 1.5:
                activity[h] += 30 * max(0, 1 - dist / 1.5)

    # Solunar periods
    solunar = forecast.get("solunar", {})
    for mp in solunar.get("major_periods", []):
        s_h = _parse_time_str(mp["start"])
        e_h = _parse_time_str(mp["end"])
        for h in range(24):
            if s_h <= h <= e_h:
                activity[h] += 35
            elif abs(h - s_h) < 1 or abs(h - e_h) < 1:
                activity[h] += 15

    for mp in solunar.get("minor_periods", []):
        s_h = _parse_time_str(mp["start"])
        e_h = _parse_time_str(mp["end"])
        for h in range(24):
            if s_h <= h <= e_h:
                activity[h] += 20
            elif abs(h - s_h) < 1 or abs(h - e_h) < 1:
                activity[h] += 8

    # Tide change windows
    tides = forecast.get("tides", [])
    for t in tides:
        t_h = t.get("hour", _parse_time_str(t.get("time", "12:00 PM")))
        for h in range(24):
            dist = abs(h - t_h)
            if dist < 2:
                boost = 20 if t.get("type") == "High" else 12
                activity[h] += boost * max(0, 1 - dist / 2)

    # Night penalty (subtle — fish are less active 11 PM to 4 AM)
    for h in [23, 0, 1, 2, 3, 4]:
        activity[h] *= 0.7

    # Normalize to 0-100
    max_val = max(activity) if max(activity) > 0 else 1
    labels_12h = [
        "12 AM", "1 AM", "2 AM", "3 AM", "4 AM", "5 AM",
        "6 AM", "7 AM", "8 AM", "9 AM", "10 AM", "11 AM",
        "12 PM", "1 PM", "2 PM", "3 PM", "4 PM", "5 PM",
        "6 PM", "7 PM", "8 PM", "9 PM", "10 PM", "11 PM",
    ]

    timeline = []
    for h in range(24):
        level = min(100, int(activity[h] / max_val * 100))
        if level >= 75:
            tag = "prime"
        elif level >= 50:
            tag = "high"
        elif level >= 30:
            tag = "med"
        else:
            tag = "low"
        timeline.append({
            "hour": h,
            "label": labels_12h[h],
            "level": level,
            "tag": tag,
        })

    return timeline


def build_share_text(forecast: Dict[str, Any]) -> str:
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
