"""Shared utility functions used across multiple modules.

Centralises small helpers that were previously duplicated in services/,
domain/, and storage/ to avoid drift between independent copies.
"""

from __future__ import annotations

import logging
from typing import Optional

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_DEFAULT_TZ = "America/New_York"


def safe_zone(tz_name: str) -> ZoneInfo:
    """Return ZoneInfo for *tz_name*, falling back to Eastern if it is invalid.

    Replaces three independent copies of ``_safe_zone`` that previously lived
    in ``services/astro.py``, ``services/noaa.py``, and ``domain/forecast.py``.
    """
    try:
        return ZoneInfo(tz_name)
    except (KeyError, Exception):
        if tz_name != _DEFAULT_TZ:
            logger.warning(
                "Invalid timezone %r; falling back to %s", tz_name, _DEFAULT_TZ
            )
        return ZoneInfo(_DEFAULT_TZ)


def norm_user_id(user_id: Optional[int]) -> int:
    """Normalise an optional user_id to a plain int (0 for anonymous users).

    Replaces two identical copies of ``_norm_user_id`` in ``storage/cache.py``
    and ``services/forecast_refresh.py``.
    """
    return int(user_id or 0)
