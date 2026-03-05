"""Dynamic saltwater fishing regulations lookup.

Regulations are loaded from JSON snapshot data so they can be updated without
code changes. The API payload always includes provenance fields so callers can
see where data came from.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Dict, Optional

from storage.species_loader import SPECIES_DB

_DEFAULT_REGULATIONS_PATH = Path(__file__).resolve().parent / "storage" / "regulations_data.json"
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
        self.states: Dict[str, Dict[str, Dict[str, str]]] = {}
        self.last_updated: str = ""
        self.snapshot_source: str = ""
        self.source_file: str = ""


_REG_DATA = _RegData()
_REG_LOCK = Lock()
_LAST_LOADED_MONO = 0.0


def _build_default_name_map() -> Dict[str, str]:
    default_map: Dict[str, str] = {}
    for entry in SPECIES_DB:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        key = (
            name.lower()
            .replace("(", "")
            .replace(")", "")
            .replace("/", " ")
            .replace("-", " ")
            .replace(",", "")
            .strip()
            .replace(" ", "_")
        )
        default_map[name] = key
    return default_map


def _resolve_path() -> Path:
    custom = os.getenv("REGULATIONS_DATA_PATH", "").strip()
    return Path(custom) if custom else _DEFAULT_REGULATIONS_PATH


def _load_data_file() -> _RegData:
    data = _RegData()
    data.name_map = _build_default_name_map()

    path = _resolve_path()
    data.source_file = str(path)
    if not path.exists():
        return data

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return data

    custom_name_map = raw.get("name_map")
    if isinstance(custom_name_map, dict):
        for name, key in custom_name_map.items():
            if isinstance(name, str) and isinstance(key, str) and name.strip() and key.strip():
                data.name_map[name.strip()] = key.strip()

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
    state_regs = _REG_DATA.states.get(state_key) if species_key else None
    matched = state_regs.get(species_key) if state_regs else None

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
