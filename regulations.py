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
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}

_CLOSED_RANGE_RE = re.compile(r"closed\s+([a-z]+)[–\-]([a-z]+)")
_CLOSED_SEASON_RE = re.compile(r"(season\s+closed|closed\s+season)", re.IGNORECASE)
_QUALIFIER_WORDS = frozenset(("some", "certain", "have", "having", "with", "may", "areas"))


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
    return _STATE_REGULATION_SOURCES.get((state or "").upper().strip(), _FALLBACK_SOURCE)


def classify_legality(reg: Optional[Dict], month: int = 0) -> str:
    """Return a normalised legality status for a regulation payload.

    Returns one of::

        "legal"         — regulations confirm the species is currently open to harvest
        "restricted"    — seasonal/area-specific rules apply; angler must verify
        "out_of_season" — currently inside a closed season window for this month
        "prohibited"    — harvest definitively not allowed (C&R, bag=0, federally protected)
        "unknown"       — no usable regulation data found for this state/species

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

    # ── Hard prohibitions ───────────────────────────────────────────────────
    _HARD_PROHIBITED = (
        "catch and release only",
        "catch-and-release only",
        "no harvest",
        "harvest prohibited",
        "retention prohibited",
        "possession prohibited",
        "must be released",
        "cannot be retained",
        "closed year-round",
        "harvest tag required",
        "harvest permit required",
        "tag required to harvest",
        "federally protected",
        "endangered species",
    )
    if any(phrase in combined for phrase in _HARD_PROHIBITED):
        return "prohibited"
    if bag_limit in {"0", "0/day", "0 per day", "0 fish", "none"}:
        return "prohibited"

    # ── Month-specific seasonal closure ─────────────────────────────────────
    if month and month in _parse_closed_months_text(combined):
        return "out_of_season"

    # Standalone "season closed" / "closed season" (without qualifying hedges)
    for m in _CLOSED_SEASON_RE.finditer(combined):
        preceding = combined[max(0, m.start() - 30): m.start()].lower()
        if not any(q in preceding for q in _QUALIFIER_WORDS):
            return "out_of_season"

    # ── Soft restrictions ────────────────────────────────────────────────────
    if "seasonal" in season or ("check" in season and "open year-round" not in season):
        return "restricted"
    _SOFT_RESTRICTED = ("some areas", "certain areas", "may be closed", "area closures", "varies by")
    if any(phrase in combined for phrase in _SOFT_RESTRICTED):
        return "restricted"

    return "legal"


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
