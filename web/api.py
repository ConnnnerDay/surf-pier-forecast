"""JSON API routes: preferences, fishing log, forecast data, sharing."""

from __future__ import annotations

import concurrent.futures as _cf
import datetime
import json as _json_mod
import logging
import os
import re as _re
import threading
import time
import uuid
from zoneinfo import available_timezones
from typing import Any, Optional

import requests as _requests

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)

from domain.forecast import (
    build_share_text,
    generate_forecast,
)
from services.forecast_refresh import enqueue_forecast_refresh, is_refreshing
from locations import COASTAL_LOCATIONS, get_location, get_water_temp
from storage.reg_scraper import invalidate_cache as _reg_invalidate_cache
from storage.species_loader import SPECIES_DB
from regulations import lookup_regulation
from storage.cache import (
    CACHE_MAX_AGE_HOURS,
    _forecast_age_minutes,
    load_cached_forecast,
    save_forecast,
)
from storage.sqlite import (
    add_log_entry,
    attach_photos_to_entry,
    delete_log_entry,
    get_entry_photo_paths,
    get_log_entries,
    get_log_stats,
    get_page_layout,
    get_preferences,
    save_page_layout,
    save_preferences,
)
from web.auth import record_refresh_attempt, refresh_is_rate_limited
from web.helpers import get_session_location
from web.openapi import build_openapi_spec
from web.schemas import (
    ApiError,
    ForecastQuery,
    LogCreatePayload,
    ProfilePayload,
    error_envelope,
    normalize_log_stats,
    normalize_preferences,
    success_envelope,
)

logger = logging.getLogger(__name__)

bp = Blueprint("api", __name__)

# ── Timezone validation ───────────────────────────────────────────────────────
# Build the allowed set once at import time from the system's zoneinfo database.
# This is the authoritative list of valid IANA timezone names — anything not in
# this set is rejected outright.
_VALID_TIMEZONES: frozenset[str] = frozenset(available_timezones())

# ── Rate limiting for /api/v1/timezone ───────────────────────────────────────
# 5 updates per IP per hour.  Timezone detection is a one-shot per-session
# event; anything beyond that is suspicious.
_TZ_RATE_LIMIT_MAX = 5
_TZ_RATE_LIMIT_WINDOW_S = 60 * 60  # 1 hour
_tz_rate_store: dict[str, tuple[float, int]] = {}
_tz_rate_lock = threading.Lock()


from web.rate_limit import (
    client_ip as _client_ip,
    is_rate_limited as _is_rate_limited_ip,
    record_attempt as _record_ip_attempt,
)


def _tz_is_rate_limited() -> bool:
    return _is_rate_limited_ip(
        _tz_rate_store, _tz_rate_lock, _TZ_RATE_LIMIT_MAX, _TZ_RATE_LIMIT_WINDOW_S
    )


def _tz_record_attempt() -> None:
    _record_ip_attempt(_tz_rate_store, _tz_rate_lock, _TZ_RATE_LIMIT_WINDOW_S)


# ── Rate limiting for /api/v1/regulations ────────────────────────────────────
# The regulations endpoint can trigger external web-scraping, making it
# relatively expensive.  Limit to 30 lookups per IP per hour to prevent
# abuse without affecting legitimate usage.
_REG_RATE_LIMIT_MAX = 30
_REG_RATE_LIMIT_WINDOW_S = 60 * 60  # 1 hour
_reg_rate_store: dict[str, tuple[float, int]] = {}
_reg_rate_lock = threading.Lock()


def _reg_is_rate_limited() -> bool:
    return _is_rate_limited_ip(
        _reg_rate_store, _reg_rate_lock, _REG_RATE_LIMIT_MAX, _REG_RATE_LIMIT_WINDOW_S
    )


def _reg_record_attempt() -> None:
    _record_ip_attempt(_reg_rate_store, _reg_rate_lock, _REG_RATE_LIMIT_WINDOW_S)


# ── Rate limiting for /api/v1/regulations/refresh ─────────────────────────────
# Cache invalidation forces re-scraping on the next lookup.  Any authenticated
# user can call this, so limit to 5 invalidations per IP per hour.
_REG_REFRESH_RATE_LIMIT_MAX = 5
_REG_REFRESH_RATE_LIMIT_WINDOW_S = 60 * 60  # 1 hour
_reg_refresh_rate_store: dict[str, tuple[float, int]] = {}
_reg_refresh_rate_lock = threading.Lock()


def _reg_refresh_is_rate_limited() -> bool:
    return _is_rate_limited_ip(
        _reg_refresh_rate_store,
        _reg_refresh_rate_lock,
        _REG_REFRESH_RATE_LIMIT_MAX,
        _REG_REFRESH_RATE_LIMIT_WINDOW_S,
    )


def _reg_refresh_record_attempt() -> None:
    _record_ip_attempt(
        _reg_refresh_rate_store,
        _reg_refresh_rate_lock,
        _REG_REFRESH_RATE_LIMIT_WINDOW_S,
    )


# ── Rate limiting for forecast sub-endpoints (status/outlook/solunar) ─────────
# These serve cached data only, but are polled frequently by the dashboard.
# Limit to 120 requests per IP per minute (2/sec on average) to prevent
# deliberate polling storms.
_FORECAST_SUB_RATE_LIMIT_MAX = 120
_FORECAST_SUB_RATE_LIMIT_WINDOW_S = 60  # 1 minute
_forecast_sub_rate_store: dict[str, tuple[float, int]] = {}
_forecast_sub_rate_lock = threading.Lock()


def _forecast_sub_is_rate_limited() -> bool:
    return _is_rate_limited_ip(
        _forecast_sub_rate_store,
        _forecast_sub_rate_lock,
        _FORECAST_SUB_RATE_LIMIT_MAX,
        _FORECAST_SUB_RATE_LIMIT_WINDOW_S,
    )


def _forecast_sub_record_attempt() -> None:
    _record_ip_attempt(
        _forecast_sub_rate_store,
        _forecast_sub_rate_lock,
        _FORECAST_SUB_RATE_LIMIT_WINDOW_S,
    )


# ── Rate limiting for photo uploads ───────────────────────────────────────────
# Each upload writes up to 8 MB to disk.  Limit authenticated users to 20
# uploads per 10 minutes per IP to prevent disk-filling abuse.
_UPLOAD_RATE_LIMIT_MAX = 20
_UPLOAD_RATE_LIMIT_WINDOW_S = 10 * 60
_upload_rate_store: dict[str, tuple[float, int]] = {}
_upload_rate_lock = threading.Lock()


def _upload_is_rate_limited() -> bool:
    return _is_rate_limited_ip(
        _upload_rate_store,
        _upload_rate_lock,
        _UPLOAD_RATE_LIMIT_MAX,
        _UPLOAD_RATE_LIMIT_WINDOW_S,
    )


def _upload_record_attempt() -> None:
    _record_ip_attempt(
        _upload_rate_store, _upload_rate_lock, _UPLOAD_RATE_LIMIT_WINDOW_S
    )


def _json_error(err: ApiError) -> Any:
    return jsonify(
        error_envelope(err.code, err.message, details=err.details)
    ), err.status


@bp.route("/api/v1/timezone", methods=["POST"])
def set_timezone() -> Any:
    """Store the client-detected IANA timezone in the user's profile.

    Called once per session by the nav script. No-op for anonymous users.

    Security:
    - Requires authentication (anonymous requests are silently ignored)
    - Rate-limited: 5 updates per IP per hour
    - Timezone validated against the authoritative zoneinfo database — rejects
      anything that isn't a real IANA zone name (including path-like strings)
    """
    if not g.user:
        return jsonify({"ok": True})

    if _tz_is_rate_limited():
        logger.warning(
            "security.timezone_rate_limit user_id=%s ip=%s",
            g.user["id"],
            _client_ip(),
        )
        return jsonify(
            {"ok": True}
        )  # silent — no need to reveal rate limiting to client

    _tz_record_attempt()

    data = request.get_json(silent=True) or {}
    tz = str(data.get("timezone", "")).strip()

    if tz not in _VALID_TIMEZONES:
        # Log suspicious input (anything outside the zoneinfo whitelist).
        if tz:
            logger.warning(
                "security.invalid_timezone user_id=%s tz=%r ip=%s",
                g.user["id"],
                tz[:80],
                _client_ip(),
            )
        return jsonify({"ok": True})  # silent rejection — no error info to caller

    save_preferences(g.user["id"], timezone=tz)
    logger.info("timezone.saved user_id=%s tz=%s", g.user["id"], tz)
    return jsonify({"ok": True})


def _v1_forecast_payload(query: ForecastQuery) -> dict[str, Any]:
    location = (
        get_location(query.location_id) if query.location_id else get_session_location()
    )
    if not location:
        raise ApiError("location_not_found", "No valid location selected", status=404)

    loc_id = location["id"]
    user_id = g.user["id"] if g.user else None
    forecast_data: Optional[dict[str, Any]] = None
    if query.force_refresh:
        if refresh_is_rate_limited():
            raise ApiError(
                "rate_limited",
                "Too many forecast refreshes. Please wait a few minutes.",
                status=429,
            )
        record_refresh_attempt()
        logger.info("cache.force_refresh location_id=%s", loc_id)
        forecast_data = generate_forecast(location)
        save_forecast(forecast_data, loc_id, user_id=user_id)
        logger.info("cache.regenerated location_id=%s", loc_id)
    else:
        forecast_data = load_cached_forecast(
            loc_id, user_id=user_id, include_stale=True
        )
        if forecast_data:
            age = _forecast_age_minutes(forecast_data)
            if age is not None and age > CACHE_MAX_AGE_HOURS * 60:
                logger.info("cache.stale_served location_id=%s", loc_id)
                enqueue_forecast_refresh(loc_id, user_id=user_id)
            else:
                logger.info("cache.hit location_id=%s", loc_id)
        else:
            logger.info("cache.miss location_id=%s", loc_id)
            forecast_data = generate_forecast(location)
            save_forecast(forecast_data, loc_id, user_id=user_id)
            logger.info("cache.regenerated location_id=%s", loc_id)

    if not forecast_data:
        raise ApiError("forecast_unavailable", "No forecast available", status=503)

    return {
        "location_id": loc_id,
        "force_refresh": query.force_refresh,
        "is_refreshing": is_refreshing(loc_id, user_id=user_id),
        "forecast": forecast_data,
    }


@bp.route("/api/openapi.json", methods=["GET"])
@bp.route("/api/v1/openapi.json", methods=["GET"])
def openapi_spec() -> Any:
    return jsonify(build_openapi_spec())


_DEPRECATION_HEADER = (
    "true"  # RFC 8594 §3 — value "true" marks as deprecated without a date
)


@bp.route("/api/preferences", methods=["GET", "POST"])
def preferences() -> Any:
    """Legacy profile endpoint — superseded by /api/v1/profile."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    uid = g.user["id"]
    if request.method == "GET":
        resp = jsonify(get_preferences(uid))
        resp.headers["Deprecation"] = _DEPRECATION_HEADER
        resp.headers["Link"] = '</api/v1/profile>; rel="successor-version"'
        return resp

    data = request.get_json(silent=True) or {}
    try:
        payload = ProfilePayload.from_json(data)
    except ApiError as err:
        return jsonify({"error": err.message}), err.status

    updates = payload.as_updates()
    if updates:
        save_preferences(uid, **updates)
        if "location_id" in updates and updates["location_id"]:
            session["location_id"] = updates["location_id"]
    resp = jsonify({"ok": True})
    resp.headers["Deprecation"] = _DEPRECATION_HEADER
    resp.headers["Link"] = '</api/v1/profile>; rel="successor-version"'
    return resp


@bp.route("/api/v1/profile", methods=["GET", "POST"])
def profile_v1() -> Any:
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401

    uid = g.user["id"]
    if request.method == "GET":
        prefs = normalize_preferences(get_preferences(uid))
        return jsonify(success_envelope({"profile": prefs}))

    data = request.get_json(silent=True) or {}
    try:
        payload = ProfilePayload.from_json(data)
    except ApiError as err:
        return _json_error(err)

    updates = payload.as_updates()
    if updates:
        save_preferences(uid, **updates)
        if "location_id" in updates and updates["location_id"]:
            session["location_id"] = updates["location_id"]

    prefs = normalize_preferences(get_preferences(uid))
    return jsonify(success_envelope({"profile": prefs}))


@bp.route("/api/v1/page-layout", methods=["GET", "POST"])
def page_layout_v1() -> Any:
    """Get or save the user's custom page section layout."""
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401

    uid = g.user["id"]
    if request.method == "GET":
        layout = get_page_layout(uid)
        return jsonify(success_envelope({"layout": layout}))

    data = request.get_json(silent=True) or {}
    layout = data.get("layout")
    if not isinstance(layout, list):
        return _json_error(
            ApiError("invalid_param", "layout must be an array", status=400)
        )
    if len(layout) > 25:
        return _json_error(ApiError("invalid_param", "layout too large", status=400))
    for item in layout:
        if not isinstance(item, dict) or "id" not in item:
            return _json_error(
                ApiError(
                    "invalid_param", "each layout item must have an id", status=400
                )
            )
    save_page_layout(uid, layout)
    return jsonify(success_envelope({"ok": True}))


@bp.route("/api/log", methods=["GET", "POST"])
def log() -> Any:
    """Legacy log endpoint — superseded by /api/v1/log."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    uid = g.user["id"]
    loc_id = request.args.get("location") or session.get("location_id", "")
    if request.method == "GET":
        entries = get_log_entries(uid, loc_id)
        stats = get_log_stats(uid, loc_id) if loc_id else {}
        resp = jsonify({"entries": entries, "stats": stats})
        resp.headers["Deprecation"] = _DEPRECATION_HEADER
        resp.headers["Link"] = '</api/v1/log>; rel="successor-version"'
        return resp
    data = request.get_json(silent=True) or {}
    try:
        payload = LogCreatePayload.from_json(data, loc_id)
    except ApiError as err:
        return jsonify({"error": err.message}), err.status
    entry_id = add_log_entry(
        uid,
        payload.location_id,
        payload.species,
        size=payload.size,
        notes=payload.notes,
    )
    resp = jsonify({"ok": True, "id": entry_id})
    resp.headers["Deprecation"] = _DEPRECATION_HEADER
    resp.headers["Link"] = '</api/v1/log>; rel="successor-version"'
    return resp, 201


@bp.route("/api/v1/log", methods=["GET", "POST"])
def log_v1() -> Any:
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401

    uid = g.user["id"]
    loc_id = (
        request.args.get("location_id")
        or request.args.get("location")
        or session.get("location_id")
        or ""
    ).strip()

    if request.method == "GET":
        entries = get_log_entries(uid, loc_id) if loc_id else []
        stats = normalize_log_stats(get_log_stats(uid, loc_id) if loc_id else {})
        return jsonify(
            success_envelope(
                {"location_id": loc_id or None, "entries": entries, "stats": stats}
            )
        )

    data = request.get_json(silent=True) or {}
    try:
        payload = LogCreatePayload.from_json(data, loc_id)
    except ApiError as err:
        return _json_error(err)

    entry_id = add_log_entry(
        uid,
        payload.location_id,
        payload.species,
        size=payload.size,
        notes=payload.notes,
    )
    created = {
        "id": entry_id,
        "species": payload.species,
        "size": payload.size,
        "notes": payload.notes,
        "location_id": payload.location_id,
    }
    return jsonify(success_envelope({"entry": created})), 201


@bp.route("/api/log/<int:entry_id>", methods=["DELETE"])
def log_delete(entry_id: int) -> Any:
    """Delete a fishing log entry."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    deleted = delete_log_entry(g.user["id"], entry_id)
    if not deleted:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@bp.route("/api/v1/log/<int:entry_id>", methods=["DELETE"])
def log_delete_v1(entry_id: int) -> Any:
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401
    uid = g.user["id"]
    photo_paths = get_entry_photo_paths(uid, entry_id)
    if photo_paths is None:
        # Entry doesn't exist (get_entry_photo_paths returns None for missing rows)
        return jsonify(error_envelope("not_found", "Log entry not found")), 404
    # Delete files before the DB row so a crash between the two doesn't leave
    # orphaned files on disk with no DB record to clean them up later.
    _delete_upload_file(photo_paths[0])
    _delete_upload_file(photo_paths[1])
    delete_log_entry(uid, entry_id)
    return jsonify(success_envelope({"deleted": True, "entry_id": entry_id}))


@bp.route("/api/forecast")
def forecast() -> Any:
    """Legacy forecast endpoint — superseded by /api/v1/forecast."""
    session_loc = get_session_location()
    fallback = session_loc["id"] if session_loc else ""
    query = ForecastQuery.from_request(request.args, fallback_location_id=fallback)

    try:
        payload = _v1_forecast_payload(query)
    except ApiError as err:
        # Keep legacy semantics for historical clients.
        if err.code == "location_not_found":
            return jsonify({"error": "No forecast available"}), 503
        return jsonify({"error": err.message}), err.status

    # Keep legacy shape: return raw forecast document
    resp = jsonify(payload["forecast"])
    resp.headers["Deprecation"] = _DEPRECATION_HEADER
    resp.headers["Link"] = '</api/v1/forecast>; rel="successor-version"'
    return resp


@bp.route("/api/v1/forecast", methods=["GET"])
def forecast_v1() -> Any:
    session_loc = get_session_location()
    fallback = session_loc["id"] if session_loc else ""
    query = ForecastQuery.from_request(request.args, fallback_location_id=fallback)

    try:
        payload = _v1_forecast_payload(query)
    except ApiError as err:
        return _json_error(err)

    return jsonify(success_envelope(payload))


@bp.route("/api/v1/forecast/<location_id>/status", methods=["GET"])
def forecast_status_v1(location_id: str) -> Any:
    """Return cache status for dashboard polling."""
    if _forecast_sub_is_rate_limited():
        return _json_error(ApiError("rate_limited", "Too many requests", status=429))
    _forecast_sub_record_attempt()
    forecast_data = load_cached_forecast(location_id, user_id=None, include_stale=True)
    if not forecast_data:
        return jsonify(
            success_envelope(
                {
                    "location_id": location_id,
                    "last_generated_at": None,
                    "is_stale": True,
                    "is_refreshing": is_refreshing(location_id, user_id=None),
                }
            )
        )

    age = _forecast_age_minutes(forecast_data)
    is_stale = bool(age is not None and age > CACHE_MAX_AGE_HOURS * 60)
    return jsonify(
        success_envelope(
            {
                "location_id": location_id,
                "last_generated_at": forecast_data.get("generated_at"),
                "is_stale": is_stale,
                "is_refreshing": is_refreshing(location_id, user_id=None),
            }
        )
    )


@bp.route("/api/v1/forecast/<location_id>/outlook", methods=["GET"])
def forecast_outlook_v1(location_id: str) -> Any:
    """Return cached 3-day outlook payload for lazy dashboard hydration."""
    if _forecast_sub_is_rate_limited():
        return _json_error(ApiError("rate_limited", "Too many requests", status=429))
    _forecast_sub_record_attempt()
    user_id = g.user["id"] if g.user else None
    # load_cached_forecast already falls back to the anonymous (user_id=0)
    # pre-warmed cache internally; no second call needed.
    forecast_data = load_cached_forecast(location_id, user_id=user_id)
    if not forecast_data:
        return _json_error(
            ApiError("forecast_not_cached", "No cached forecast available", status=404)
        )

    return jsonify(
        success_envelope(
            {
                "location_id": location_id,
                "outlook": forecast_data.get("outlook") or [],
                "best_day": forecast_data.get("best_day"),
                "activity_timeline": forecast_data.get("activity_timeline") or [],
            }
        )
    )


@bp.route("/api/v1/forecast/<location_id>/solunar", methods=["GET"])
def forecast_solunar_v1(location_id: str) -> Any:
    """Return cached solunar payload for lazy dashboard hydration."""
    if _forecast_sub_is_rate_limited():
        return _json_error(ApiError("rate_limited", "Too many requests", status=429))
    _forecast_sub_record_attempt()
    user_id = g.user["id"] if g.user else None
    forecast_data = load_cached_forecast(location_id, user_id=user_id)
    if not forecast_data:
        return _json_error(
            ApiError("forecast_not_cached", "No cached forecast available", status=404)
        )

    return jsonify(
        success_envelope(
            {
                "location_id": location_id,
                "solunar": forecast_data.get("solunar") or {},
            }
        )
    )


@bp.route("/api/refresh", methods=["POST"])
def refresh() -> Any:
    """Legacy forecast refresh — superseded by POST /api/v1/forecast?force_refresh=1."""
    location = get_session_location()
    if location is None:
        return redirect(url_for("views.setup"))
    if refresh_is_rate_limited():
        return redirect(url_for("views.index"))
    record_refresh_attempt()
    enqueue_forecast_refresh(location["id"], user_id=None)
    resp = redirect(url_for("views.index", cached="refreshing"))
    resp.headers["Deprecation"] = _DEPRECATION_HEADER
    resp.headers["Link"] = '</api/v1/forecast>; rel="successor-version"'
    return resp


@bp.route("/api/v1/regulations/refresh", methods=["POST"])
def regulations_refresh_v1() -> Any:
    """Invalidate the live-scrape regulation cache.

    Requires an authenticated session.  Optionally filter to a single state
    via the ``state`` query param.  The next regulation lookup for affected
    entries will re-scrape the official state agency website.
    """
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401

    if _reg_refresh_is_rate_limited():
        logger.warning("security.reg_refresh_rate_limit ip=%s", _client_ip())
        return _json_error(ApiError("rate_limited", "Too many requests", status=429))
    _reg_refresh_record_attempt()

    _VALID_STATES = frozenset(
        {
            "AL",
            "AK",
            "AZ",
            "AR",
            "CA",
            "CO",
            "CT",
            "DE",
            "FL",
            "GA",
            "HI",
            "ID",
            "IL",
            "IN",
            "IA",
            "KS",
            "KY",
            "LA",
            "ME",
            "MD",
            "MA",
            "MI",
            "MN",
            "MS",
            "MO",
            "MT",
            "NE",
            "NV",
            "NH",
            "NJ",
            "NM",
            "NY",
            "NC",
            "ND",
            "OH",
            "OK",
            "OR",
            "PA",
            "RI",
            "SC",
            "SD",
            "TN",
            "TX",
            "UT",
            "VT",
            "VA",
            "WA",
            "WV",
            "WI",
            "WY",
        }
    )
    state_raw = request.args.get("state", "").strip().upper() or None
    if state_raw and state_raw not in _VALID_STATES:
        return jsonify(
            error_envelope("invalid_state", f"Unknown state code: {state_raw}")
        ), 400
    state = state_raw
    try:
        removed = _reg_invalidate_cache(state)
    except Exception:
        logger.exception("regulations_refresh failed state=%s", state)
        removed = 0
    return jsonify(success_envelope({"invalidated": removed, "state": state}))


@bp.route("/api/v1/regulations", methods=["GET"])
def regulations_v1() -> Any:
    """Return fishing regulations for a species at a given location or state.

    Query parameters
    ----------------
    species      : str (required) — full species name, e.g. "Red drum (puppy drum)"
    location_id  : str (optional) — location ID; state is derived automatically
    state        : str (optional) — two-letter state abbreviation (overrides location_id)

    Always returns HTTP 200.  ``regulation`` is ``null`` when no data is available.
    """
    if _reg_is_rate_limited():
        logger.warning("security.regulations_rate_limit ip=%s", _client_ip())
        return _json_error(ApiError("rate_limited", "Too many requests", status=429))
    _reg_record_attempt()
    species_name = request.args.get("species", "")[:200].strip()
    if not species_name:
        return _json_error(
            ApiError(
                "missing_param", "'species' query parameter is required", status=400
            )
        )

    # Resolve state: explicit param takes priority, else derive from location_id,
    # else fall back to the current session location.
    # Cap state to 2 chars; anything longer is invalid and rejected below.
    state = request.args.get("state", "")[:2].strip().upper()
    if not state:
        location_id = request.args.get("location_id", "")[:100].strip()
        loc = get_location(location_id) if location_id else None
        if not loc:
            loc = get_session_location()
        if loc:
            state = (loc.get("state") or "").upper()

    reg = lookup_regulation(species_name, state) if state else None

    return jsonify(
        success_envelope(
            {
                "species": species_name,
                "state": state or None,
                "regulation": reg,
            }
        )
    )


_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB per photo

# Magic byte signatures for each allowed image format.
# Validated against the raw file content to prevent MIME-type spoofing — a
# client-controlled header that cannot be trusted on its own.
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_WEBP_RIFF = b"RIFF"
_WEBP_TAG = b"WEBP"


def _check_magic_bytes(data: bytes, claimed_mime: str) -> bool:
    """Return True if ``data`` starts with the magic bytes for ``claimed_mime``."""
    if claimed_mime == "image/jpeg":
        return data[:3] == _JPEG_MAGIC
    if claimed_mime == "image/png":
        return data[:8] == _PNG_MAGIC
    if claimed_mime == "image/webp":
        # WebP container: bytes 0-3 = "RIFF", bytes 8-11 = "WEBP"
        return len(data) >= 12 and data[:4] == _WEBP_RIFF and data[8:12] == _WEBP_TAG
    return False


def _save_upload(file_storage, user_id: int) -> tuple[str, str]:
    """Validate + write an uploaded photo.

    Returns ``(relative_path, absolute_path)`` where relative_path is suitable
    for storing in the DB and serving via ``/static/...``.

    Raises ApiError on validation failure.
    """
    mime = file_storage.mimetype or ""
    if mime not in _ALLOWED_MIME:
        raise ApiError(
            "invalid_file_type",
            "Unsupported file type. Use JPEG, PNG, or WebP.",
            status=400,
        )

    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    if ext not in _ALLOWED_EXT:
        raise ApiError(
            "invalid_file_type",
            "Unsupported extension. Use .jpg, .png, or .webp.",
            status=400,
        )

    data = file_storage.read()
    if len(data) > _MAX_PHOTO_BYTES:
        raise ApiError("file_too_large", "Photo must be 8 MB or smaller.", status=413)

    # Validate actual file content against known magic bytes so that a client
    # cannot bypass the MIME / extension checks by spoofing headers.
    if not _check_magic_bytes(data, mime):
        raise ApiError(
            "invalid_file_type",
            "File content does not match the declared type.",
            status=400,
        )

    upload_root = current_app.config["UPLOAD_FOLDER"]
    user_dir = os.path.join(upload_root, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    try:
        os.chmod(user_dir, 0o700)
    except OSError as _e:
        logger.warning("upload: chmod 700 failed for dir %s: %s", user_dir, _e)

    filename = f"{uuid.uuid4()}{ext}"
    abs_path = os.path.join(user_dir, filename)
    with open(abs_path, "wb") as fh:
        fh.write(data)
    try:
        os.chmod(abs_path, 0o600)
    except OSError as _e:
        logger.warning("upload: chmod 600 failed for file %s: %s", abs_path, _e)

    rel_path = f"uploads/{user_id}/{filename}"
    return rel_path, abs_path


def _delete_upload_file(rel_path: Optional[str]) -> None:
    """Silently remove a stored photo file; no-op when path is None or missing."""
    if not rel_path:
        return
    upload_root = current_app.config.get("UPLOAD_FOLDER", "")
    if not upload_root:
        return
    # rel_path is "uploads/<user_id>/<filename>"; strip the leading "uploads/" part
    sub = rel_path[len("uploads/") :] if rel_path.startswith("uploads/") else rel_path
    # Resolve symlinks and normalise to prevent path traversal (e.g. "../../etc")
    # before constructing the absolute path.
    upload_root_real = os.path.realpath(upload_root)
    abs_path = os.path.realpath(os.path.join(upload_root, sub))
    # Only delete files that are actually inside the upload root.
    if not abs_path.startswith(upload_root_real + os.sep):
        logger.warning(
            "Blocked attempt to delete file outside upload root: %s", rel_path
        )
        return
    try:
        os.remove(abs_path)
    except OSError:
        pass


@bp.route("/api/v1/log/<int:entry_id>/photos", methods=["POST"])
def log_photos_v1(entry_id: int) -> Any:
    """Attach up to two photos to an existing catch-log entry.

    Expects ``multipart/form-data`` with optional fields ``photo1`` and/or
    ``photo2`` (each a file upload).  At least one field must be present.
    """
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401

    if _upload_is_rate_limited():
        logger.warning(
            "security.upload_rate_limit user_id=%s ip=%s", g.user["id"], _client_ip()
        )
        return _json_error(
            ApiError("rate_limited", "Too many uploads. Please slow down.", status=429)
        )
    _upload_record_attempt()

    uid = g.user["id"]
    paths = get_entry_photo_paths(uid, entry_id)
    if paths is None:
        return jsonify(error_envelope("not_found", "Log entry not found")), 404

    photo1_file = request.files.get("photo1")
    photo2_file = request.files.get("photo2")

    if not photo1_file and not photo2_file:
        return _json_error(
            ApiError(
                "missing_param", "Provide at least one of: photo1, photo2", status=400
            )
        )

    saved: dict[str, str] = {}
    try:
        if photo1_file and photo1_file.filename:
            rel, _ = _save_upload(photo1_file, uid)
            saved["photo1_path"] = rel
        if photo2_file and photo2_file.filename:
            rel, _ = _save_upload(photo2_file, uid)
            saved["photo2_path"] = rel
    except ApiError as err:
        # Clean up any files already written this request
        for p in saved.values():
            _delete_upload_file(p)
        return _json_error(err)

    attach_photos_to_entry(uid, entry_id, **saved)
    # Delete old photo files that were just replaced so they don't become orphans.
    if "photo1_path" in saved:
        _delete_upload_file(paths[0])
    if "photo2_path" in saved:
        _delete_upload_file(paths[1])
    return jsonify(success_envelope({"entry_id": entry_id, **saved})), 201


@bp.route("/api/share-text")
def share_text() -> Any:
    """Return a plain-text forecast summary for copy/paste sharing."""
    location = get_session_location()
    loc_id = location["id"] if location else ""
    user_id = g.user["id"] if g.user else None
    forecast_data = load_cached_forecast(loc_id, user_id=user_id)
    if not forecast_data:
        return jsonify({"error": "No forecast available"}), 503
    text = build_share_text(forecast_data)
    return jsonify({"text": text, "location_id": loc_id})

