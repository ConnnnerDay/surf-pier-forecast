"""API schema helpers: dataclass-based request parsing and stable envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ApiError(Exception):
    code: str
    message: str
    status: int = 400
    details: Optional[dict[str, Any]] = None


def success_envelope(
    data: dict[str, Any], *, version: str = "v1", meta: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "data": data,
        "error": None,
        "meta": {"version": version},
    }
    if meta:
        payload["meta"].update(meta)
    return payload


def error_envelope(
    code: str,
    message: str,
    *,
    version: str = "v1",
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message, "details": details or {}},
        "meta": {"version": version},
    }


_MAX_FAVORITES = 20
_MAX_FAVORITES_ENTRY_LEN = 100
_MAX_LOCATION_ID_LEN = 100
_MAX_SPECIES_LEN = 100
_MAX_SIZE_LEN = 50
_MAX_NOTES_LEN = 1000
_MAX_BAIT_LEN = 60

_VALID_FISHING_TYPES = frozenset(
    {
        "surf",
        "pier",
        "jetty",
        "bridge",
        "wade",
        "kayak",
        "inshore",
        "offshore",
        "fly",
        "charter",
    }
)
_VALID_TARGETS = frozenset(
    {"bottom", "pelagic", "structure", "inshore_slam", "gamefish", "anything"}
)
_VALID_EXPERIENCE = frozenset({"beginner", "intermediate", "experienced"})
_VALID_BAIT_PREF = frozenset({"yes", "sometimes", "no", "skip"})
_VALID_PREFERRED_TIMES = frozenset({"dawn", "morning", "afternoon", "evening", "night", "anytime"})
_VALID_PRIMARY_GOAL = frozenset({"action", "trophy", "relaxing", "exploring"})
_VALID_CONDITION_TOLERANCE = frozenset({"calm", "moderate", "rough"})
_VALID_TIDE_PREFERENCE = frozenset({"incoming", "outgoing", "high", "low", "any"})
_VALID_SESSION_FREQUENCY = frozenset({"weekly", "monthly", "occasional"})
_VALID_CATCH_RELEASE = frozenset({"always", "sometimes", "keep"})

# Notification preferences: the minimum forecast rating that triggers an alert,
# and how many hours of lead time the daily check looks ahead.
_VALID_MIN_RATING = frozenset({"Good", "Excellent"})
_NOTIFY_LEAD_HOURS_MAX = 24


def _validate_notification_prefs(value: Any) -> dict[str, Any]:
    """Validate and normalize the notification_prefs object.

    Shape: {enabled: bool, email: bool, push: bool,
            min_rating: "Good"|"Excellent", lead_hours: int 0..24}
    Unknown keys are dropped so the stored object stays small and predictable.
    """
    if not isinstance(value, dict):
        raise ApiError(
            "invalid_notification_prefs",
            "notification_prefs must be an object",
            status=400,
        )
    out: dict[str, Any] = {}
    for flag in ("enabled", "email", "push", "weekly_email"):
        fv = value.get(flag)
        if fv is not None:
            if not isinstance(fv, bool):
                raise ApiError(
                    f"invalid_notification_{flag}",
                    f"notification_prefs.{flag} must be a boolean",
                    status=400,
                )
            out[flag] = fv
    min_rating = value.get("min_rating")
    if min_rating is not None:
        if min_rating not in _VALID_MIN_RATING:
            raise ApiError(
                "invalid_min_rating",
                f"min_rating must be one of: {sorted(_VALID_MIN_RATING)}",
                status=400,
            )
        out["min_rating"] = min_rating
    lead_hours = value.get("lead_hours")
    if lead_hours is not None:
        if (
            isinstance(lead_hours, bool)
            or not isinstance(lead_hours, int)
            or not (0 <= lead_hours <= _NOTIFY_LEAD_HOURS_MAX)
        ):
            raise ApiError(
                "invalid_lead_hours",
                f"lead_hours must be an integer between 0 and {_NOTIFY_LEAD_HOURS_MAX}",
                status=400,
            )
        out["lead_hours"] = lead_hours
    return out


def _validate_enum_list(field: str, value: Any, valid: frozenset) -> None:
    if not isinstance(value, list) or not all(isinstance(x, str) and x in valid for x in value):
        raise ApiError(
            f"invalid_{field}",
            f"{field} must be a list of: {sorted(valid)}",
            status=400,
        )


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
    def from_request(
        cls, args: dict[str, Any], fallback_location_id: str = ""
    ) -> "ForecastQuery":
        loc_id = (args.get("location_id") or "")[:100].strip() or fallback_location_id
        return cls(
            location_id=loc_id,
            force_refresh=parse_bool(args.get("force_refresh"), False),
        )


@dataclass
class ProfilePayload:
    location_id: Optional[str] = None
    theme: Optional[str] = None
    units: Optional[str] = None
    fishing_profile: Optional[dict[str, Any]] = None
    favorites: Optional[list[str]] = None
    notification_prefs: Optional[dict[str, Any]] = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ProfilePayload":
        if not isinstance(data, dict):
            raise ApiError(
                "invalid_payload", "Request body must be a JSON object", status=400
            )

        theme = data.get("theme")
        if theme is not None and theme not in {"light", "dark"}:
            raise ApiError(
                "invalid_theme", "theme must be 'light' or 'dark'", status=400
            )

        units = data.get("units")
        if units is not None and units not in {"F", "C"}:
            raise ApiError("invalid_units", "units must be 'F' or 'C'", status=400)

        favorites = data.get("favorites")
        if favorites is not None:
            if not isinstance(favorites, list):
                raise ApiError(
                    "invalid_favorites",
                    "favorites must be a list of strings",
                    status=400,
                )
            if len(favorites) > _MAX_FAVORITES:
                raise ApiError(
                    "invalid_favorites",
                    f"favorites may not contain more than {_MAX_FAVORITES} entries",
                    status=400,
                )
            if not all(isinstance(x, str) and len(x) <= _MAX_FAVORITES_ENTRY_LEN for x in favorites):
                raise ApiError(
                    "invalid_favorites",
                    f"favorites must be a list of strings (max {_MAX_FAVORITES_ENTRY_LEN} characters each)",
                    status=400,
                )

        fishing_profile = data.get("fishing_profile")
        if fishing_profile is not None:
            if not isinstance(fishing_profile, dict):
                raise ApiError(
                    "invalid_profile", "fishing_profile must be an object", status=400
                )
            fp_types = fishing_profile.get("fishing_types")
            if fp_types is not None:
                _validate_enum_list("fishing_types", fp_types, _VALID_FISHING_TYPES)
            fp_targets = fishing_profile.get("targets")
            if fp_targets is not None:
                _validate_enum_list("targets", fp_targets, _VALID_TARGETS)
            fp_experience = fishing_profile.get("experience")
            if fp_experience is not None and fp_experience not in _VALID_EXPERIENCE:
                raise ApiError(
                    "invalid_experience",
                    f"experience must be one of: {sorted(_VALID_EXPERIENCE)}",
                    status=400,
                )
            for bait_field in ("live_bait", "cut_bait", "lures"):
                bait_val = fishing_profile.get(bait_field)
                if bait_val is not None and bait_val not in _VALID_BAIT_PREF:
                    raise ApiError(
                        f"invalid_{bait_field}",
                        f"{bait_field} must be one of: {sorted(_VALID_BAIT_PREF)}",
                        status=400,
                    )
            fp_preferred_times = fishing_profile.get("preferred_times")
            if fp_preferred_times is not None:
                _validate_enum_list("preferred_times", fp_preferred_times, _VALID_PREFERRED_TIMES)
            fp_primary_goal = fishing_profile.get("primary_goal")
            if (
                fp_primary_goal is not None
                and fp_primary_goal not in _VALID_PRIMARY_GOAL
            ):
                raise ApiError(
                    "invalid_primary_goal",
                    f"primary_goal must be one of: {sorted(_VALID_PRIMARY_GOAL)}",
                    status=400,
                )
            fp_condition_tolerance = fishing_profile.get("condition_tolerance")
            if (
                fp_condition_tolerance is not None
                and fp_condition_tolerance not in _VALID_CONDITION_TOLERANCE
            ):
                raise ApiError(
                    "invalid_condition_tolerance",
                    f"condition_tolerance must be one of: {sorted(_VALID_CONDITION_TOLERANCE)}",
                    status=400,
                )
            fp_tide_preference = fishing_profile.get("tide_preference")
            if (
                fp_tide_preference is not None
                and fp_tide_preference not in _VALID_TIDE_PREFERENCE
            ):
                raise ApiError(
                    "invalid_tide_preference",
                    f"tide_preference must be one of: {sorted(_VALID_TIDE_PREFERENCE)}",
                    status=400,
                )
            fp_session_frequency = fishing_profile.get("session_frequency")
            if (
                fp_session_frequency is not None
                and fp_session_frequency not in _VALID_SESSION_FREQUENCY
            ):
                raise ApiError(
                    "invalid_session_frequency",
                    f"session_frequency must be one of: {sorted(_VALID_SESSION_FREQUENCY)}",
                    status=400,
                )
            fp_catch_release = fishing_profile.get("catch_release")
            if (
                fp_catch_release is not None
                and fp_catch_release not in _VALID_CATCH_RELEASE
            ):
                raise ApiError(
                    "invalid_catch_release",
                    f"catch_release must be one of: {sorted(_VALID_CATCH_RELEASE)}",
                    status=400,
                )
            # Personal go/no-go comfort thresholds (optional numeric limits).
            for thresh_field, hi in (("max_wind_kt", 60), ("max_wave_ft", 30)):
                tval = fishing_profile.get(thresh_field)
                if tval is None:
                    continue
                if isinstance(tval, bool) or not isinstance(tval, (int, float)):
                    raise ApiError(
                        f"invalid_{thresh_field}",
                        f"{thresh_field} must be a number between 0 and {hi}",
                        status=400,
                    )
                if not (0 <= tval <= hi):
                    raise ApiError(
                        f"invalid_{thresh_field}",
                        f"{thresh_field} must be between 0 and {hi}",
                        status=400,
                    )

        notification_prefs = data.get("notification_prefs")
        if notification_prefs is not None:
            notification_prefs = _validate_notification_prefs(notification_prefs)

        location_id = data.get("location_id")
        if location_id is not None:
            if not isinstance(location_id, str):
                raise ApiError(
                    "invalid_location_id", "location_id must be a string", status=400
                )
            if len(location_id) > _MAX_LOCATION_ID_LEN:
                raise ApiError(
                    "invalid_location_id",
                    f"location_id must be {_MAX_LOCATION_ID_LEN} characters or fewer",
                    status=400,
                )

        return cls(
            location_id=location_id,
            theme=theme,
            units=units,
            fishing_profile=fishing_profile,
            favorites=favorites,
            notification_prefs=notification_prefs,
        )

    def as_updates(self) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        for k in (
            "location_id",
            "theme",
            "units",
            "fishing_profile",
            "favorites",
            "notification_prefs",
        ):
            v = getattr(self, k)
            if v is not None:
                updates[k] = v
        return updates


@dataclass
class LogCreatePayload:
    species: str
    size: str = ""
    notes: str = ""
    bait: str = ""
    location_id: str = ""

    @classmethod
    def from_json(
        cls, data: dict[str, Any], location_id: str = ""
    ) -> "LogCreatePayload":
        if not isinstance(data, dict):
            raise ApiError(
                "invalid_payload", "Request body must be a JSON object", status=400
            )
        species = str(data.get("species", "")).strip()
        if not species:
            raise ApiError("missing_species", "species is required", status=400)
        if len(species) > _MAX_SPECIES_LEN:
            raise ApiError(
                "invalid_species", f"species must be {_MAX_SPECIES_LEN} characters or fewer", status=400
            )
        size = str(data.get("size", "")).strip()
        if len(size) > _MAX_SIZE_LEN:
            raise ApiError(
                "invalid_size", f"size must be {_MAX_SIZE_LEN} characters or fewer", status=400
            )
        notes = str(data.get("notes", "")).strip()
        if len(notes) > _MAX_NOTES_LEN:
            raise ApiError(
                "invalid_notes", f"notes must be {_MAX_NOTES_LEN} characters or fewer", status=400
            )
        bait = str(data.get("bait", "")).strip()
        if len(bait) > _MAX_BAIT_LEN:
            raise ApiError(
                "invalid_bait", f"bait must be {_MAX_BAIT_LEN} characters or fewer", status=400
            )
        loc = str(data.get("location_id", "")).strip() or location_id
        if not loc:
            raise ApiError("missing_location", "location_id is required", status=400)
        return cls(species=species, size=size, notes=notes, bait=bait, location_id=loc)


def normalize_log_stats(stats: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "total": 0,
        "unique_species": 0,
        "top_species": None,
        "last_date": None,
        "species_breakdown": [],
        "monthly_counts": {},
    }
    base.update(stats or {})
    return base


def normalize_preferences(prefs: dict[str, Any]) -> dict[str, Any]:
    return {
        "location_id": prefs.get("location_id"),
        "theme": prefs.get("theme", "light"),
        "units": prefs.get("units", "F"),
        "fishing_profile": prefs.get("fishing_profile"),
        "favorites": prefs.get("favorites", []),
        "notification_prefs": prefs.get("notification_prefs", {}),
    }
