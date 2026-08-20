"""Shared timezone-resolution helper (sprint 16).

Extracted here because two provider adapters need the same "resolve a
timezone name, falling back to a default if it's invalid" behavior:
sprint 14's `app.providers.noaa_coops` (CO-OPS timestamps are in
station-local time) and sprint 16's `app.providers.astronomy` (solar/
lunar calculations are inherently location-and-timezone dependent). A
third copy would be one too many to keep hand-duplicating.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/New_York"


def safe_zone(tz_name: str) -> ZoneInfo:
    """Return `ZoneInfo(tz_name)`, falling back to `DEFAULT_TIMEZONE` if
    *tz_name* isn't a valid IANA zone name.
    """
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)
