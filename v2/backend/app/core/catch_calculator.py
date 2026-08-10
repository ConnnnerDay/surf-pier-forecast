"""Legal-catch calculator: given a regulation payload from regulations.py
and a measured fish length, decide whether it's legal to keep right now.

This is new engineering for v2, not a port — v1 exposes regulation lookups
(min/max size text, bag limits, season) but never built a calculator that
actually evaluates a specific catch against them (see docs/V2_PLAN.md §3
"Legal-catch calculator"). regulations.py's min_size/slot fields are messy
scraped/hand-compiled free text ("18 in TL", "12 in TL in Gulf; 14 in TL in
Atlantic", "Varies by area"), so the size parsing here is deliberately
conservative: an ambiguous or unparseable limit returns "verify manually"
rather than guessing at legality. This is advisory only — regulations
change and vary by sub-area; the UI must keep saying so.
"""

import re
from typing import Literal, TypedDict

Verdict = Literal["legal", "too_small", "too_large", "cannot_target", "unknown"]

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:in\b|inch|\")", re.IGNORECASE)
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)")
_NO_MINIMUM_RE = re.compile(r"\bno\s+minimum\b|^\s*none\b", re.IGNORECASE)

# classify_legality() statuses (see regulations.py) that mean "don't even
# get to the size question — this fish can't be kept right now regardless
# of how big it is".
_CANNOT_TARGET_STATUSES = {"prohibited", "out_of_season", "catch_and_release"}

_CANNOT_TARGET_REASONS = {
    "prohibited": "This species is protected or closed to harvest — do not "
    "keep it regardless of size.",
    "out_of_season": "The season is currently closed for this species — do "
    "not keep it right now.",
    "catch_and_release": "This fishery is catch-and-release only — do not "
    "keep it regardless of size.",
}


class CatchEvaluation(TypedDict):
    verdict: Verdict
    legal: bool | None
    reason: str
    min_size_in: float | None
    max_size_in: float | None


def parse_size_bounds(reg: dict) -> tuple[float | None, float | None, bool]:
    """Extract (min_inches, max_inches, confident) from a regulation payload.

    confident=False means the text couldn't be parsed with enough certainty
    to give a legal/illegal answer — the caller should surface "verify
    manually" rather than a number that might be wrong for this angler's
    specific sub-area.
    """
    slot_text = str(reg.get("slot") or "")
    if slot_text:
        m = _RANGE_RE.search(slot_text)
        if m:
            return float(m.group(1)), float(m.group(2)), True

    min_text = str(reg.get("min_size") or "")
    if not min_text.strip():
        return None, None, False
    if _NO_MINIMUM_RE.search(min_text):
        return 0.0, None, True

    # A regulation string with more than one number ("12 in TL in Gulf; 14
    # in TL in Atlantic") is genuinely ambiguous without a sub-area input we
    # don't collect — don't silently pick the first one.
    numbers = _SIZE_RE.findall(min_text)
    if len(numbers) == 1:
        return float(numbers[0]), None, True

    return None, None, False


def evaluate_catch(reg: dict | None, legality_status: str, length_in: float) -> CatchEvaluation:
    """Decide whether a fish of `length_in` is legal to keep, given the
    already-computed classify_legality() status for this species/state/month
    and the raw regulation payload for size parsing."""
    if reg is None or legality_status == "unknown":
        return CatchEvaluation(
            verdict="unknown",
            legal=None,
            reason="No regulation data found for this species/state — "
            "verify with the official source before keeping anything.",
            min_size_in=None,
            max_size_in=None,
        )

    if legality_status in _CANNOT_TARGET_STATUSES:
        return CatchEvaluation(
            verdict="cannot_target",
            legal=False,
            reason=_CANNOT_TARGET_REASONS[legality_status],
            min_size_in=None,
            max_size_in=None,
        )

    min_in, max_in, confident = parse_size_bounds(reg)
    if not confident or min_in is None:
        return CatchEvaluation(
            verdict="unknown",
            legal=None,
            reason="Size limit text for this species/state couldn't be "
            "parsed with confidence — verify the exact limit with the "
            "official source before keeping it.",
            min_size_in=None,
            max_size_in=None,
        )

    if length_in < min_in:
        return CatchEvaluation(
            verdict="too_small",
            legal=False,
            reason=f'Under the {min_in:g}" minimum — release it.',
            min_size_in=min_in,
            max_size_in=max_in,
        )
    if max_in is not None and length_in > max_in:
        return CatchEvaluation(
            verdict="too_large",
            legal=False,
            reason=f'Over the {max_in:g}" slot maximum — release it.',
            min_size_in=min_in,
            max_size_in=max_in,
        )

    size_range = f'{min_in:g}"+' if max_in is None else f'{min_in:g}"-{max_in:g}"'
    return CatchEvaluation(
        verdict="legal",
        legal=True,
        reason=f"Within the legal size range ({size_range}). Bag limit and "
        "season rules still apply — this only checks size.",
        min_size_in=min_in,
        max_size_in=max_in,
    )
