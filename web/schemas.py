"""API schema helpers: dataclass-based request parsing and stable envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ApiError(Exception):
    code: str
    message: str
    status: int = 400
    details: Optional[Dict[str, Any]] = None


def success_envelope(data: Dict[str, Any], *, version: str = "v1", meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"ok": True, "data": data, "error": None, "meta": {"version": version}}
    if meta:
        payload["meta"].update(meta)
    return payload


def error_envelope(code: str, message: str, *, version: str = "v1", details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message, "details": details or {}},
        "meta": {"version": version},
    }


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y", "on"}


@dataclass
class ForecastQuery:
    location_id: str = ""
    force_refresh: bool = False

    @classmethod
    def from_request(cls, args: Dict[str, Any], fallback_location_id: str = "") -> "ForecastQuery":
        loc_id = (args.get("location_id") or "").strip() or fallback_location_id
        return cls(location_id=loc_id, force_refresh=parse_bool(args.get("force_refresh"), False))


@dataclass
class ProfilePayload:
    location_id: Optional[str] = None
    theme: Optional[str] = None
    units: Optional[str] = None
    fishing_profile: Optional[Dict[str, Any]] = None
    favorites: Optional[List[str]] = None

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "ProfilePayload":
        if not isinstance(data, dict):
            raise ApiError("invalid_payload", "Request body must be a JSON object", status=400)

        theme = data.get("theme")
        if theme is not None and theme not in {"light", "dark"}:
            raise ApiError("invalid_theme", "theme must be 'light' or 'dark'", status=400)

        units = data.get("units")
        if units is not None and units not in {"F", "C"}:
            raise ApiError("invalid_units", "units must be 'F' or 'C'", status=400)

        favorites = data.get("favorites")
        if favorites is not None:
            if not isinstance(favorites, list) or not all(isinstance(x, str) for x in favorites):
                raise ApiError("invalid_favorites", "favorites must be a list of strings", status=400)

        _VALID_FISHING_TYPES = {"surf", "pier", "inshore", "offshore"}
        _VALID_TARGETS = {"bottom", "pelagic", "structure", "gamefish", "anything"}

        fishing_profile = data.get("fishing_profile")
        if fishing_profile is not None:
            if not isinstance(fishing_profile, dict):
                raise ApiError("invalid_profile", "fishing_profile must be an object", status=400)
            fp_types = fishing_profile.get("fishing_types")
            if fp_types is not None:
                if not isinstance(fp_types, list) or not all(
                    isinstance(x, str) and x in _VALID_FISHING_TYPES for x in fp_types
                ):
                    raise ApiError(
                        "invalid_fishing_types",
                        f"fishing_types must be a list of: {sorted(_VALID_FISHING_TYPES)}",
                        status=400,
                    )
            fp_targets = fishing_profile.get("targets")
            if fp_targets is not None:
                if not isinstance(fp_targets, list) or not all(
                    isinstance(x, str) and x in _VALID_TARGETS for x in fp_targets
                ):
                    raise ApiError(
                        "invalid_targets",
                        f"targets must be a list of: {sorted(_VALID_TARGETS)}",
                        status=400,
                    )

        location_id = data.get("location_id")
        if location_id is not None and not isinstance(location_id, str):
            raise ApiError("invalid_location_id", "location_id must be a string", status=400)

        return cls(
            location_id=location_id,
            theme=theme,
            units=units,
            fishing_profile=fishing_profile,
            favorites=favorites,
        )

    def as_updates(self) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        for k in ("location_id", "theme", "units", "fishing_profile", "favorites"):
            v = getattr(self, k)
            if v is not None:
                updates[k] = v
        return updates


@dataclass
class LogCreatePayload:
    species: str
    size: str = ""
    notes: str = ""
    location_id: str = ""

    @classmethod
    def from_json(cls, data: Dict[str, Any], location_id: str = "") -> "LogCreatePayload":
        if not isinstance(data, dict):
            raise ApiError("invalid_payload", "Request body must be a JSON object", status=400)
        species = str(data.get("species", "")).strip()
        if not species:
            raise ApiError("missing_species", "species is required", status=400)
        size = str(data.get("size", "")).strip()
        notes = str(data.get("notes", "")).strip()
        loc = str(data.get("location_id", "")).strip() or location_id
        if not loc:
            raise ApiError("missing_location", "location_id is required", status=400)
        return cls(species=species, size=size, notes=notes, location_id=loc)


def normalize_log_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    base = {
        "total": 0,
        "unique_species": 0,
        "top_species": None,
        "last_date": None,
        "species_breakdown": [],
        "monthly_counts": {},
    }
    base.update(stats or {})
    return base


def normalize_preferences(prefs: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "location_id": prefs.get("location_id"),
        "theme": prefs.get("theme", "light"),
        "units": prefs.get("units", "F"),
        "fishing_profile": prefs.get("fishing_profile"),
        "favorites": prefs.get("favorites", []),
    }
