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


def analyze_catch_patterns(
    catches: list[dict[str, Any]],
    current: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Summarize productive patterns from a list of catch dicts.

    Each catch may carry: species, tide_state, wind_dir, water_temp_f,
    moon_phase, hab_risk, river_discharge_cfs. Returns a dict with the catch
    counts, a list of human-readable ``insights`` strings, and the structured
    ``factors`` behind them.

    When *current* (the forecast conditions right now: tide_state, wind_dir,
    moon_phase) is supplied, any dominant factor that matches today's
    conditions is reported under ``matches`` so the angler knows when the
    moment lines up with what's historically worked.
    """
    total = len(catches)
    factors: dict[str, Any] = {}
    insights: list[str] = []

    # Food-safety flag — surfaced first since it's a warning, not a "what
    # works" pattern. Counts catches logged while an EPA WQP harmful-algal-
    # bloom watch/danger reading was active at that spot.
    hab_events = sum(
        1 for c in catches if (c.get("hab_risk") or "").lower() in ("watch", "danger")
    )
    if hab_events:
        factors["hab_events"] = {"count": hab_events, "sample": total}
        insights.append(
            f"{hab_events} of your logged catches happened during an active "
            "algal bloom advisory — those fish may not have been safe to eat."
        )

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
        factors["water_temp_f"] = {
            "low": round(lo),
            "high": round(hi),
            "sample": len(temps),
        }
        if round(hi) > round(lo):
            insights.append(
                f"Most of your catches landed between {round(lo)}-{round(hi)}°F water."
            )
        else:
            insights.append(f"Your catches cluster around {round(lo)}°F water.")

    # River discharge sweet spot — only meaningful for catches near an inlet
    # or river mouth where a USGS gauge reported a reading at catch time.
    flows = sorted(
        c["river_discharge_cfs"]
        for c in catches
        if isinstance(c.get("river_discharge_cfs"), (int, float))
    )
    if len(flows) >= _MIN_SAMPLES:
        lo = flows[len(flows) // 4]
        hi = flows[(len(flows) * 3) // 4]
        factors["river_discharge_cfs"] = {
            "low": round(lo),
            "high": round(hi),
            "sample": len(flows),
        }
        if round(hi) > round(lo):
            insights.append(
                f"Most of your catches came with nearby river flow between "
                f"{round(lo)}-{round(hi)} cfs."
            )
        else:
            insights.append(
                f"Your catches cluster around {round(lo)} cfs of nearby river flow."
            )

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

    # Most effective bait (only when enough catches recorded one).
    baits = Counter(
        (c.get("bait") or "").strip() for c in catches if (c.get("bait") or "").strip()
    )
    bait_top = baits.most_common(3)
    if bait_top and sum(baits.values()) >= _MIN_SAMPLES:
        factors["top_bait"] = [
            {"bait": name, "count": count} for name, count in bait_top
        ]
        lead_bait, lead_bait_count = bait_top[0]
        if lead_bait_count >= 2:
            insights.append(
                f"{lead_bait} is your most productive bait ({lead_bait_count} catches)."
            )

    # Most effective rig.
    rigs = Counter(
        (c.get("rig") or "").strip() for c in catches if (c.get("rig") or "").strip()
    )
    rig_top = rigs.most_common(3)
    if rig_top and sum(rigs.values()) >= _MIN_SAMPLES:
        factors["top_rig"] = [{"rig": name, "count": count} for name, count in rig_top]
        lead_rig, lead_rig_count = rig_top[0]
        if lead_rig_count >= 2:
            insights.append(
                f"The {lead_rig} is your most productive rig ({lead_rig_count} catches)."
            )

    # Does today's forecast line up with a historically productive factor?
    matches: list[str] = []
    if current:
        _match_labels = {
            "tide_state": lambda v: (
                f"Today's {v.lower()} tide matches your most productive pattern."
            ),
            "wind_dir": lambda v: (
                f"Today's {v} wind matches your most productive pattern."
            ),
            "moon_phase": lambda v: (
                f"Tonight's {v.lower()} moon matches your most productive pattern."
            ),
        }
        for key, label in _match_labels.items():
            fac = factors.get(key)
            cur = (current.get(key) or "").strip()
            if fac and cur and cur.lower() == str(fac["value"]).lower():
                matches.append(label(cur))
        # Surface matches at the top of the insight list.
        insights = matches + insights

    return {
        "total": total,
        "with_conditions": sum(
            1
            for c in catches
            if c.get("tide_state") or c.get("wind_dir") or c.get("moon_phase")
        ),
        "insights": insights,
        "matches": matches,
        "factors": factors,
    }
