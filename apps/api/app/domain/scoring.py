"""Forecast scoring (sprint 22).

Ports the core of the legacy `domain/forecast.py:score_conditions` — the
0-100 go/no-go index with an ordered, plain-language explanation — and
`domain/species.py`'s wind-orientation/onshore-offshore-direction
helpers it depends on. Per docs/R1_RECONCILIATION_AUDIT.md, this is an
adapt, not a verbatim carry-over.

This module is the reconciliation point sprint 21 (forecast assembly)
explicitly deferred: it takes a single already-reconciled `wind_range`/
`wave_range`, not sprint 21's `ForecastConditions` with its two
still-separate NWS/NDBC readings. Callers (a future wiring step, not
this sprint's job — see docs/CANONICAL_ROADMAP.md's sprint ledger)
decide how to pick one source over the other; this module only knows
how to score a range once it has one. Scoring is therefore independent
of `app.domain.assembly`, matching the pattern of every sprint in this
recovery owning one self-contained, independently testable unit.

Ported components: wind speed, wave height, wind direction (onshore/
offshore by coastline orientation — `wind_orientation_for_region`/
`onshore_offshore_dirs` are legacy's "single authoritative" mapping,
carried over verbatim per its own docstring's warning not to
reimplement it elsewhere), water temperature comfort band (with a
small bonus for a live, non-fallback reading), dawn/dusk light-window
bonus, and solunar rating/illumination bonus. All thresholds, point
values, and verdict tiers are unchanged from the legacy function.

Deliberately not ported, each requiring an input this recovery hasn't
built yet:

- Tide state/range/turn-proximity bonuses — need tide predictions,
  which sprint 21 explicitly didn't fetch (sprint 34's job).
- Fishing-type-specific modifiers (kayak/fly/wade/jetty/bridge/
  charter/offshore) and angler comfort thresholds
  (`max_wind_kt`/`max_wave_ft`) — both need user preferences data,
  which doesn't exist yet (sprint 36, "Preferences").
- Water-quality (harmful-algal-bloom / dissolved-oxygen) signals from
  `services/datagov.py` — R1_RECONCILIATION_AUDIT.md flagged whether
  that service is future enrichment or scope creep as a still-open
  product question; porting it here without that answer would be
  exactly the kind of unsupported invention this recovery exists to
  stop doing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel

from app.domain.models import Observation
from app.providers.astronomy import SolunarTimes, SunTimes

_SCORE_BASELINE = 50.0

_WIND_LIGHT_KT = 8
_WIND_MODERATE_LOW_KT = 12
_WIND_MODERATE_HIGH_KT = 16
_WIND_STRONG_KT = 20
_WIND_VERY_STRONG_KT = 25

_WAVE_FLAT_FT = 1.5
_WAVE_MODERATE_FT = 3.0
_WAVE_ROUGH_FT = 5.0
_WAVE_HEAVY_FT = 7.0

_VERDICT_EXCELLENT = 80
_VERDICT_GOOD = 64
_VERDICT_FAIR = 48
_VERDICT_CHALLENGING = 32

_DAWN_DUSK_WINDOW = timedelta(hours=2)

_ONSHORE_DIRS_EAST = {"S", "SE", "E", "SSE", "ESE", "SSW", "ENE", "NE"}
_OFFSHORE_DIRS_EAST = {"N", "NW", "W", "NNW", "WNW", "NNE"}
_ONSHORE_DIRS_WEST = {"W", "NW", "SW", "WNW", "WSW", "NNW", "SSW"}
_OFFSHORE_DIRS_WEST = {"E", "NE", "SE", "ENE", "ESE", "NNE", "SSE"}
_ONSHORE_DIRS_GULF = {"S", "SSW", "SW", "SSE", "SE", "WSW", "ESE"}
_OFFSHORE_DIRS_GULF = {"N", "NNE", "NE", "NNW", "NW", "ENE", "WNW"}

# Hawaii (and any island/omnidirectional coast): onshore/offshore is
# ambiguous because the shore faces every direction, so no wind-direction
# bonus or penalty applies rather than guessing.
_DIRS_BY_ORIENTATION: dict[str, tuple[set[str], set[str]]] = {
    "east": (_ONSHORE_DIRS_EAST, _OFFSHORE_DIRS_EAST),
    "west": (_ONSHORE_DIRS_WEST, _OFFSHORE_DIRS_WEST),
    "gulf": (_ONSHORE_DIRS_GULF, _OFFSHORE_DIRS_GULF),
    "hawaii": (set(), set()),
}

_SOLUNAR_RATING_BONUS = {"Excellent": 8, "Good": 4, "Fair": 0, "Poor": -6}


class ScoreVerdict(str, Enum):
    EXCELLENT = "Excellent"
    GOOD = "Good"
    FAIR = "Fair"
    CHALLENGING = "Challenging"
    POOR = "Poor"
    UNKNOWN = "Unknown"


class ScoreFactor(BaseModel):
    impact: float
    description: str


class ForecastScore(BaseModel):
    """`score`/`verdict` are `None`/`UNKNOWN` when neither wind nor wave
    data is available — there's nothing defensible to score.
    """

    score: int | None
    verdict: ScoreVerdict
    factors: list[ScoreFactor]
    summary: str


def wind_orientation_for_region(conditions_region: str) -> str:
    """The coastline orientation used for wind-direction scoring, from a
    location's `conditions_region` (sprint 19's `CuratedLocation`/
    `ResolvedLocation` field). Ported from the legacy
    `domain/forecast.py:_wind_orientation` — kept distinct from that
    module's separate `_derive_coast` (which folds the Gulf into
    `"east"` for species selection): a south-facing Gulf shore has
    different onshore/offshore winds than an east-facing Atlantic one,
    so the Gulf stays its own orientation here.
    """
    cr = conditions_region or ""
    if cr.startswith("pacific"):
        return "west"
    if cr.startswith("gulf"):
        return "gulf"
    if cr.startswith("hawaii"):
        return "hawaii"
    return "east"


def onshore_offshore_dirs(orientation: str) -> tuple[set[str], set[str]]:
    """Return `(onshore_dirs, offshore_dirs)` for a coastline
    orientation. Unknown values fall back to the east-facing (Atlantic)
    sets.
    """
    return _DIRS_BY_ORIENTATION.get(orientation, _DIRS_BY_ORIENTATION["east"])


def score_conditions(
    wind_range: tuple[float, float] | None,
    wave_range: tuple[float, float] | None,
    *,
    wind_direction: str | None = None,
    water_temperature: Observation | None = None,
    sun_times: SunTimes | None = None,
    now: datetime | None = None,
    solunar: SolunarTimes | None = None,
    coast: str = "east",
) -> ForecastScore:
    """Score fishability from the marine and astronomical signals given.

    Returns `ForecastScore(score=None, verdict=UNKNOWN, ...)` if neither
    *wind_range* nor *wave_range* is available — the legacy function's
    behavior for "conditions unknown."
    """
    if wind_range is None or wave_range is None:
        return ForecastScore(
            score=None, verdict=ScoreVerdict.UNKNOWN, factors=[], summary=""
        )

    score = _SCORE_BASELINE
    wind_max = wind_range[1]
    wave_max = wave_range[1]
    factors: list[ScoreFactor] = []

    wind_lbl = f"{int(wind_range[0])}-{int(wind_range[1])} kt"
    wave_lbl = f"{int(wave_range[0])}-{int(wave_range[1])} ft"

    if wind_max <= _WIND_LIGHT_KT:
        score += 14
        factors.append(ScoreFactor(impact=14, description=f"Light wind ({wind_lbl})"))
    elif wind_max <= _WIND_MODERATE_LOW_KT:
        score += 8
        factors.append(ScoreFactor(impact=8, description=f"Moderate wind ({wind_lbl})"))
    elif wind_max <= _WIND_MODERATE_HIGH_KT:
        score += 2
        factors.append(ScoreFactor(impact=2, description=f"Breezy ({wind_lbl})"))
    elif wind_max <= _WIND_STRONG_KT:
        score -= 8
        factors.append(ScoreFactor(impact=-8, description=f"Strong wind ({wind_lbl})"))
    elif wind_max <= _WIND_VERY_STRONG_KT:
        score -= 16
        factors.append(
            ScoreFactor(impact=-16, description=f"Very strong wind ({wind_lbl})")
        )
    else:
        score -= 26
        factors.append(
            ScoreFactor(impact=-26, description=f"Gale-force wind ({wind_lbl})")
        )

    if wave_max <= _WAVE_FLAT_FT:
        score += 10
        factors.append(ScoreFactor(impact=10, description=f"Flat surf ({wave_lbl})"))
    elif wave_max <= _WAVE_MODERATE_FT:
        score += 6
        factors.append(ScoreFactor(impact=6, description=f"Light surf ({wave_lbl})"))
    elif wave_max <= _WAVE_ROUGH_FT:
        score -= 4
        factors.append(
            ScoreFactor(impact=-4, description=f"Moderate surf ({wave_lbl})")
        )
    elif wave_max <= _WAVE_HEAVY_FT:
        score -= 12
        factors.append(ScoreFactor(impact=-12, description=f"Rough surf ({wave_lbl})"))
    else:
        score -= 22
        factors.append(ScoreFactor(impact=-22, description=f"Heavy surf ({wave_lbl})"))

    if wind_direction:
        onshore_dirs, offshore_dirs = onshore_offshore_dirs(coast)
        if wind_direction in offshore_dirs:
            score += 4
            factors.append(
                ScoreFactor(
                    impact=4, description=f"Clean offshore wind ({wind_direction})"
                )
            )
        elif wind_direction in onshore_dirs:
            score -= 4
            factors.append(
                ScoreFactor(
                    impact=-4,
                    description=f"Onshore wind ({wind_direction}) — murkier water",
                )
            )

    if water_temperature is not None:
        temp_f = water_temperature.value
        if 58 <= temp_f <= 78:
            score += 6
            factors.append(
                ScoreFactor(impact=6, description=f"Ideal water temp ({temp_f:.0f}°F)")
            )
        elif 50 <= temp_f < 58 or 78 < temp_f <= 84:
            score += 2
            factors.append(
                ScoreFactor(impact=2, description=f"Fair water temp ({temp_f:.0f}°F)")
            )
        else:
            score -= 4
            factors.append(
                ScoreFactor(
                    impact=-4, description=f"Off-peak water temp ({temp_f:.0f}°F)"
                )
            )
        if not water_temperature.is_fallback:
            score += 1

    if now and sun_times:
        if abs(now - sun_times.sunrise) <= _DAWN_DUSK_WINDOW:
            score += 4
            factors.append(ScoreFactor(impact=4, description="Dawn bite window"))
        if abs(sun_times.sunset - now) <= _DAWN_DUSK_WINDOW:
            score += 4
            factors.append(ScoreFactor(impact=4, description="Dusk bite window"))

    if solunar:
        score += _SOLUNAR_RATING_BONUS.get(solunar.rating.value, 0)
        if solunar.rating.value == "Excellent":
            factors.append(
                ScoreFactor(impact=8, description="Major feeding window (solunar)")
            )
        elif solunar.rating.value == "Good":
            factors.append(ScoreFactor(impact=4, description="Good solunar period"))
        elif solunar.rating.value == "Poor":
            factors.append(ScoreFactor(impact=-6, description="Weak solunar period"))

        illum = solunar.illumination_pct
        if 40 <= illum <= 85:
            score += 2
        elif illum < 10 or illum > 95:
            score += 1

    score = max(0.0, min(100.0, score))
    if score >= _VERDICT_EXCELLENT:
        verdict = ScoreVerdict.EXCELLENT
    elif score >= _VERDICT_GOOD:
        verdict = ScoreVerdict.GOOD
    elif score >= _VERDICT_FAIR:
        verdict = ScoreVerdict.FAIR
    elif score >= _VERDICT_CHALLENGING:
        verdict = ScoreVerdict.CHALLENGING
    else:
        verdict = ScoreVerdict.POOR

    factors.sort(key=lambda f: -abs(f.impact))
    summary = ", ".join(f.description for f in factors[:4])

    return ForecastScore(
        score=round(score), verdict=verdict, factors=factors, summary=summary
    )
