"""Dynamic saltwater fishing regulations lookup.

Regulations are loaded from JSON snapshot data so they can be updated without
code changes. The API payload always includes provenance fields so callers can
see where data came from.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Dict, List, Optional

from storage.species_loader import SPECIES_DB

_STALE_MONTHS = 6  # snapshot data older than this is flagged as potentially outdated


def _months_since(date_str: str) -> float:
    """Return approximate months since a 'YYYY-MM' date string. Returns inf on error."""
    try:
        parts = str(date_str or "").split("-")
        year, month = int(parts[0]), int(parts[1])
        today = date.today()
        return (today.year - year) * 12 + (today.month - month)
    except Exception:
        return float("inf")


logger = logging.getLogger(__name__)

_DEFAULT_REGULATIONS_PATH = (
    Path(__file__).resolve().parent / "storage" / "regulations_data.json"
)
_RELOAD_INTERVAL_SECONDS = 300.0

_STATE_REGULATION_SOURCES: Dict[str, str] = {
    "AL": "https://www.outdooralabama.com/saltwater-fishing",
    "CA": "https://wildlife.ca.gov/Fishing/Ocean/Regulations",
    "DE": "https://dnrec.delaware.gov/fish-wildlife/fishing/saltwater-fishing/",
    "FL": "https://myfwc.com/fishing/saltwater/recreational/",
    "GA": "https://georgiawildlife.com/fishing/regulations",
    "HI": "https://dlnr.hawaii.gov/dar/fishing/fishing-regulations/",
    "LA": "https://www.wlf.louisiana.gov/page/recreational-fishing-regulations",
    "MA": "https://www.mass.gov/saltwater-fishing-regulations",
    "MD": "https://dnr.maryland.gov/fisheries/pages/recreational.aspx",
    "ME": "https://www.maine.gov/dmr/fisheries/recreational",
    "MS": "https://www.mdwfp.com/fishing-boating/saltwater-fishing/",
    "NC": "https://www.deq.nc.gov/about/divisions/marine-fisheries/recreational-fishing",
    "NJ": "https://dep.nj.gov/njfw/fishing/marine/",
    "NY": "https://dec.ny.gov/things-to-do/saltwater-fishing/recreational-regulations",
    "OR": "https://myodfw.com/recreation-report/fishing-report/marine-zone",
    "RI": "https://dem.ri.gov/natural-resources-bureau/fish-wildlife/marine-fisheries/recreational-fishing",
    "SC": "https://www.dnr.sc.gov/marine/recreationalfishing.html",
    "TX": "https://tpwd.texas.gov/regulations/outdoor-annual/fishing/saltwater-fishing-limits",
    "VA": "https://webapps.mrc.virginia.gov/public/reports/swrecfishingrules.php",
    "WA": "https://wdfw.wa.gov/fishing/regulations",
}

_FALLBACK_SOURCE = "https://www.fisheries.noaa.gov/recreational-fishing-rules"


class _RegData:
    def __init__(self) -> None:
        self.name_map: Dict[str, str] = {}
        self.normalized_name_map: Dict[str, str] = {}
        self.states: Dict[str, Dict[str, Dict[str, str]]] = {}
        self.last_updated: str = ""
        self.snapshot_source: str = ""
        self.source_file: str = ""


def _normalize_species_name(name: str) -> str:
    return (
        str(name or "")
        .lower()
        .replace("(", "")
        .replace(")", "")
        .replace("/", " ")
        .replace("-", " ")
        .replace(",", "")
        .strip()
        .replace(" ", "_")
    )


def _species_name_variants(name: str) -> List[str]:
    raw = str(name or "").strip()
    variants: List[str] = []

    normalized = _normalize_species_name(raw)
    if normalized:
        variants.append(normalized)

    no_parenthetical = re.sub(r"\s*\([^)]*\)", "", raw).strip()
    normalized_no_paren = _normalize_species_name(no_parenthetical)
    if normalized_no_paren and normalized_no_paren not in variants:
        variants.append(normalized_no_paren)

    return variants


_REG_DATA = _RegData()
_REG_LOCK = Lock()
_LAST_LOADED_MONO = -_RELOAD_INTERVAL_SECONDS  # ensure first call always loads


def _build_default_name_map() -> Dict[str, str]:
    default_map: Dict[str, str] = {}
    for entry in SPECIES_DB:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        key = _normalize_species_name(name)
        default_map[name] = key
    return default_map


def _resolve_path() -> Path:
    custom = os.getenv("REGULATIONS_DATA_PATH", "").strip()
    return Path(custom) if custom else _DEFAULT_REGULATIONS_PATH


def _load_data_file() -> _RegData:
    data = _RegData()
    data.name_map = _build_default_name_map()
    data.normalized_name_map = {}
    for name, key in data.name_map.items():
        for variant in _species_name_variants(name):
            data.normalized_name_map[variant] = key

    path = _resolve_path()
    data.source_file = str(path)
    if not path.exists():
        return data

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse regulations_data.json: %s", exc)
        return data
    if not isinstance(raw, dict):
        return data

    custom_name_map = raw.get("name_map")
    if isinstance(custom_name_map, dict):
        for name, key in custom_name_map.items():
            if (
                isinstance(name, str)
                and isinstance(key, str)
                and name.strip()
                and key.strip()
            ):
                clean_name = name.strip()
                clean_key = key.strip()
                data.name_map[clean_name] = clean_key
                for variant in _species_name_variants(clean_name):
                    data.normalized_name_map[variant] = clean_key

    states = raw.get("states")
    if isinstance(states, dict):
        normalized_states: Dict[str, Dict[str, Dict[str, str]]] = {}
        for st, regs in states.items():
            if not isinstance(st, str) or not isinstance(regs, dict):
                continue
            st_key = st.upper().strip()
            normalized_states[st_key] = {}
            for species_key, details in regs.items():
                if not isinstance(species_key, str) or not isinstance(details, dict):
                    continue
                normalized_states[st_key][species_key.strip()] = {
                    "min_size": str(details.get("min_size") or "").strip(),
                    "bag_limit": str(details.get("bag_limit") or "").strip(),
                    "season": str(details.get("season") or "").strip(),
                    "notes": str(details.get("notes") or "").strip(),
                    "source": str(details.get("source") or "").strip(),
                }
        data.states = normalized_states

    data.last_updated = str(raw.get("last_updated") or "").strip()
    data.snapshot_source = str(raw.get("snapshot_source") or "").strip()
    return data


def _ensure_data_loaded() -> None:
    global _LAST_LOADED_MONO
    now = monotonic()
    if now - _LAST_LOADED_MONO < _RELOAD_INTERVAL_SECONDS:
        return
    with _REG_LOCK:
        now = monotonic()
        if now - _LAST_LOADED_MONO < _RELOAD_INTERVAL_SECONDS:
            return
        loaded = _load_data_file()
        _REG_DATA.name_map = loaded.name_map
        _REG_DATA.normalized_name_map = loaded.normalized_name_map
        _REG_DATA.states = loaded.states
        _REG_DATA.last_updated = loaded.last_updated
        _REG_DATA.snapshot_source = loaded.snapshot_source
        _REG_DATA.source_file = loaded.source_file
        _LAST_LOADED_MONO = now


def _base_payload(state: str) -> Dict[str, str]:
    source = _STATE_REGULATION_SOURCES.get(state, _FALLBACK_SOURCE)
    return {
        "min_size": "",
        "bag_limit": "",
        "season": "",
        "notes": "",
        "official_source": source,
        "snapshot_source": _REG_DATA.snapshot_source,
        "source_file": _REG_DATA.source_file,
        "data_status": "official_reference",
        "last_updated": _REG_DATA.last_updated,
        "is_stale": _months_since(_REG_DATA.last_updated) >= _STALE_MONTHS,  # type: ignore[dict-item]
        "fetched_at": "",
    }


# ---------------------------------------------------------------------------
# Month abbreviations for closed-season parsing in classify_legality
# ---------------------------------------------------------------------------
_LEGALITY_MONTH_ABBREVS: Dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_CLOSED_RANGE_RE = re.compile(r"closed\s+([a-z]+)[–\-]([a-z]+)")
_CLOSED_SEASON_RE = re.compile(r"(season\s+closed|closed\s+season)", re.IGNORECASE)
_QUALIFIER_WORDS = frozenset(
    ("some", "certain", "have", "having", "with", "may", "areas")
)


def _parse_closed_months_text(text: str) -> set:
    """Parse month ranges from text like 'closed Jan-May'.

    Returns the set of month numbers (1-12) that are closed.
    Handles year-wrap ranges such as 'closed Nov-Feb'.
    """
    closed: set = set()
    for m in _CLOSED_RANGE_RE.finditer(text.lower()):
        start_str = m.group(1)[:3]
        end_str = m.group(2)[:3]
        start = _LEGALITY_MONTH_ABBREVS.get(start_str)
        end = _LEGALITY_MONTH_ABBREVS.get(end_str)
        if start and end:
            if end >= start:
                closed.update(range(start, end + 1))
            else:
                # Wraps around the year-end, e.g. Nov-Feb
                closed.update(range(start, 13))
                closed.update(range(1, end + 1))
    return closed


def get_official_regulations_url(state: str) -> str:
    """Return the official state fishing regulations URL for *state* (2-letter code).

    Falls back to the NOAA recreational fishing rules page when the state is
    not recognised or when *state* is empty.
    """
    return _STATE_REGULATION_SOURCES.get(
        (state or "").upper().strip(), _FALLBACK_SOURCE
    )


def classify_legality(reg: Optional[Dict], month: int = 0) -> str:
    """Return a normalised legality status for a regulation payload.

    Returns one of::

        "legal"             — open fishery with standard bag/size limits
        "catch_and_release" — legal to target; all catch must be released
        "restricted"        — conditional rules apply; angler must verify before keeping
        "out_of_season"     — currently inside a closed season window for this month
        "prohibited"        — fishery closed; do not target (year-round closure, federal
                              protection)
        "unknown"           — no usable regulation data found for this state/species

    The :func:`should_hide_from_forecast` helper enforces a strict visibility
    policy: only ``"legal"`` status allows a species to appear in forecast
    recommendation surfaces.  All other statuses — including ``"unknown"`` where
    regulations could not be verified — are hidden so the app never recommends
    targeting a species unless legality is confirmed.

    This function is **advisory only**.  Regulations change frequently; always
    verify with the official state source before fishing.

    Args:
        reg:   Regulation dict returned by :func:`lookup_regulation`, or ``None``.
        month: Current month (1-12) used to evaluate month-specific closures.
               Pass 0 (default) to skip month-specific checks.
    """
    if not reg:
        return "unknown"

    bag_limit = str(reg.get("bag_limit") or "").strip().lower()
    season = str(reg.get("season") or "").strip().lower()
    notes = str(reg.get("notes") or "").strip().lower()
    combined = " ".join(p for p in (bag_limit, season, notes) if p)

    # No meaningful regulation content at all — link-only fallback payload.
    # Check *combined* (not individual fields) so that notes-only payloads
    # with genuine prohibition text still get classified correctly below.
    if not combined:
        return "unknown"
    if "species-specific limits were not found" in notes:
        return "unknown"

    # ── Step 1: Truly closed fisheries — check BEFORE C&R phrases so that
    #    e.g. "season closed, bag limit 0" is correctly classified as
    #    out_of_season (cannot target) rather than catch_and_release. ─────────

    # Year-round / federal closures where targeting is not permitted
    _TRULY_CLOSED = (
        "closed year-round",
        "federally protected",
        "endangered species",
        "prohibited year-round",
    )
    if any(phrase in combined for phrase in _TRULY_CLOSED):
        return "prohibited"

    # Month-specific seasonal closure ("closed Jan–Apr", etc.)
    if month and month in _parse_closed_months_text(combined):
        return "out_of_season"

    # Standalone "season closed" / "closed season" (without qualifying hedges)
    for m in _CLOSED_SEASON_RE.finditer(combined):
        preceding = combined[max(0, m.start() - 30) : m.start()].lower()
        if not any(q in preceding for q in _QUALIFIER_WORDS):
            return "out_of_season"

    # ── Step 2: Catch-and-release — fishery is open for targeting, retention
    #    is not permitted.  Show with a C&R badge; do NOT hide. ──────────────

    # Phrases that unambiguously mean "the whole fishery is C&R" — checked
    # against combined (bag + season + notes) because they are unambiguous
    # even in note text (e.g. "catch and release only; no harvest").
    _CATCH_AND_RELEASE_STRONG = (
        "catch and release only",
        "catch-and-release only",
        "harvest prohibited",
        "retention prohibited",
        "possession prohibited",
        "cannot be retained",
    )
    if any(phrase in combined for phrase in _CATCH_AND_RELEASE_STRONG):
        return "catch_and_release"

    # "must be released" and "no harvest" are ambiguous in the notes field:
    # slot-limit and size-limit rules frequently say things like
    # "fish outside the slot must be released" or "juveniles must be released",
    # which describe *partial* restrictions on one size class — not a whole-
    # fishery closure.  Only treat these phrases as C&R signals when they
    # appear in the bag_limit or season fields, where they are unambiguous.
    _CATCH_AND_RELEASE_BAG_OR_SEASON = (
        "must be released",
        "no harvest",
    )
    bag_season = " ".join(p for p in (bag_limit, season) if p)
    if any(phrase in bag_season for phrase in _CATCH_AND_RELEASE_BAG_OR_SEASON):
        return "catch_and_release"

    # bag_limit of zero means no retention allowed — effectively C&R
    if bag_limit in {"0", "0/day", "0 per day", "0 fish"}:
        return "catch_and_release"

    # Explicit zero in bag_limit text (e.g. "0 — prohibited")
    if (
        bag_limit.startswith("0 ")
        or bag_limit.startswith("0—")
        or bag_limit.startswith("0 —")
    ):
        return "catch_and_release"

    # ── Step 3: Restricted — conditional rules that require angler verification ─
    # Permit/tag-based harvest (can target; need a permit to keep)
    _PERMIT_REQUIRED = (
        "harvest tag required",
        "harvest permit required",
        "tag required to harvest",
    )
    if any(phrase in combined for phrase in _PERMIT_REQUIRED):
        return "restricted"

    if "seasonal" in season or ("check" in season and "open year-round" not in season):
        return "restricted"
    _SOFT_RESTRICTED = (
        "some areas",
        "certain areas",
        "may be closed",
        "area closures",
        "varies by",
    )
    if any(phrase in combined for phrase in _SOFT_RESTRICTED):
        return "restricted"

    return "legal"


def should_hide_from_forecast(status: str) -> bool:
    """Return True when a species should be suppressed from 'What\'s Biting',
    'What\'s Spawning Now', and all other ranked forecast surfaces.

    The rule is intentionally strict: a species is shown **only** when its
    status is ``"legal"``.  Every other outcome is hidden:

      - ``"catch_and_release"`` — retention/harvest prohibited; cannot keep the fish
      - ``"restricted"``        — conditional rules that may prohibit retention
      - ``"out_of_season"``     — currently inside a closed season window
      - ``"prohibited"``        — year-round closure or federal protection
      - ``"unknown"``           — regulations could not be verified; we must not
                                  recommend targeting a species of unknown legality

    Only ``"legal"`` is visible because that is the only status that confirms
    the angler can lawfully target and keep the species at the current time.
    """
    return status != "legal"


def lookup_regulation(species_name: str, state: str) -> Optional[Dict[str, str]]:
    """Look up fishing regulations for a species in a state.

    Tries in order:
      1. Live scrape from the official state agency website (cached 24 h).
      2. Local JSON snapshot (storage/regulations_data.json).
      3. Returns a bare payload with just the official-source link so the
         modal can still point the angler somewhere useful.
    """
    state_key = (state or "").upper().strip()
    if not state_key:
        return None

    _ensure_data_loaded()

    payload = _base_payload(state_key)

    # ── 1. Try live scraper ──────────────────────────────────────────
    try:
        from storage.reg_scraper import scrape_regulation

        scraped = scrape_regulation(species_name, state_key)
        if scraped:
            payload.update(scraped)
            payload["data_status"] = "live"
            payload["is_stale"] = False  # type: ignore[assignment]
            # Make sure official_source is always set
            if not payload.get("official_source"):
                payload["official_source"] = _STATE_REGULATION_SOURCES.get(
                    state_key, _FALLBACK_SOURCE
                )
            return payload
    except Exception:
        logger.warning(
            "Live regulation scraper failed for %r/%r; falling back to snapshot",
            species_name,
            state_key,
            exc_info=True,
        )

    # ── 2. Static JSON snapshot ──────────────────────────────────────
    species_key = _REG_DATA.name_map.get(species_name)
    normalized_variants = _species_name_variants(species_name)
    if not species_key:
        for normalized_name in normalized_variants:
            species_key = _REG_DATA.normalized_name_map.get(normalized_name)
            if species_key:
                break
    state_regs = _REG_DATA.states.get(state_key)
    if state_regs and (not species_key or species_key not in state_regs):
        for normalized_name in normalized_variants:
            if normalized_name in state_regs:
                species_key = normalized_name
                break

    matched = state_regs.get(species_key) if state_regs and species_key else None

    if matched:
        payload.update(matched)
        payload["data_status"] = "snapshot"
        payload["is_stale"] = (
            _months_since(payload.get("last_updated", "")) >= _STALE_MONTHS  # type: ignore[assignment]
        )
        if payload.get("source"):
            payload["snapshot_source"] = payload["source"]
        return payload

    # ── 3. Nothing found — return link-only payload ──────────────────
    payload["notes"] = (
        "Species-specific limits were not found. "
        "Use the official source link for current bag, size, and season rules."
    )
    return payload
