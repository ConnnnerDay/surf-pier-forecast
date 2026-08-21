"""NOAA CO-OPS (Center for Operational Oceanographic Products and
Services) provider adapter (sprint 14; wind fallback added picking up
sprint 14's own deferred scope once Phase 2 closed out).

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

Original scope: water temperature and tide predictions.

**Wind fallback, picked up from sprint 14's own deferred scope.** Ports
the legacy module's `_try_coops_wind`: the latest wind reading from the
same CO-OPS station used for water temperature — if that succeeds, this
is very likely to as well. `app.domain.assembly` wires it in at the
exact priority the legacy `domain/forecast.py:get_marine_conditions`
used (marine-zone forecast, then NDBC buoy, then this, then NWS
gridpoint wind last), tried only when neither of the first two provide
wind — same "bounded parallel calls, no duplicates" performance-budget
discipline as the gridpoint fallback. It's a fallback, not a
decision-relevant primary source, so — unlike `fetch_water_temperature`/
`fetch_tide_predictions` — it degrades to `None` on failure rather than
raising, matching `app.providers.nws.fetch_gridpoint_wind`'s posture.

The legacy module's currents (`fetch_currents_predictions`/
`fetch_currents_observation`), environmental metrics
(`fetch_coops_environmental_metrics` — air temp, humidity, visibility,
pressure, salinity, conductivity), and `build_tide_chart_svg` rendering
helper remain deliberately deferred: nothing in the canonical roadmap's
required `ForecastConditions` shape or `docs/product-definition.md`'s
dashboard-hierarchy list names tidal currents or these environmental
metrics, so porting them now would be inventing product scope, not
closing a named gap; SVG rendering isn't a provider-adapter concern at
all.

CO-OPS timestamps are returned in the station's local standard/daylight
time (`time_zone=lst_ldt`), not UTC. Parsing uses `zoneinfo.ZoneInfo`,
which (unlike `pytz`) resolves the correct UTC offset for a given
wall-clock instant via a plain `.replace(tzinfo=...)`, so a station's
timestamps are interpreted with the right offset on both sides of a DST
transition without a separate `.localize()` step. The
`ZoneInfo`-with-fallback helper itself now lives in
`app.infra.timezones` (extracted in sprint 16, once
`app.providers.astronomy` needed the identical helper — a second copy
was fine, a third wasn't).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.infra.http_client import BoundedHTTPClient, ProviderError
from app.infra.timezones import DEFAULT_TIMEZONE
from app.infra.timezones import safe_zone as _safe_zone

logger = logging.getLogger(__name__)

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


class CoopsWindReading(BaseModel):
    """The latest wind reading from a CO-OPS station. `None` fields mean
    the station didn't report that quantity, not that it was zero.
    """

    wind_low_kt: float | None = None
    wind_high_kt: float | None = None
    wind_direction: str | None = None


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


async def fetch_coops_wind(
    client: BoundedHTTPClient, station_id: str
) -> CoopsWindReading | None:
    """Fetch the latest wind reading from a CO-OPS station. A last-resort
    wind fallback (see `app.domain.assembly`'s docstring for the
    reconciliation order among this, NWS gridpoint wind, marine-zone,
    and buoy) — degrades to `None` on any failure rather than raising,
    unlike `fetch_water_temperature`/`fetch_tide_predictions`.
    """
    url = (
        f"{_DATAGETTER_URL}?date=latest&station={station_id}"
        "&product=wind&units=english"
        "&time_zone=lst_ldt&format=json"
    )
    try:
        data = cast(
            "dict[str, Any]", await client.get_json(url, headers=_COOPS_HEADERS)
        )
    except ProviderError:
        logger.warning(
            "CO-OPS wind unavailable for station %r", station_id, exc_info=True
        )
        return None

    rows = data.get("data") or []
    if not rows:
        return None
    speed = rows[0].get("s")
    if speed in (None, ""):
        return None
    speed_f = float(speed)
    gust_raw = rows[0].get("g")
    gust_f = float(gust_raw) if gust_raw not in (None, "", "0.00") else speed_f
    return CoopsWindReading(
        wind_low_kt=round(speed_f, 1),
        wind_high_kt=round(max(speed_f, gust_f), 1),
        wind_direction=rows[0].get("d") or None,
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
