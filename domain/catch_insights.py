"""Catch-log pattern analysis.

Turns a user's logged catches (each carrying a snapshot of the conditions at
catch time) into plain-language insights — e.g. "7 of 10 catches came on a
rising tide". Pure functions: feed it a list of catch dicts, get back a
summary. No database or network here.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

# Need at least this many catches carrying a given signal before we'll claim a
# pattern — below this the "pattern" is just noise.
_MIN_SAMPLES = 4
# Only surface a factor when the leading value accounts for at least this share.
_DOMINANCE = 0.5


def _dominant(values: list[str]) -> Optional[tuple[str, int, int]]:
    """Return (value, count, total) for the most common non-empty value.

    ``None`` when there aren't enough samples or nothing dominates.
    """
    cleaned = [v for v in values if v]
    if len(cleaned) < _MIN_SAMPLES:
        return None
    value, count = Counter(cleaned).most_common(1)[0]
    if count / len(cleaned) <= _DOMINANCE:
        return None
    return value, count, len(cleaned)


def analyze_catch_patterns(catches: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize productive patterns from a list of catch dicts.

    Each catch may carry: species, tide_state, wind_dir, water_temp_f,
    moon_phase. Returns a dict with the catch counts, a list of human-readable
    ``insights`` strings, and the structured ``factors`` behind them.
    """
    total = len(catches)
    factors: dict[str, Any] = {}
    insights: list[str] = []

    tide = _dominant([c.get("tide_state") or "" for c in catches])
    if tide:
        value, count, n = tide
        factors["tide_state"] = {"value": value, "count": count, "sample": n}
        insights.append(
            f"{count} of your last {n} catches came on a {value.lower()} tide."
        )

    wind = _dominant([c.get("wind_dir") or "" for c in catches])
    if wind:
        value, count, n = wind
        factors["wind_dir"] = {"value": value, "count": count, "sample": n}
        insights.append(f"{value} winds produced {count} of {n} logged catches.")

    moon = _dominant([c.get("moon_phase") or "" for c in catches])
    if moon:
        value, count, n = moon
        factors["moon_phase"] = {"value": value, "count": count, "sample": n}
        insights.append(f"A {value.lower()} moon accounted for {count} of {n} catches.")

    # Water-temperature sweet spot (the 25th–75th percentile band).
    temps = sorted(
        c["water_temp_f"]
        for c in catches
        if isinstance(c.get("water_temp_f"), (int, float))
    )
    if len(temps) >= _MIN_SAMPLES:
        lo = temps[len(temps) // 4]
        hi = temps[(len(temps) * 3) // 4]
        factors["water_temp_f"] = {"low": round(lo), "high": round(hi), "sample": len(temps)}
        if round(hi) > round(lo):
            insights.append(
                f"Most of your catches landed between {round(lo)}-{round(hi)}°F water."
            )
        else:
            insights.append(f"Your catches cluster around {round(lo)}°F water.")

    # Top species (always useful, even without condition snapshots).
    species = Counter(c.get("species", "").strip() for c in catches if c.get("species"))
    top = species.most_common(3)
    if top:
        factors["top_species"] = [
            {"species": name, "count": count} for name, count in top
        ]
        lead_name, lead_count = top[0]
        if lead_count >= 2:
            insights.append(
                f"{lead_name} is your most-logged catch ({lead_count} times)."
            )

    return {
        "total": total,
        "with_conditions": sum(
            1
            for c in catches
            if c.get("tide_state") or c.get("wind_dir") or c.get("moon_phase")
        ),
        "insights": insights,
        "factors": factors,
    }
