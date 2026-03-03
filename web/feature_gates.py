"""Feature gate configuration and helpers for free vs paid accounts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

FEATURE_GATES: Dict[str, Dict[str, Any]] = {
    "free": {
        "max_locations": 1,
        "forecast_horizon_hours": 24,
        "alerts": False,
        "extended_outlook": False,
        "saved_logs": False,
    },
    "paid": {
        "max_locations": 10,
        "forecast_horizon_hours": 96,
        "alerts": True,
        "extended_outlook": True,
        "saved_logs": True,
    },
}


@dataclass
class GateContext:
    user_id: int
    tier: str
    is_paid: bool


def tier_config(tier: str) -> Dict[str, Any]:
    return FEATURE_GATES.get(tier, FEATURE_GATES["free"])
