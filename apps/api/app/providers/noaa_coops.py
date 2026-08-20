"""NOAA CO-OPS (Center for Operational Oceanographic Products and
Services) provider adapter (sprint 14).

Ports water-temperature and tide-prediction fetching from the legacy
`services/noaa.py`, behind typed contracts and
`app.infra.http_client.BoundedHTTPClient`. Per
docs/R1_RECONCILIATION_AUDIT.md, this is an adapt, not a verbatim
carry-over.

Both water temperature and tide predictions are decision-relevant (unlike
sprint 13's NWS active alerts), so fetch failures — including a station
reporting no reading for the requested product, a valid response with an
empty data row — propagate as a `ProviderError` subclass rather than
being silently swallowed. The legacy module's silent-failure /
monthly-average-fallback behavior (`get_water_temp`) is deliberately not
ported here: deciding *when* to fall back to a historical average, and
recording that decision on an `Observation.is_fallback`
(`app.domain.models.Observation`), is forecast-assembly's job (sprint 21),
which has the full picture (which sources succeeded, what the location's
own data offers) that a single provider adapter doesn't.

Scope for this sprint: water temperature and tide predictions. The legacy
module's wind/currents/environmental-metrics fetches and its
`build_tide_chart_svg` rendering helper (not a provider concern at all)
are deliberately deferred — see docs/CANONICAL_ROADMAP.md's sprint
ledger.

CO-OPS timestamps are returned in the station's local standard/daylight
time (`time_zone=lst_ldt`), not UTC. Parsing uses `zoneinfo.ZoneInfo`,
which (unlike `pytz`) resolves the correct UTC offset for a given
wall-clock instant via a plain `.replace(tzinfo=...)`, so a station's
timestamps are interpreted with the right offset on both sides of a DST
transition without a separate `.localize()` step.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from app.infra.http_client import BoundedHTTPClient, ProviderError

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "America/New_York"

_COOPS_HEADERS = {
    "User-Agent": "(SurfPierForecast, github.com/ConnnnerDay/surf-pier-forecast)",
    "Accept": "application/json",
}

_DATAGETTER_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"


class NoaaDataUnavailableError(ProviderError):
    """The request succeeded, but the station reported no usable data for
    the requested product (e.g. a sensor outage) — distinct from a
    transport or HTTP-status failure, but still a `ProviderError` so
    callers can handle "this source is unavailable" as one concern
    regardless of why.
    """


class WaterTemperatureReading(BaseModel):
    value_f: float
    observed_at: datetime


class TidePrediction(BaseModel):
    time: datetime
    kind: Literal["high", "low"]
    height_ft: float


def _safe_zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def _parse_coops_time(raw: str, tz: ZoneInfo) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=tz)


async def fetch_water_temperature(
    client: BoundedHTTPClient, station_id: str, tz_name: str = DEFAULT_TIMEZONE
) -> WaterTemperatureReading:
    """Fetch the latest water temperature reading for a CO-OPS station.

    Raises `NoaaDataUnavailableError` if the station has no current
    reading for this product.
    """
    url = (
        f"{_DATAGETTER_URL}?date=latest&station={station_id}"
        "&product=water_temperature&units=english"
        "&time_zone=lst_ldt&format=json"
    )
    data = cast("dict[str, Any]", await client.get_json(url, headers=_COOPS_HEADERS))
    rows = data.get("data") or []
    raw_time = rows[0].get("t") if rows else None
    raw_value = rows[0].get("v") if rows else None
    if not raw_time or raw_value in (None, ""):
        raise NoaaDataUnavailableError(
            f"station {station_id} has no water temperature reading"
        )
    assert raw_value is not None
    tz = _safe_zone(tz_name)
    return WaterTemperatureReading(
        value_f=float(raw_value), observed_at=_parse_coops_time(raw_time, tz)
    )


async def fetch_tide_predictions(
    client: BoundedHTTPClient,
    station_id: str,
    begin_date: str,
    end_date: str,
    tz_name: str = DEFAULT_TIMEZONE,
) -> list[TidePrediction]:
    """Fetch high/low tide predictions for a CO-OPS station between
    *begin_date* and *end_date* (each `YYYYMMDD`).

    Rows missing a timestamp or height are skipped rather than
    substituted with a placeholder — unlike the legacy module, which
    filled in a fake noon-hour default to avoid crashing a
    dict-based pipeline. A typed `TidePrediction.time` shouldn't lie.
    Raises `NoaaDataUnavailableError` if the station has no tide
    predictions for the range, or if every row it returned was
    unusable.
    """
    tz = _safe_zone(tz_name)
    url = (
        f"{_DATAGETTER_URL}?begin_date={begin_date}&end_date={end_date}"
        f"&station={station_id}"
        "&product=predictions&datum=MLLW&units=english"
        "&time_zone=lst_ldt&format=json&interval=hilo"
    )
    data = cast("dict[str, Any]", await client.get_json(url, headers=_COOPS_HEADERS))
    predictions = data.get("predictions") or []
    if not predictions:
        raise NoaaDataUnavailableError(
            f"station {station_id} has no tide predictions for {begin_date}-{end_date}"
        )

    out: list[TidePrediction] = []
    for row in predictions:
        raw_time = row.get("t")
        raw_height = row.get("v")
        if not raw_time or raw_height in (None, ""):
            logger.debug("Skipping tide prediction row missing time or height: %r", row)
            continue
        out.append(
            TidePrediction(
                time=_parse_coops_time(raw_time, tz),
                kind="high" if row.get("type") == "H" else "low",
                height_ft=float(raw_height),
            )
        )

    if not out:
        raise NoaaDataUnavailableError(
            f"station {station_id} returned tide predictions with no usable rows"
        )
    return out
