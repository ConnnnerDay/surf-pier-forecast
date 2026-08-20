"""NWS (National Weather Service) provider adapter (sprint 13).

Ports the marine-zone conditions parsing and active-alerts handling from
the legacy `services/nws.py`, behind typed contracts and
`app.infra.http_client.BoundedHTTPClient` instead of the legacy
`requests`-based `http_client.get`. Per docs/R1_RECONCILIATION_AUDIT.md,
this is an adapt, not a verbatim carry-over: the two near-duplicate
GeoJSON/JSON-LD alert parsers in the legacy module (`fetch_weather_alerts`
and `fetch_state_alerts`) are unified into one `_parse_alerts` here.

Scope for this sprint: marine-zone wind/wave/direction parsing and fetch,
plus point and state active-alerts parsing and fetch. The legacy module's
gridpoint-forecast wind fallback (`_try_nws_gridpoint`) and current-weather
observations (`fetch_current_weather`, including its heat-index and
recent-precipitation logic) are deliberately deferred to a follow-up
sprint to keep this PR reviewable — tracked in
docs/CANONICAL_ROADMAP.md's live checkpoint, needed before sprint 21
(forecast assembly).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel

from app.infra.http_client import BoundedHTTPClient, ProviderError

logger = logging.getLogger(__name__)

NWS_MARINE_ZONE_DEFAULT = "AMZ158"

_NWS_HEADERS = {
    "User-Agent": "(SurfPierForecast, github.com/ConnnnerDay/surf-pier-forecast)",
    "Accept": "application/ld+json",
}

_DIR_MAP: dict[str, str] = {
    "north": "N",
    "northeast": "NE",
    "northwest": "NW",
    "south": "S",
    "southeast": "SE",
    "southwest": "SW",
    "east": "E",
    "west": "W",
    "variable": "VARIABLE",
}

_WIND_DIR_RE = re.compile(
    r"(north(?:east|west)?|south(?:east|west)?|east|west|"
    r"NE|NW|SE|SW|N|E|S|W|VARIABLE)\s+wind",
    re.IGNORECASE,
)
_WIND_SPEED_RE = re.compile(r"(\d+)(?:\s*to\s*(\d+))?\s*(?:kt|knots?)", re.IGNORECASE)
_SEA_HEIGHT_RE = re.compile(
    r"(?:seas?|waves?)\s*(?:around\s+)?(\d+)(?:\s*to\s*(\d+))?\s*(?:ft|feet|foot)",
    re.IGNORECASE,
)


class MarineZoneConditions(BaseModel):
    """Wind and wave ranges parsed from an NWS marine zone forecast's first
    ~24 hours (3 periods). `None` fields mean the source text didn't
    mention that quantity in the periods examined, not that it was zero.
    """

    wind_low_kt: float | None = None
    wind_high_kt: float | None = None
    wind_direction: str | None = None
    wave_low_ft: float | None = None
    wave_high_ft: float | None = None


class WeatherAlert(BaseModel):
    event: str
    severity: str
    headline: str
    description: str


def parse_marine_zone_conditions(periods: list[dict[str, Any]]) -> MarineZoneConditions:
    """Extract wind and wave ranges from NWS marine forecast periods.

    Examines the first 3 periods (~24 hours) and regex-parses wind speed
    (kt), wind direction, and sea height (ft) from each period's
    `detailedForecast` text. Ported verbatim in behavior from the legacy
    `services/nws.py:parse_conditions`.
    """
    wind_ranges: list[tuple[float, float]] = []
    wave_ranges: list[tuple[float, float]] = []
    wind_directions: list[str] = []

    for period in periods[:3]:
        text = period.get("detailedForecast", "")

        dir_match = _WIND_DIR_RE.search(text)
        if dir_match:
            raw = dir_match.group(1)
            wind_directions.append(_DIR_MAP.get(raw.lower(), raw.upper()))

        wind_match = _WIND_SPEED_RE.search(text)
        if wind_match:
            low = float(wind_match.group(1))
            high = float(wind_match.group(2)) if wind_match.group(2) else low
            wind_ranges.append((low, high))

        sea_match = _SEA_HEIGHT_RE.search(text)
        if sea_match:
            low = float(sea_match.group(1))
            high = float(sea_match.group(2)) if sea_match.group(2) else low
            wave_ranges.append((low, high))

    conditions = MarineZoneConditions(
        wind_direction=wind_directions[0] if wind_directions else None,
    )
    if wind_ranges:
        conditions.wind_low_kt = min(w[0] for w in wind_ranges)
        conditions.wind_high_kt = max(w[1] for w in wind_ranges)
    if wave_ranges:
        conditions.wave_low_ft = min(s[0] for s in wave_ranges)
        conditions.wave_high_ft = max(s[1] for s in wave_ranges)
    return conditions


def _parse_alerts(payload: dict[str, Any], limit: int) -> list[WeatherAlert]:
    """Parse NWS active-alerts JSON, handling both response shapes the API
    can return depending on the `Accept` header: GeoJSON (`features`, with
    alert fields nested under `properties`) and JSON-LD (`@graph`, with
    alert fields at the top level). Descriptions are truncated to 300
    characters, matching the legacy behavior.
    """
    raw = payload.get("features", []) or payload.get("@graph", [])
    alerts: list[WeatherAlert] = []
    for item in raw[:limit]:
        fields = item.get("properties", item)
        event = fields.get("event", "")
        if not event:
            continue
        alerts.append(
            WeatherAlert(
                event=event,
                severity=fields.get("severity", ""),
                headline=fields.get("headline", ""),
                description=(fields.get("description", "") or "")[:300],
            )
        )
    return alerts


async def fetch_marine_zone_conditions(
    client: BoundedHTTPClient, zone: str = NWS_MARINE_ZONE_DEFAULT
) -> MarineZoneConditions:
    """Fetch and parse the NWS marine zone forecast. Raises a
    `ProviderError` subclass on failure — marine conditions are
    decision-relevant, so callers must see the failure rather than get a
    silently empty result (unlike alerts, see `fetch_point_alerts`).
    """
    url = f"https://api.weather.gov/zones/forecast/{zone}/forecast"
    data = await client.get_json(url, headers=_NWS_HEADERS)
    periods = data["properties"]["periods"]  # type: ignore[index]
    return parse_marine_zone_conditions(periods)


async def fetch_point_alerts(
    client: BoundedHTTPClient, lat: float, lng: float, limit: int = 5
) -> list[WeatherAlert]:
    """Fetch active weather alerts for a lat/lng. Alerts are non-critical
    enrichment, so failures are logged and swallowed to an empty list —
    matching the legacy `fetch_weather_alerts`'s resilience choice —
    rather than failing the whole forecast.
    """
    url = f"https://api.weather.gov/alerts/active?point={lat},{lng}"
    try:
        data = await client.get_json(url, headers=_NWS_HEADERS)
    except ProviderError:
        logger.warning("NWS point alerts unavailable", exc_info=True)
        return []
    return _parse_alerts(data, limit)  # type: ignore[arg-type]


async def fetch_state_alerts(
    client: BoundedHTTPClient, state_code: str, limit: int = 10
) -> list[WeatherAlert]:
    """Fetch active alerts for an entire state. Same non-critical
    resilience posture as `fetch_point_alerts`.
    """
    if not state_code:
        return []
    url = f"https://api.weather.gov/alerts/active?area={state_code.upper()}"
    try:
        data = await client.get_json(url, headers=_NWS_HEADERS)
    except ProviderError:
        logger.warning("NWS state alerts unavailable", exc_info=True)
        return []
    return _parse_alerts(data, limit)  # type: ignore[arg-type]
