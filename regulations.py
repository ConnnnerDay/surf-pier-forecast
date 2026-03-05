"""Dynamic saltwater fishing regulations lookup.

Regulations are loaded from JSON snapshot data so they can be updated without
code changes. A legacy embedded dataset is also loaded as a fallback so common
species still resolve when snapshot coverage is partial.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Dict, List, Optional

from storage.species_loader import SPECIES_DB

_DEFAULT_REGULATIONS_PATH = Path(__file__).resolve().parent / "storage" / "regulations_data.json"
_LEGACY_REGULATIONS_PATH = Path(__file__).resolve().parent / "storage" / "legacy_regulations_data.json"
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


def _normalize_state_rows(states: object) -> Dict[str, Dict[str, Dict[str, str]]]:
    normalized_states: Dict[str, Dict[str, Dict[str, str]]] = {}
    if not isinstance(states, dict):
        return normalized_states

    for st, regs in states.items():
        if not isinstance(st, str) or not isinstance(regs, dict):
            continue
        st_key = st.upper().strip()
        state_rows: Dict[str, Dict[str, str]] = {}
        for species_key, details in regs.items():
            if not isinstance(species_key, str) or not isinstance(details, dict):
                continue
            state_rows[species_key.strip()] = {
                "min_size": str(details.get("min_size") or "").strip(),
                "bag_limit": str(details.get("bag_limit") or "").strip(),
                "season": str(details.get("season") or "").strip(),
                "notes": str(details.get("notes") or "").strip(),
                "source": str(details.get("source") or "").strip(),
            }
        normalized_states[st_key] = state_rows

    return normalized_states


_REG_DATA = _RegData()
_REG_LOCK = Lock()
_LAST_LOADED_MONO = 0.0


def _build_default_name_map() -> Dict[str, str]:
    default_map: Dict[str, str] = {}
    for entry in SPECIES_DB:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        default_map[name] = _normalize_species_name(name)
    return default_map


def _resolve_path() -> Path:
    custom = os.getenv("REGULATIONS_DATA_PATH", "").strip()
    return Path(custom) if custom else _DEFAULT_REGULATIONS_PATH


def _apply_name_map(data: _RegData, name_map: object) -> None:
    if not isinstance(name_map, dict):
        return
    for name, key in name_map.items():
        if not isinstance(name, str) or not isinstance(key, str) or not name.strip() or not key.strip():
            continue
        clean_name = name.strip()
        clean_key = key.strip()
        data.name_map[clean_name] = clean_key
        for variant in _species_name_variants(clean_name):
            data.normalized_name_map[variant] = clean_key


def _load_data_file() -> _RegData:
    data = _RegData()
    data.name_map = _build_default_name_map()
    for name, key in data.name_map.items():
        for variant in _species_name_variants(name):
            data.normalized_name_map[variant] = key

    data.source_file = str(_resolve_path())

    # 1) Load legacy fallback first (broad coverage)
    if _LEGACY_REGULATIONS_PATH.exists():
        legacy_raw = json.loads(_LEGACY_REGULATIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(legacy_raw, dict):
            _apply_name_map(data, legacy_raw.get("name_map"))
            data.states = _normalize_state_rows(legacy_raw.get("states"))
            data.snapshot_source = str(legacy_raw.get("snapshot_source") or "").strip()

    # 2) Overlay current snapshot (authoritative overrides)
    path = _resolve_path()
    if not path.exists():
        return data

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return data

    _apply_name_map(data, raw.get("name_map"))

    snapshot_states = _normalize_state_rows(raw.get("states"))
    for st, regs in snapshot_states.items():
        base = data.states.setdefault(st, {})
        base.update(regs)

    data.last_updated = str(raw.get("last_updated") or "").strip()
    raw_snapshot_source = str(raw.get("snapshot_source") or "").strip()
    if raw_snapshot_source:
        data.snapshot_source = raw_snapshot_source
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
    }


def lookup_regulation(species_name: str, state: str) -> Optional[Dict[str, str]]:
    """Look up fishing regulations for a species in a state."""
    state_key = (state or "").upper().strip()
    if not state_key:
        return None

    _ensure_data_loaded()

    payload = _base_payload(state_key)
    species_key = _REG_DATA.name_map.get(species_name)
    normalized_variants = _species_name_variants(species_name)
    if not species_key:
        for normalized_name in normalized_variants:
            species_key = _REG_DATA.normalized_name_map.get(normalized_name)
            if species_key:
                break

    state_regs = _REG_DATA.states.get(state_key)
    if not species_key and state_regs:
        for normalized_name in normalized_variants:
            if normalized_name in state_regs:
                species_key = normalized_name
                break

    matched = state_regs.get(species_key) if state_regs and species_key else None

    if matched:
        payload.update(matched)
        payload["data_status"] = "snapshot"
        if payload.get("source"):
            payload["snapshot_source"] = payload["source"]
        return payload

    payload["notes"] = (
        "Species-specific limits were not found in the local snapshot. "
        "Use the official source link for current bag, size, and season rules."
    )
    return payload
