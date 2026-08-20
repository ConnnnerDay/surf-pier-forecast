"""NDBC (National Data Buoy Center) provider adapter (sprint 15).

Ports real-time buoy observation parsing from the legacy
`services/ndbc.py`, behind typed contracts and
`app.infra.http_client.BoundedHTTPClient` (which gained `get_text` in
this sprint — NDBC's `realtime2` feed is fixed-width text, not JSON,
unlike sprints 13/14's providers). Per docs/R1_RECONCILIATION_AUDIT.md,
this is an adapt, not a verbatim carry-over.

A buoy's `realtime2` feed is a small text table: a `#`-prefixed header
row naming columns, a units row, then up to ~45 recent observation rows
(most recent first). Not every buoy reports every column (a wave-only
buoy has no WSPD, a weather-only buoy has no WVHT), and any given cell
can hold a provider-specific "missing" marker (`MM`, `99.0`, `999`, ...)
instead of a real reading. `parse_realtime_text` handles both: an absent
column leaves that `BuoyObservation` field `None` rather than raising,
and a station with genuinely no usable reading in any of its recent rows
raises `NdbcDataUnavailableError`.

Scope for this sprint: wind speed/gust/direction, wave height, and
barometric pressure — the single-latest-reading parse that
`_try_ndbc_station` did. The legacy module's `fetch_barometric_pressure`
also computed a pressure *trend* (rising/falling/steady across several
readings) and a fishing-impact narrative from that trend; both are
scoring/narrative concerns that belong with the rest of the fishing
guidance logic (sprint 35), not a provider adapter, so they're
deliberately not ported here — see docs/CANONICAL_ROADMAP.md's sprint
ledger.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.infra.http_client import BoundedHTTPClient, ProviderError

_MS_TO_KNOTS = 1.94384
_M_TO_FEET = 3.28084

_MISSING = frozenset({"MM", "99.0", "99.00", "999", "999.0", "9999.0"})

_COMPASS_POINTS = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)


class NdbcDataUnavailableError(ProviderError):
    """The request succeeded, but the station's feed had too few rows, or
    none of its recent rows had a usable reading for any tracked field —
    distinct from a transport or HTTP-status failure, but still a
    `ProviderError` so callers can handle "this source is unavailable"
    as one concern regardless of why.
    """


class BuoyObservation(BaseModel):
    """A buoy's most recent reading for each field it reports. `None`
    means the station's feed doesn't carry that column, or none of the
    recent rows checked had a non-missing value for it — not that the
    reading is zero.
    """

    wind_speed_kt: float | None = None
    wind_gust_kt: float | None = None
    wind_direction: str | None = None
    wave_height_ft: float | None = None
    pressure_mb: float | None = None


def _deg_to_compass(deg: float) -> str:
    idx = round(deg / 22.5) % 16
    return _COMPASS_POINTS[idx]


def parse_realtime_text(text: str) -> BuoyObservation:
    """Parse an NDBC `realtime2/<station>.txt` feed into the most recent
    usable reading for each field, checking up to the 10 most recent
    observation rows (rows 2-11; row 0 is the header, row 1 is units).

    Raises `NdbcDataUnavailableError` if the feed has fewer than 3 lines
    (header + units + at least one observation), or if no row had a
    usable value for any tracked field.
    """
    lines = text.strip().split("\n")
    if len(lines) < 3:
        raise NdbcDataUnavailableError(
            "feed has too few rows to contain an observation"
        )

    header = lines[0].replace("#", "").split()
    col = {name: idx for idx, name in enumerate(header)}

    wind_speed_kt: float | None = None
    wind_gust_kt: float | None = None
    wind_direction: str | None = None
    wave_height_ft: float | None = None
    pressure_mb: float | None = None

    for line in lines[2:12]:
        fields = line.split()
        if len(fields) < len(header):
            continue

        wspd_raw = fields[col["WSPD"]] if "WSPD" in col else "MM"
        gst_raw = fields[col["GST"]] if "GST" in col else "MM"
        wdir_raw = fields[col["WDIR"]] if "WDIR" in col else "MM"
        wvht_raw = fields[col["WVHT"]] if "WVHT" in col else "MM"
        pres_raw = fields[col["PRES"]] if "PRES" in col else "MM"

        if wind_speed_kt is None and wspd_raw not in _MISSING:
            wind_speed_kt = round(float(wspd_raw) * _MS_TO_KNOTS, 1)
            wind_gust_kt = (
                round(float(gst_raw) * _MS_TO_KNOTS, 1)
                if gst_raw not in _MISSING
                else wind_speed_kt
            )

        if wind_direction is None and wdir_raw not in _MISSING:
            wind_direction = _deg_to_compass(float(wdir_raw))

        if wave_height_ft is None and wvht_raw not in _MISSING:
            wave_height_ft = round(float(wvht_raw) * _M_TO_FEET, 1)

        if pressure_mb is None and pres_raw not in _MISSING:
            pressure_mb = round(float(pres_raw), 1)

        if (
            wind_speed_kt is not None
            and wind_direction is not None
            and wave_height_ft is not None
            and pressure_mb is not None
        ):
            break

    if (
        wind_speed_kt is None
        and wind_direction is None
        and wave_height_ft is None
        and pressure_mb is None
    ):
        raise NdbcDataUnavailableError(
            "no row in the checked window had a usable reading for any field"
        )

    return BuoyObservation(
        wind_speed_kt=wind_speed_kt,
        wind_gust_kt=wind_gust_kt,
        wind_direction=wind_direction,
        wave_height_ft=wave_height_ft,
        pressure_mb=pressure_mb,
    )


async def fetch_buoy_observation(
    client: BoundedHTTPClient, station_id: str
) -> BuoyObservation:
    """Fetch and parse a buoy's real-time observation feed."""
    url = f"https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"
    text = await client.get_text(url)
    return parse_realtime_text(text)
