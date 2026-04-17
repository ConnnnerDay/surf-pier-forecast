"""JSON API routes: preferences, fishing log, forecast data, sharing."""

from __future__ import annotations

import json as _json_mod
import logging
import os
import threading
import time
import uuid
from zoneinfo import available_timezones
from typing import Any, Optional

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

from domain.forecast import build_share_text, generate_forecast
from services.forecast_refresh import enqueue_forecast_refresh, is_refreshing
from locations import get_location, get_water_temp
from regulations import lookup_regulation
from storage.cache import (
    CACHE_MAX_AGE_HOURS,
    _forecast_age_minutes,
    load_cached_forecast,
    save_forecast,
)
from services.fish_structures import VALID_TYPES, find_fish_structures
from storage.sqlite import (
    add_log_entry,
    add_map_catch,
    add_map_catch_comment,
    attach_photos_to_entry,
    delete_log_entry,
    delete_map_catch,
    get_community_hotspots,
    get_custom_markers,
    get_entry_photo_paths,
    get_log_entries,
    get_log_stats,
    get_map_catch,
    get_map_catch_comments,
    get_map_catches_in_bbox,
    get_page_layout,
    get_preferences,
    get_recent_public_catches,
    save_page_layout,
    save_preferences,
    toggle_map_catch_like,
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


_TRUST_PROXY = os.environ.get("TRUSTED_PROXY", "").strip() == "1"


def _client_ip() -> str:
    """Return the best-effort client IP for rate limiting.

    X-Forwarded-For is only honoured when TRUSTED_PROXY=1 is set in the
    environment.  Without that flag, the header is ignored to prevent clients
    from spoofing arbitrary IPs and bypassing IP-based rate limiting.
    """
    if _TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"




_PRUNE_EVERY = 200  # evict expired entries every N rate-limit checks
_prune_counter = 0


def _prune_rate_store(store: dict[str, tuple[float, int]], window_s: int) -> None:
    """Remove expired entries from a rate store.  Must be called under its lock."""
    now = time.time()
    expired = [ip for ip, (start, _) in store.items() if now - start > window_s]
    for ip in expired:
        del store[ip]


def _is_rate_limited_ip(
    store: dict[str, tuple[float, int]],
    lock: threading.Lock,
    max_attempts: int,
    window_s: int,
) -> bool:
    """Generic IP-keyed sliding-window rate limiter (read-only check)."""
    global _prune_counter
    ip = _client_ip()
    now = time.time()
    with lock:
        _prune_counter += 1
        if _prune_counter % _PRUNE_EVERY == 0:
            _prune_rate_store(store, window_s)
        start, count = store.get(ip, (now, 0))
        if now - start > window_s:
            return False
        return count >= max_attempts


def _record_ip_attempt(
    store: dict[str, tuple[float, int]],
    lock: threading.Lock,
    window_s: int,
) -> None:
    """Record one attempt in a generic IP-keyed sliding-window rate store."""
    ip = _client_ip()
    now = time.time()
    with lock:
        start, count = store.get(ip, (now, 0))
        if now - start > window_s:
            store[ip] = (now, 1)
        else:
            store[ip] = (start, count + 1)


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
        forecast_data = load_cached_forecast(loc_id, user_id=user_id)
        if forecast_data:
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
        "forecast": forecast_data,
    }


@bp.route("/api/openapi.json", methods=["GET"])
@bp.route("/api/v1/openapi.json", methods=["GET"])
def openapi_spec() -> Any:
    return jsonify(build_openapi_spec())


@bp.route("/api/preferences", methods=["GET", "POST"])
def preferences() -> Any:
    """Legacy profile endpoint (compatible shape)."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    uid = g.user["id"]
    if request.method == "GET":
        return jsonify(get_preferences(uid))

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
    return jsonify({"ok": True})


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
    """Legacy log endpoint (compatible shape)."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    uid = g.user["id"]
    loc_id = request.args.get("location") or session.get("location_id", "")
    if request.method == "GET":
        entries = get_log_entries(uid, loc_id)
        stats = get_log_stats(uid, loc_id) if loc_id else {}
        return jsonify({"entries": entries, "stats": stats})
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
    return jsonify({"ok": True, "id": entry_id}), 201


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
    """Legacy forecast endpoint with support for location_id + force_refresh."""
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
    return jsonify(payload["forecast"])


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
    forecast_data = load_cached_forecast(location_id, user_id=user_id)
    if not forecast_data and user_id is not None:
        # Dashboard renders from the shared cache namespace today; fall back so
        # authenticated users can still hydrate lazy sections.
        forecast_data = load_cached_forecast(location_id, user_id=None)
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
    if not forecast_data and user_id is not None:
        forecast_data = load_cached_forecast(location_id, user_id=None)
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
    """Queue generation of a new forecast and return immediately."""
    location = get_session_location()
    if location is None:
        return redirect(url_for("views.setup"))
    if refresh_is_rate_limited():
        return redirect(url_for("views.index"))
    record_refresh_attempt()
    enqueue_forecast_refresh(location["id"], user_id=None)
    return redirect(url_for("views.index", cached="refreshing"))


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

    state = request.args.get("state", "").strip().upper() or None
    try:
        from storage.reg_scraper import invalidate_cache

        removed = invalidate_cache(state)
    except Exception:
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
    except OSError:
        pass

    filename = f"{uuid.uuid4()}{ext}"
    abs_path = os.path.join(user_dir, filename)
    with open(abs_path, "wb") as fh:
        fh.write(data)
    try:
        os.chmod(abs_path, 0o600)
    except OSError:
        pass

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


# ---------------------------------------------------------------------------
# Region-to-coast helpers for the fishing map
# ---------------------------------------------------------------------------

# Maps species 'regions' values → sets of location temp_region strings.
_SPECIES_REGION_TO_LOC_REGIONS: dict[str, frozenset] = {
    "northeast": frozenset({"northeast"}),
    "new_england": frozenset({"northeast"}),
    "mid-atlantic": frozenset({"midatlantic"}),
    "midatlantic": frozenset({"midatlantic"}),
    "southeast": frozenset({"nc_outer_banks", "nc_south", "sc_ga"}),
    "florida": frozenset(
        {
            "fl_northeast",
            "fl_central_east",
            "fl_south",
            "fl_keys",
            "fl_gulf_north",
            "fl_gulf_south",
        }
    ),
    "gulf": frozenset({"gulf_central", "gulf_west", "fl_gulf_north", "fl_gulf_south"}),
    "pacific_nw": frozenset({"pacific_nw"}),
    "pacific northwest": frozenset({"pacific_nw"}),
    "pacific_northwest": frozenset({"pacific_nw"}),
    "norcal": frozenset({"pacific_norcal"}),
    "california": frozenset(
        {"pacific_norcal", "pacific_central_cal", "pacific_socal", "pacific_san_diego"}
    ),
    "socal": frozenset({"pacific_socal", "pacific_san_diego"}),
}


def _temp_factor(species: dict[str, Any], water_temp_f: float) -> float:
    """Return 0.3–1.0 temperature suitability multiplier for a species.

    1.0 = water is inside the ideal temperature window.
    Tapers to 0.5 at the survivable edges, 0.3 outside survivable range.
    """
    t_min = float(species.get("temp_min", 32))
    t_max = float(species.get("temp_max", 95))
    t_low = float(species.get("temp_ideal_low", t_min))
    t_high = float(species.get("temp_ideal_high", t_max))

    if water_temp_f < t_min or water_temp_f > t_max:
        return 0.3
    if t_low <= water_temp_f <= t_high:
        return 1.0
    if water_temp_f < t_low:
        span = t_low - t_min
        return 1.0 if span <= 0 else 0.5 + 0.5 * (water_temp_f - t_min) / span
    span = t_max - t_high
    return 1.0 if span <= 0 else 0.5 + 0.5 * (t_max - water_temp_f) / span


def _location_coast(loc: dict[str, Any]) -> str:
    """Return 'west', 'hawaii', or 'east' for a location."""
    region = loc.get("temp_region", "")
    if region.startswith("pacific_"):
        return "west"
    if region == "hawaii":
        return "hawaii"
    return "east"


def _species_present_at(species: dict[str, Any], loc: dict[str, Any]) -> bool:
    """Return True when a species is plausibly found at a given location.

    Uses the optional per-species ``regions`` list for fine-grained matching;
    falls back to coast-level matching when no regions are specified.
    """
    loc_coast = _location_coast(loc)
    if species["coast"] != loc_coast:
        return False
    regions = species.get("regions")
    if not regions:
        return True  # coast match is sufficient
    loc_region = loc.get("temp_region", "")
    for r in regions:
        if loc_region in _SPECIES_REGION_TO_LOC_REGIONS.get(r, frozenset()):
            return True
    return False


def _month_score(species: dict[str, Any], month: int) -> int:
    """Activity score 0-100 for a species in the given calendar month."""
    if month in species.get("peak_months", []):
        return 100
    if month in species.get("good_months", []):
        return 65
    return 20


# ── Species-names list (autocomplete) — pre-computed once ─────────────────────
# sorted({s["name"] for s in SPECIES_DB}) iterates 800+ species every cold
# request.  The list never changes at runtime so we compute it once and reuse.
_SPECIES_NAMES_CACHE: Optional[list] = None

# Pre-built lowercase name index: list of (lowercase_name, species_dict) pairs
# so species_q filtering avoids calling s["name"].lower() on every species on
# every request.
_SPECIES_LOWER_INDEX: Optional[list[tuple[str, frozenset, Any]]] = None

# ── Location → species index — pre-computed once at first warm request ─────────
# _species_present_at(sp, loc) is O(1) per call but it is called
# len(SPECIES_DB) × len(COASTAL_LOCATIONS) ≈ 895 × 117 ≈ 104,000 times on
# every cold cache miss with the default (no-filter) query.  Building this
# mapping once reduces the per-request cost to a single dict lookup for the
# unfiltered case, and to iterating a much smaller per-location list for
# filtered queries.
_LOC_SPECIES_ALL: Optional[dict[str, List]] = None  # loc_id → [species, ...]


def _get_loc_species_all() -> dict[str, List]:
    """Return {loc_id: [species_dict, ...]} for every location using all species."""
    global _LOC_SPECIES_ALL
    if _LOC_SPECIES_ALL is None:
        from storage.species_loader import SPECIES_DB
        from locations import COASTAL_LOCATIONS

        _LOC_SPECIES_ALL = {
            loc["id"]: [s for s in SPECIES_DB if _species_present_at(s, loc)]
            for loc in COASTAL_LOCATIONS
        }
    return _LOC_SPECIES_ALL


def _get_all_species_names() -> list:
    global _SPECIES_NAMES_CACHE
    if _SPECIES_NAMES_CACHE is None:
        from storage.species_loader import SPECIES_DB

        _SPECIES_NAMES_CACHE = sorted({s["name"] for s in SPECIES_DB})
    return _SPECIES_NAMES_CACHE


def _get_species_lower_index() -> "list[tuple[str, frozenset, Any]]":
    """Return list of (lowercase_name, lowercase_category_frozenset, species_dict).

    Built once at first call so neither .lower() nor list-comp over categories
    runs at filter time.
    """
    global _SPECIES_LOWER_INDEX
    if _SPECIES_LOWER_INDEX is None:
        from storage.species_loader import SPECIES_DB

        _SPECIES_LOWER_INDEX = [
            (
                s["name"].lower(),
                frozenset(c.lower() for c in s.get("categories", [])),
                s,
            )
            for s in SPECIES_DB
        ]
    return _SPECIES_LOWER_INDEX


# ── Fishing-map response cache ─────────────────────────────────────────────────
# The scoring loop iterates 100+ locations × 800+ species every request.  Cache
# the fully-built response dict for 5 minutes so rapid filter changes (species,
# coast, season…) that repeat a previous combination return instantly.
# Key: (species_q, coast_q, category_q, month, season_q, time_q, tide_q,
#        min_temp, max_temp)  — the complete set of params that affect output.
_FMAP_CACHE: dict[tuple, dict[str, Any]] = {}
_FMAP_CACHE_TTL: int = 900  # 15 minutes — scores only change when the month rolls over
_FMAP_CACHE_MAX: int = 128  # cap entries; each is ~50 KB serialised


def _fmap_cache_get(key: tuple) -> Optional[dict[str, Any]]:
    entry = _FMAP_CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _FMAP_CACHE_TTL:
        return entry["data"]
    return None


def _fmap_cache_set(key: tuple, data: dict[str, Any]) -> None:
    if len(_FMAP_CACHE) >= _FMAP_CACHE_MAX:
        # Drop the oldest insertion
        try:
            del _FMAP_CACHE[next(iter(_FMAP_CACHE))]
        except (KeyError, StopIteration):
            pass
    _FMAP_CACHE[key] = {"ts": time.time(), "data": data}


@bp.route("/api/fishing-map")
def fishing_map_data() -> Any:
    """Return location suitability data for the AI Fishing Map.

    Query params
    ------------
    species : str, optional
        Case-insensitive substring to filter target species.
    coast   : 'east' | 'west' | 'hawaii', optional
        Restrict to one coast.  Omit or pass 'all' for every location.
    category : str, optional
        Species category filter (e.g. 'shark', 'game_fish', 'reef_fish').
    month   : int 1-12, optional
        Override current month (for testing / future planning).
    """
    import datetime
    from storage.species_loader import SPECIES_DB
    from locations import COASTAL_LOCATIONS

    # -- parse & sanitise params -----------------------------------------------
    species_q = request.args.get("species", "").strip()[:100].lower()
    coast_q = request.args.get("coast", "").strip()[:20].lower()
    category_q = request.args.get("category", "").strip()[:50].lower()

    # Client sends has_species=1 after the first fetch to skip the 895-name list
    # (saves ~20 KB per response once the autocomplete is warm).
    has_species_q = request.args.get("has_species", "0") == "1"

    # New extended filters
    season_q = request.args.get("season", "").strip()[:20].lower()
    # valid values: spring | summer | fall | winter | ""
    time_q = request.args.get("time_of_day", "").strip()[:20].lower()
    # valid values: dawn | morning | midday | evening | night | ""
    tide_q = request.args.get("tide_phase", "").strip()[:20].lower()
    # valid values: incoming | outgoing | high | low | ""
    min_temp_q = request.args.get("min_water_temp")
    max_temp_q = request.args.get("max_water_temp")

    _min_temp: Optional[float] = None
    _max_temp: Optional[float] = None
    try:
        if min_temp_q is not None:
            _min_temp = float(min_temp_q)
        if max_temp_q is not None:
            _max_temp = float(max_temp_q)
    except (ValueError, TypeError):
        pass

    try:
        month = int(request.args.get("month", "0"))
        if not 1 <= month <= 12:
            raise ValueError
    except (ValueError, TypeError):
        month = datetime.date.today().month

    # Map season → months
    _SEASON_MONTHS = {
        "spring": [3, 4, 5],
        "summer": [6, 7, 8],
        "fall": [9, 10, 11],
        "winter": [12, 1, 2],
    }
    # When a season is supplied force the month selection to that season's
    # representative middle month (used for scoring), unless month was
    # explicitly provided by the caller.
    if season_q in _SEASON_MONTHS and not request.args.get("month"):
        season_months = _SEASON_MONTHS[season_q]
        month = season_months[1]  # middle of the season

    # Map time-of-day → preferred fishing "bonus" (used for scoring later)
    # Dawn/dusk/night species get a mild boost when those times are selected.
    _DAWN_DUSK_TIMES = {"dawn", "morning", "evening", "night"}

    # Tide phase is informational metadata returned in the response for the
    # frontend to display; the backend scores don't change by tide currently.
    include_tide_hint = tide_q in {"incoming", "outgoing", "high", "low"}

    # ── Cache check — return pre-built response dict if still fresh ───────────
    _fmap_key = (
        species_q,
        coast_q,
        category_q,
        month,
        season_q,
        time_q,
        tide_q,
        _min_temp,
        _max_temp,
    )
    _cached_response = _fmap_cache_get(_fmap_key)
    if _cached_response is not None:
        if has_species_q and _cached_response.get("species_names"):
            _hit_data = {**_cached_response, "species_names": []}
            resp = jsonify(_hit_data)
        else:
            resp = jsonify(_cached_response)
        resp.headers["Cache-Control"] = "public, max-age=900, stale-while-revalidate=60"
        resp.headers["X-Cache"] = "HIT"
        return resp

    # -- filter the species DB once -------------------------------------------
    # Use _get_species_lower_index() so .lower() is never called at filter time;
    # the lowercase name strings are pre-built once at first request.
    if species_q or category_q:
        filtered_species = [
            s
            for (lname, lcats, s) in _get_species_lower_index()
            if (not species_q or species_q in lname)
            and (not category_q or category_q in lcats)
        ]
    else:
        filtered_species = SPECIES_DB

    # -- pre-compute _month_score for the current month once per species -------
    # _month_score(sp, month) is a pure function of two values; calling it
    # repeatedly in the scoring loop and then again in the monthly summary adds
    # up to millions of redundant calls on cold requests.  Build two lookup
    # structures once:
    #   _cur_score[name]       → score for the current month (used in main loop)
    #   _all_scores[name][m]   → score for month m, 1-indexed (monthly summary)
    _cur_score: dict[str, int] = {}
    _all_scores: dict[str, list[int]] = {}
    for _sp in filtered_species:
        _sn = _sp["name"]
        _cur_score[_sn] = _month_score(_sp, month)
        _all_scores[_sn] = [0] + [_month_score(_sp, _m) for _m in range(1, 13)]

    # -- score every location -------------------------------------------------
    # Use _get_loc_species_all() to eliminate _species_present_at() calls at
    # request time.  The full loc→species map is built once at first warm
    # request (pre-warm thread covers this) and reused for every subsequent
    # request:
    #   • No filter  → use pre-built list directly (zero _species_present_at calls)
    #   • Filtered   → intersect pre-built list with filtered_names frozenset
    #                  (O(loc × species_at_loc) instead of O(loc × all_species))
    def _activity_label(sc: int) -> str:
        if sc >= 100:
            return "peak"
        if sc >= 65:
            return "good"
        if sc >= 30:
            return "fair"
        return "slow"

    _all_loc_sp = _get_loc_species_all()  # pre-built; O(1)
    _is_filtered = bool(species_q or category_q)
    if _is_filtered:
        _filtered_names: frozenset = frozenset(s["name"] for s in filtered_species)

    results = []
    _loc_sp_map: dict[str, list] = {}  # loc_id → [species_dict, ...]
    for loc in COASTAL_LOCATIONS:
        loc_coast = _location_coast(loc)

        if coast_q and coast_q not in ("all", "") and loc_coast != coast_q:
            continue

        # Species for this location — resolved from pre-built index; no
        # _species_present_at() calls at request time.
        _pre = _all_loc_sp.get(loc["id"], [])
        loc_species = (
            [s for s in _pre if s["name"] in _filtered_names] if _is_filtered else _pre
        )
        _loc_sp_map[loc["id"]] = loc_species

        if not loc_species:
            score = 0
            activity = "none"
            top_species: list = []
        else:
            scored = sorted(loc_species, key=lambda s: -_cur_score[s["name"]])
            best_score = _cur_score[scored[0]["name"]]
            score = best_score
            activity = _activity_label(score)

            # Build rich species objects for the detail drawer.
            # Include up to 6 species that are at least "fair" (score >= 30),
            # each carrying bait, rig, and their own activity label.
            rich: list = []
            for sp in scored[:10]:
                sp_score = _cur_score[sp["name"]]
                if sp_score < 30 and len(rich) >= 3:
                    break
                rich.append(
                    {
                        "name": sp["name"],
                        "bait": sp.get("bait", ""),
                        "rig": sp.get("rig", ""),
                        "lures": sp.get("lures", ""),
                        "activity": _activity_label(sp_score),
                        "peak_months": sp.get("peak_months", []),
                        "good_months": sp.get("good_months", []),
                        # Extra fields used by AI reasoning
                        "temp_ideal_low": sp.get("temp_ideal_low"),
                        "temp_ideal_high": sp.get("temp_ideal_high"),
                        "explanation_cold": sp.get("explanation_cold", "")[:160],
                        "explanation_warm": sp.get("explanation_warm", "")[:160],
                    }
                )
            top_species = rich[:6]

        # Water temperature and temperature-weighted AI score
        water_temp = get_water_temp(
            loc.get("temp_region", ""),
            month,
            loc.get("temp_offset", 0),
        )
        if water_temp is not None and loc_species and score > 0:
            best_sp = scored[0]  # already sorted by _cur_score above
            ai_score: float = _cur_score[best_sp["name"]] * _temp_factor(
                best_sp, water_temp
            )
        else:
            ai_score = float(score)

        # Water-temp range filter — skip locations outside requested range
        if _min_temp is not None and water_temp is not None and water_temp < _min_temp:
            continue
        if _max_temp is not None and water_temp is not None and water_temp > _max_temp:
            continue

        results.append(
            {
                "id": loc["id"],
                "name": loc["name"],
                "state": loc["state"],
                "lat": loc["lat"],
                "lng": loc["lng"],
                "coast": loc_coast,
                "ai_score": round(ai_score, 1),
                "water_temp": water_temp,
                "activity": activity,
                "top_species": top_species,
                # extended filter metadata echoed back
                "tide_hint": tide_q if include_tide_hint else None,
                "time_hint": time_q or None,
            }
        )

    # Autocomplete dropdown names — served from pre-computed cache
    species_names = _get_all_species_names()

    # Monthly activity summary — use pre-computed _all_scores[name][m] so
    # _month_score is never called here at all (it was already called once
    # per species above when building _all_scores).
    monthly_summary = []
    for m in range(1, 13):
        peak_c = good_c = fair_c = 0
        for loc in results:
            loc_sp = _loc_sp_map.get(loc["id"], [])
            if not loc_sp:
                continue
            best = max(_all_scores[s["name"]][m] for s in loc_sp)
            if best >= 100:
                peak_c += 1
            elif best >= 65:
                good_c += 1
            elif best >= 30:
                fair_c += 1
        monthly_summary.append(
            {"month": m, "peak": peak_c, "good": good_c, "fair": fair_c}
        )

    # Trending species: in peak season this month, ranked by number of active locations.
    # frozenset lookup is O(1) vs re-calling _species_present_at O(n) per check.
    trending_species: list = []
    if not species_q:
        _loc_sp_names = {
            lid: frozenset(s["name"] for s in sp_list)
            for lid, sp_list in _loc_sp_map.items()
        }
        peak_sp_counts: dict = {}
        for sp in filtered_species:
            if month not in sp.get("peak_months", []):
                continue
            cnt = sum(
                1
                for loc in results
                if loc["activity"] != "none"
                and sp["name"] in _loc_sp_names.get(loc["id"], frozenset())
            )
            if cnt > 0:
                peak_sp_counts[sp["name"]] = cnt
        trending_species = sorted(peak_sp_counts, key=lambda n: -peak_sp_counts[n])[:10]

    # When a species filter is active, return enough metadata for the JS to infer
    # habitat type and build a relevant Overpass query — without hardcoding species names.
    species_meta: dict = {}
    if species_q and filtered_species:
        sp0 = filtered_species[0]
        species_meta = {
            "name": sp0["name"],
            "bait": sp0.get("bait", ""),
            "rig": sp0.get("rig", ""),
            "lures": sp0.get("lures", ""),
            "coast": sp0.get("coast", ""),
        }

    _response_data = {
        "locations": results,
        "month": month,
        "season": season_q or None,
        "time_of_day": time_q or None,
        "tide_phase": tide_q or None,
        "species_filter": species_q,
        "species_names": species_names,
        "monthly_summary": monthly_summary,
        "trending_species": trending_species,
        "species_meta": species_meta,
    }
    _fmap_cache_set(_fmap_key, _response_data)

    if has_species_q:
        _response_data = {**_response_data, "species_names": []}
    resp = jsonify(_response_data)
    resp.headers["Cache-Control"] = "public, max-age=900, stale-while-revalidate=60"
    resp.headers["X-Cache"] = "MISS"
    return resp


# ── Structure spots (wrecks & reefs from NOAA ENC) ──────────────────────────

_STRUCTURE_CACHE: dict = {}  # {cache_key: {"ts": float, "data": list}}
_STRUCTURE_CACHE_TTL = 3600  # 1 hour — wrecks don't move
_STRUCTURE_CACHE_MAX = 128  # ~0.02° keys; cap so long-running servers don't leak

_NOAA_ENC_BASE = "https://encdirect.noaa.gov/arcgis/rest/services/encdirect"
_NOAA_ENC_TIMEOUT: tuple[float, float] = (3.05, 10)


def _fetch_noaa_structures(
    sw_lat: float, sw_lng: float, ne_lat: float, ne_lng: float
) -> list:
    """Fetch wrecks from NOAA ENC Direct within bbox. Results are cached for 1 h."""
    import requests as _req

    cache_key = (
        f"{round(sw_lat, 2)},{round(sw_lng, 2)},{round(ne_lat, 2)},{round(ne_lng, 2)}"
    )
    cached = _STRUCTURE_CACHE.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _STRUCTURE_CACHE_TTL:
        return cached["data"]

    if len(_STRUCTURE_CACHE) >= _STRUCTURE_CACHE_MAX:
        oldest = min(_STRUCTURE_CACHE, key=lambda k: _STRUCTURE_CACHE[k]["ts"])
        _STRUCTURE_CACHE.pop(oldest, None)

    geometry_json = _json_mod.dumps(
        {
            "xmin": sw_lng,
            "ymin": sw_lat,
            "xmax": ne_lng,
            "ymax": ne_lat,
            "spatialReference": {"wkid": 4326},
        }
    )

    base_params = {
        "f": "json",
        "geometry": geometry_json,
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "true",
        "resultRecordCount": "200",
    }

    features: list = []

    # Wrecks
    try:
        resp = _req.get(
            f"{_NOAA_ENC_BASE}/enc_wrecks/MapServer/0/query",
            params=dict(base_params, outFields="WRECKNM,VALSOU,CAUTION"),
            timeout=_NOAA_ENC_TIMEOUT,
            headers={"User-Agent": "SurfPierForecast/1.0"},
        )
        if resp.ok:
            for feat in resp.json().get("features", []):
                geom = feat.get("geometry") or {}
                attrs = feat.get("attributes") or {}
                if geom.get("x") is None or geom.get("y") is None:
                    continue
                name = (attrs.get("WRECKNM") or "").strip() or "Unknown Wreck"
                features.append(
                    {
                        "type": "wreck",
                        "name": name,
                        "lat": geom["y"],
                        "lng": geom["x"],
                        "depth_m": attrs.get("VALSOU"),
                    }
                )
    except Exception:
        pass

    _STRUCTURE_CACHE[cache_key] = {"ts": time.time(), "data": features}
    return features


@bp.route("/api/structure-spots")
def structure_spots() -> Any:
    """Return wrecks/obstructions from NOAA ENC within a map viewport bounding box.

    Query params: sw_lat, sw_lng, ne_lat, ne_lng  (decimal degrees)
    """
    try:
        sw_lat = float(request.args["sw_lat"])
        sw_lng = float(request.args["sw_lng"])
        ne_lat = float(request.args["ne_lat"])
        ne_lng = float(request.args["ne_lng"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "sw_lat, sw_lng, ne_lat, ne_lng required"}), 400

    if not (-90 <= sw_lat < ne_lat <= 90) or not (-180 <= sw_lng < ne_lng <= 180):
        return jsonify({"error": "Invalid bbox range"}), 400

    # Block oversized viewports — too many results and NOAA rate limits
    if (ne_lat - sw_lat) > 8 or (ne_lng - sw_lng) > 12:
        return jsonify({"features": [], "zoom_required": True})

    features = _fetch_noaa_structures(sw_lat, sw_lng, ne_lat, ne_lng)
    return jsonify({"features": features})


# ── Community map catch endpoints ─────────────────────────────────────────────


@bp.route("/api/map/catches", methods=["GET"])
def map_catches_list() -> Any:
    """Return public catch pins in a bounding box (+ viewer's own private ones).

    Query params
    ------------
    sw_lat, sw_lng, ne_lat, ne_lng : float  – viewport bounding box (required)
    species   : str  – optional case-insensitive species filter
    days_back : int  – how many days of history to include (default 90, max 365)
    """
    try:
        sw_lat = float(request.args["sw_lat"])
        sw_lng = float(request.args["sw_lng"])
        ne_lat = float(request.args["ne_lat"])
        ne_lng = float(request.args["ne_lng"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "sw_lat, sw_lng, ne_lat, ne_lng required"}), 400

    if not (-90 <= sw_lat < ne_lat <= 90) or not (-180 <= sw_lng < ne_lng <= 180):
        return jsonify({"error": "Invalid bbox"}), 400

    try:
        days_back = min(int(request.args.get("days_back", 90)), 365)
    except (ValueError, TypeError):
        days_back = 90

    species_filter = request.args.get("species", "").strip()[:80]
    viewer_user_id = g.user["id"] if g.get("user") else None

    catches = get_map_catches_in_bbox(
        sw_lat,
        sw_lng,
        ne_lat,
        ne_lng,
        viewer_user_id=viewer_user_id,
        species_filter=species_filter,
        days_back=days_back,
    )
    return jsonify({"catches": catches})


@bp.route("/api/map/catches", methods=["POST"])
def map_catches_create() -> Any:
    """Log a new catch pin on the map.  Requires authentication.

    JSON body
    ---------
    lat       : float   (required)
    lng       : float   (required)
    species   : str     (required)
    title     : str     optional catch title / headline
    bait      : str     bait or lure used
    weight_lb : float
    length_in : float
    notes     : str
    image_url : str     public https:// URL of a catch photo
    is_public : bool    (default true)
    caught_at : str     ISO-8601 datetime, defaults to server time
    """
    if not g.get("user"):
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}

    try:
        lat = float(data["lat"])
        lng = float(data["lng"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "lat and lng are required numbers"}), 400

    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({"error": "lat/lng out of range"}), 400

    species = str(data.get("species", "")).strip()[:100]
    if not species:
        return jsonify({"error": "species is required"}), 400

    title = str(data.get("title", "")).strip()[:120]
    bait = str(data.get("bait", "")).strip()[:80]
    notes = str(data.get("notes", "")).strip()[:500]
    is_public = bool(data.get("is_public", True))
    caught_at = str(data.get("caught_at", "")).strip()[:30] or None

    # Only accept https:// image URLs to prevent mixed-content and SSRF vectors
    raw_image_url = str(data.get("image_url", "")).strip()[:500]
    image_url = raw_image_url if raw_image_url.startswith("https://") else ""

    weight_lb = data.get("weight_lb")
    length_in = data.get("length_in")
    try:
        weight_lb = float(weight_lb) if weight_lb is not None else None
        length_in = float(length_in) if length_in is not None else None
    except (ValueError, TypeError):
        weight_lb = length_in = None

    catch_id = add_map_catch(
        g.user["id"],
        lat,
        lng,
        species,
        title=title,
        bait=bait,
        weight_lb=weight_lb,
        length_in=length_in,
        notes=notes,
        image_url=image_url,
        is_public=is_public,
        caught_at=caught_at,
    )
    return jsonify({"id": catch_id}), 201


@bp.route("/api/map/catches/<int:catch_id>", methods=["DELETE"])
def map_catches_delete(catch_id: int) -> Any:
    """Delete the caller's own catch pin."""
    if not g.get("user"):
        return jsonify({"error": "Authentication required"}), 401

    deleted = delete_map_catch(catch_id, g.user["id"])
    if not deleted:
        return jsonify({"error": "Not found or not your catch"}), 404
    return jsonify({"ok": True})


@bp.route("/api/map/catches/<int:catch_id>/like", methods=["POST"])
def map_catch_like(catch_id: int) -> Any:
    """Toggle a like on a community catch pin.  Requires auth."""
    if not g.get("user"):
        return jsonify({"error": "Authentication required"}), 401

    catch = get_map_catch(catch_id)
    if not catch or not catch["is_public"]:
        return jsonify({"error": "Catch not found"}), 404

    liked, likes_count = toggle_map_catch_like(catch_id, g.user["id"])
    return jsonify({"liked": liked, "likes_count": likes_count})


@bp.route("/api/map/catches/<int:catch_id>/comments", methods=["GET"])
def map_catch_comments_list(catch_id: int) -> Any:
    """Return all comments on a catch pin."""
    catch = get_map_catch(catch_id)
    if not catch:
        return jsonify({"error": "Catch not found"}), 404
    if not catch["is_public"]:
        if not g.get("user") or g.user["id"] != catch["user_id"]:
            return jsonify({"error": "Catch not found"}), 404

    comments = get_map_catch_comments(catch_id)
    return jsonify({"comments": comments})


@bp.route("/api/map/catches/<int:catch_id>/comments", methods=["POST"])
def map_catch_comments_create(catch_id: int) -> Any:
    """Add a comment to a catch pin.  Requires auth."""
    if not g.get("user"):
        return jsonify({"error": "Authentication required"}), 401

    catch = get_map_catch(catch_id)
    if not catch or not catch["is_public"]:
        return jsonify({"error": "Catch not found"}), 404

    data = request.get_json(silent=True) or {}
    body = str(data.get("body", "")).strip()[:500]
    if not body:
        return jsonify({"error": "body is required"}), 400

    comment_id = add_map_catch_comment(catch_id, g.user["id"], body)
    return jsonify({"id": comment_id}), 201


@bp.route("/api/map/feed", methods=["GET"])
def map_catch_feed() -> Any:
    """Recent public catches, optionally near a point.

    Query params
    ------------
    lat, lng : float  – anchor point for distance filter (optional)
    species  : str    – filter by species (optional)
    limit    : int    – max results (default 20, max 50)
    """
    lat = lng = None
    try:
        if "lat" in request.args and "lng" in request.args:
            lat = float(request.args["lat"])
            lng = float(request.args["lng"])
    except (ValueError, TypeError):
        lat = lng = None

    species_filter = request.args.get("species", "").strip()[:80]
    try:
        limit = min(int(request.args.get("limit", 20)), 50)
    except (ValueError, TypeError):
        limit = 20

    catches = get_recent_public_catches(
        limit=limit,
        species_filter=species_filter,
        lat=lat,
        lng=lng,
    )
    return jsonify({"catches": catches})


@bp.route("/api/map/hotspots", methods=["GET"])
def map_community_hotspots() -> Any:
    """Return community hotspot rankings based on recent catch activity.

    Query params
    ------------
    days_back : int  – look-back window (default 30, max 90)
    limit     : int  – max hotspots (default 10, max 25)
    """
    try:
        days_back = min(int(request.args.get("days_back", 30)), 90)
    except (ValueError, TypeError):
        days_back = 30
    try:
        limit = min(int(request.args.get("limit", 10)), 25)
    except (ValueError, TypeError):
        limit = 10

    hotspots = get_community_hotspots(days_back=days_back, limit=limit)
    return jsonify({"hotspots": hotspots, "days_back": days_back})


_STRUCT_MAX_LAT_SPAN = 8.0  # degrees — wider than this and Overpass times out
_STRUCT_MAX_LNG_SPAN = 12.0  # degrees — matches /api/structure-spots guard


@bp.route("/api/map/structures", methods=["GET"])
def map_structures() -> Any:
    """Return fish-holding structures within a bounding box.

    Queries OpenStreetMap (via Overpass API) and the NOAA ENC chart service,
    then returns a deduplicated list of structures with fishing tips.

    Query params
    ------------
    south : float  – southern latitude of the map view  (required)
    west  : float  – western longitude of the map view  (required)
    north : float  – northern latitude of the map view  (required)
    east  : float  – eastern longitude of the map view  (required)
    types : str    – comma-separated structure types to include (optional).
                     Defaults to all types.  See VALID_TYPES in
                     services/fish_structures.py for the complete list.

    Returns
    -------
    JSON: { "structures": [...], "count": <int> }

    Each structure object has: lat, lng, type, name, tip.
    """
    # ── Parse & validate bbox ─────────────────────────────────────────────────
    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError, TypeError):
        return jsonify(
            error_envelope(
                "invalid_params",
                "south, west, north, east query parameters are required floats",
            )
        ), 400

    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        return jsonify(
            error_envelope(
                "invalid_params",
                "Latitude values must be between -90 and 90",
            )
        ), 400
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        return jsonify(
            error_envelope(
                "invalid_params",
                "Longitude values must be between -180 and 180",
            )
        ), 400
    if south >= north:
        return jsonify(
            error_envelope(
                "invalid_params",
                "south must be less than north",
            )
        ), 400

    # ── Viewport size guard ───────────────────────────────────────────────────
    # Very large bboxes cause Overpass to time out and return too many features
    # to be useful.  Signal the client to zoom in rather than issuing the query.
    lng_span = east - west if east >= west else (east + 360 - west)
    if (north - south) > _STRUCT_MAX_LAT_SPAN or lng_span > _STRUCT_MAX_LNG_SPAN:
        return jsonify({"structures": [], "count": 0, "zoom_required": True})

    # ── Parse optional types filter ───────────────────────────────────────────
    active_types = None
    types_param = request.args.get("types", "").strip()
    if types_param:
        requested = {t.strip() for t in types_param.split(",") if t.strip()}
        active_types = requested & VALID_TYPES
        if not active_types:
            return jsonify(
                error_envelope(
                    "invalid_params",
                    f"No valid types supplied. Valid types: {sorted(VALID_TYPES)}",
                )
            ), 400

    structures = find_fish_structures(south, west, north, east, active_types)

    # Merge in admin-created custom markers that fall within the bbox.
    custom = [
        m
        for m in get_custom_markers()
        if south <= m["lat"] <= north
        and west <= m["lng"] <= east
        and (active_types is None or m["type"] in active_types)
    ]
    all_structures = structures + custom

    resp = jsonify({"structures": all_structures, "count": len(all_structures)})
    # Structure data (OSM/NOAA) and admin custom markers are user-agnostic, so
    # shared/browser caching is safe.  30-minute TTL matches the server-side
    # in-memory cache; stale-while-revalidate lets the browser serve a cached
    # response while a background refresh happens, keeping the UI snappy.
    resp.headers["Cache-Control"] = "public, max-age=1800, stale-while-revalidate=60"
    return resp


@bp.route("/api/map/marine-warnings", methods=["GET"])
def map_marine_warnings() -> Any:
    """Return active NWS watches/warnings intersecting the bounding box.

    Proxies the ArcGIS Living Atlas NWS_Watches_Warnings_v1 live feed so the
    frontend never needs to contact arcgis.com directly.

    Query params
    ------------
    south, west, north, east : float  – viewport bounding box (required)

    Returns
    -------
    JSON: { "warnings": [...], "count": <int> }

    Each warning has: event, severity, summary, description, instruction,
    affected, expires (ISO-8601), color (hex), marine (bool), rings ([[lat,lng]])
    """
    from services.arcgis_live_feeds import fetch_marine_warnings

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError, TypeError):
        return jsonify(
            error_envelope(
                "invalid_params",
                "south, west, north, east query parameters are required floats",
            )
        ), 400

    warnings = fetch_marine_warnings(south, west, north, east)
    return jsonify({"warnings": warnings, "count": len(warnings)})


@bp.route("/api/map/active-storms", methods=["GET"])
def map_active_storms() -> Any:
    """Return active tropical storms with forecast track and uncertainty cone.

    Proxies the ArcGIS Living Atlas Active_Hurricanes_v1 live feed.
    Returns an empty list when no storms are active.

    Returns
    -------
    JSON: { "storms": [...], "count": <int> }

    Each storm has: name, category, lat, lng, wind_mph, pressure_mb,
    track ([[lat,lng]]), cone (list of rings [[lat,lng]])
    """
    from services.arcgis_live_feeds import fetch_active_storms

    storms = fetch_active_storms()
    return jsonify({"storms": storms, "count": len(storms)})


@bp.route("/api/map/recent-storms", methods=["GET"])
def map_recent_storms() -> Any:
    """Return observed storm tracks for recent hurricane seasons.

    Proxies the ArcGIS Living Atlas Recent_Hurricanes_v1 live feed (NHC/JTWC).

    Query params
    ------------
    basin : str  – optional basin filter: AL, EP, CP, WP, …

    Returns
    -------
    JSON: { "tracks": [...], "count": <int> }

    Each track has: storm_id, name, basin, start_dtg, end_dtg, ss_max,
    category, color, path ([[lat,lng]])
    """
    from services.arcgis_live_feeds import fetch_recent_storm_tracks

    basin = request.args.get("basin", "").strip().upper() or None
    tracks = fetch_recent_storm_tracks(basin=basin)
    return jsonify({"tracks": tracks, "count": len(tracks)})


@bp.route("/api/weather/air-quality", methods=["GET"])
def weather_air_quality() -> Any:
    """Return the nearest OpenAQ PM2.5 reading to the given coordinates.

    Proxies the ArcGIS Living Atlas Air_Quality_PM25_Latest_Results live feed.

    Query params
    ------------
    lat : float  – latitude  (required)
    lng : float  – longitude (required)

    Returns
    -------
    JSON: { "aqi": { location, city, value, unit, updated, category, color,
                     distance_km } | null }
    """
    from services.arcgis_live_feeds import fetch_air_quality

    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, ValueError, TypeError):
        return jsonify(
            error_envelope("invalid_params", "lat and lng are required floats")
        ), 400

    result = fetch_air_quality(lat, lng)
    return jsonify({"aqi": result})


@bp.route("/api/weather/wind-forecast", methods=["GET"])
def weather_wind_forecast() -> Any:
    """Return NDFD wind forecast (speed/direction/gust) for a location.

    Proxies the ArcGIS Living Atlas NDFD_WindForecast_v1 live feed
    (NOAA National Digital Forecast Database, city-level, 3-h intervals).

    Query params
    ------------
    lat : float  – latitude  (required)
    lng : float  – longitude (required)

    Returns
    -------
    JSON: { "periods": [...], "count": <int> }

    Each period has: interval_start (ISO-8601), wind_dir_deg, wind_dir,
    wind_speed (knots), wind_gust (knots)
    """
    from services.arcgis_live_feeds import fetch_wind_forecast

    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, ValueError, TypeError):
        return jsonify(
            error_envelope("invalid_params", "lat and lng are required floats")
        ), 400

    periods = fetch_wind_forecast(lat, lng)
    return jsonify({"periods": periods, "count": len(periods)})


@bp.route("/api/map/sst-stations", methods=["GET"])
def map_sst_stations() -> Any:
    """Return NOAA coral reef / SST monitoring stations in the bounding box.

    Proxies the ArcGIS Living Atlas Coral_Reef_Stations live feed (NOAA CoRIS).
    Includes live sea-surface temperature, temperature anomaly, and bleaching alerts.

    Query params
    ------------
    south, west, north, east : float  – viewport bounding box (required)

    Returns
    -------
    JSON: { "stations": [...], "count": <int> }

    Each station has: name, lat, lng, sst_c, sst_f, ssta, dhw,
    alert, alert_label, alert_color, updated
    """
    from services.arcgis_live_feeds import fetch_sst_stations

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError, TypeError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    stations = fetch_sst_stations(south, west, north, east)
    return jsonify({"stations": stations, "count": len(stations)})


@bp.route("/api/map/wildfires", methods=["GET"])
def map_wildfires() -> Any:
    """Return active wildfire incidents intersecting the bounding box.

    Proxies the ArcGIS Living Atlas USA_Wildfires_v1 live feed (NIFC/IRWIN data).

    Query params
    ------------
    south, west, north, east : float  – viewport bounding box (required)

    Returns
    -------
    JSON: { "fires": [...], "count": <int> }

    Each fire has: name, state, county, acres, contained_pct, cause,
    discovered, age_days, lat, lng
    """
    from services.arcgis_live_feeds import fetch_wildfire_incidents

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError, TypeError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    fires = fetch_wildfire_incidents(south, west, north, east)
    return jsonify({"fires": fires, "count": len(fires)})


@bp.route("/api/map/smoke", methods=["GET"])
def map_smoke() -> Any:
    """Return current smoke forecast polygons for the bounding box.

    Proxies the ArcGIS Living Atlas NDGD_SmokeForecast_v1 live feed (NOAA NDGD).
    Returns only the most recent hour's polygons to avoid stacking.

    Query params
    ------------
    south, west, north, east : float  – viewport bounding box (required)

    Returns
    -------
    JSON: { "polygons": [...], "count": <int> }

    Each polygon has: class_desc, label, fill (hex), opacity, valid_from,
    valid_to, rings ([[lat,lng]])
    """
    from services.arcgis_live_feeds import fetch_smoke_forecast

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError, TypeError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    polygons = fetch_smoke_forecast(south, west, north, east)
    return jsonify({"polygons": polygons, "count": len(polygons)})


@bp.route("/api/weather/precip-forecast", methods=["GET"])
def weather_precip_forecast() -> Any:
    """Return NDFD precipitation forecast for a location (6-h intervals, ~24 h).

    Proxies the ArcGIS Living Atlas NDFD_Precipitation_v1 live feed.

    Query params
    ------------
    lat : float  – latitude  (required)
    lng : float  – longitude (required)

    Returns
    -------
    JSON: { "periods": [...], "count": <int> }

    Each period has: from_time, to_time, category (0–19), label, rain (bool)
    """
    from services.arcgis_live_feeds import fetch_precip_forecast

    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, ValueError, TypeError):
        return jsonify(
            error_envelope("invalid_params", "lat and lng are required floats")
        ), 400

    periods = fetch_precip_forecast(lat, lng)
    return jsonify({"periods": periods, "count": len(periods)})


@bp.route("/api/map/sea-ice", methods=["GET"])
def map_sea_ice() -> Any:
    """Return the most recent Arctic sea ice extent polygon and statistics.

    Proxies the ArcGIS Living Atlas seaice_extent_N_v1 live feed (NSIDC data).
    Returns the latest monthly record (typically the previous calendar month).

    Returns
    -------
    JSON: { "sea_ice": { year, month, area_mkm2, extent_mkm2, rings } | null }
    """
    from services.arcgis_live_feeds import fetch_sea_ice_extent

    result = fetch_sea_ice_extent()
    return jsonify({"sea_ice": result})


@bp.route("/api/weather/temp-forecast", methods=["GET"])
def weather_temp_forecast() -> Any:
    """Return NDFD 5-7 day daily high/low temperature forecast for a location.

    Proxies the ArcGIS Living Atlas NDFD_DailyTemperature_v1 live feed (NOAA NDFD).
    Queries layer 0 (Minimum) and layer 1 (Maximum) with a ±0.5° bbox.

    Query params: lat, lng

    Returns
    -------
    JSON: { "days": [ { date, min_f, max_f }, … ] }
    """
    from services.arcgis_live_feeds import fetch_temp_forecast

    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "lat and lng are required")
        ), 400

    days = fetch_temp_forecast(lat, lng)
    return jsonify({"days": days})


@bp.route("/api/map/seismic", methods=["GET"])
def map_seismic() -> Any:
    """Return USGS earthquake events (M ≥ 2.5) intersecting the bounding box.

    Proxies the ArcGIS Living Atlas USGS_Seismic_Data_v1 live feed (USGS ANSS).
    Filters to earthquakes only (excludes other event types when possible).

    Query params: south, west, north, east (decimal degrees)

    Returns
    -------
    JSON: { "events": [ { lat, lng, mag, depth_km, place, time, hours_old,
                           tsunami, alert, alert_color, sig, event_type } ], "count" }
    """
    from services.arcgis_live_feeds import fetch_seismic_events

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    events = fetch_seismic_events(south, west, north, east)
    return jsonify({"events": events, "count": len(events)})


@bp.route("/api/weather/drought", methods=["GET"])
def weather_drought() -> Any:
    """Return US Drought Monitor intensity at a location.

    Proxies the ArcGIS Living Atlas US_Drought_Intensity_v1 live feed (NDMC/USDA).
    Only covers CONUS; returns null outside coverage area.

    Query params: lat, lng

    Returns
    -------
    JSON: { "drought": { dm, code, label, color, date, d0, d1, d2, d3, d4 } | null }
    """
    from services.arcgis_live_feeds import fetch_drought

    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "lat and lng are required")
        ), 400

    result = fetch_drought(lat, lng)
    return jsonify({"drought": result})


@bp.route("/api/map/metar", methods=["GET"])
def map_metar() -> Any:
    """Return current NOAA METAR surface observations for the bounding box.

    Proxies the ArcGIS Living Atlas NOAA_METAR_current_wind_speed_direction_v1
    live feed. Wind speed is converted from km/h to knots server-side.

    Query params: south, west, north, east (decimal degrees)

    Returns
    -------
    JSON: { "stations": [ { icao, name, lat, lng, observed, temp_f, dew_f,
                             humidity, wind_deg, wind_dir, wind_kt, gust_kt,
                             wind_chill_f, heat_index_f, visibility_m, pressure_mb,
                             sky, weather, flight_cat, cat_color } ], "count" }
    """
    from services.arcgis_live_feeds import fetch_metar_stations

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    stations = fetch_metar_stations(south, west, north, east)
    return jsonify({"stations": stations, "count": len(stations)})


@bp.route("/api/map/terminator", methods=["GET"])
def map_terminator() -> Any:
    """Return the current day/night terminator shadow polygon.

    Proxies the ArcGIS Living Atlas Day_Night_Terminator live feed (layer 2 —
    the night-shadow polygon that rotates as the Earth turns).  Updates every
    ~5 minutes on the server.

    Returns
    -------
    JSON: { "terminator": { rings ([[lat,lng]]), timestamp (ISO) } | null }
    """
    from services.arcgis_live_feeds import fetch_terminator

    result = fetch_terminator()
    return jsonify({"terminator": result})


@bp.route("/api/map/stream-gauges", methods=["GET"])
def map_stream_gauges() -> Any:
    """Return live stream gauge readings for the bounding box.

    Proxies the ArcGIS Living Atlas Live_Stream_Gauges_v1 live feed
    (USGS/NWS water level / flood stage monitoring stations).

    Query params: south, west, north, east (decimal degrees)

    Returns
    -------
    JSON: { "gauges": [ { id, name, lat, lng, stage_ft, flow_cfs, status,
                          status_class (0-4), status_color, status_24h,
                          status_48h, status_72h, updated, station_url,
                          graph_url } ], "count" }
    """
    from services.arcgis_live_feeds import fetch_stream_gauges

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    gauges = fetch_stream_gauges(south, west, north, east)
    return jsonify({"gauges": gauges, "count": len(gauges)})


@bp.route("/api/map/storm-reports", methods=["GET"])
def map_storm_reports() -> Any:
    """Return NOAA severe weather reports (past 24 h) for the bounding box.

    Proxies hail, tornado, and wind-damage layers from the ArcGIS Living Atlas
    NOAA_storm_reports_v1 live feed and combines them into a single list.

    Query params: south, west, north, east (decimal degrees)

    Returns
    -------
    JSON: { "reports": [ { type, lat, lng, time (ISO), location, state,
                            comments, magnitude, color } ], "count" }
    """
    from services.arcgis_live_feeds import fetch_storm_reports

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    reports = fetch_storm_reports(south, west, north, east)
    return jsonify({"reports": reports, "count": len(reports)})


@bp.route("/api/map/air-quality", methods=["GET"])
def map_air_quality() -> Any:
    """Return PM2.5 AQI monitoring stations for the bounding box.

    Proxies the ArcGIS Living Atlas Air_Quality_PM25_Latest_Results live feed
    (OpenAQ global network). Returns one dot per station coloured by AQI level.

    Query params: south, west, north, east (decimal degrees)

    Returns
    -------
    JSON: { "stations": [ { lat, lng, name, pm25, category, color, updated } ],
            "count" }
    """
    from services.arcgis_live_feeds import fetch_aqi_map

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    stations = fetch_aqi_map(south, west, north, east)
    resp = jsonify({"stations": stations, "count": len(stations)})
    resp.headers["Cache-Control"] = "public, max-age=900, stale-while-revalidate=120"
    return resp


@bp.route("/api/map/drought", methods=["GET"])
def map_drought() -> Any:
    """Return US Drought Monitor intensity polygons for the bounding box.

    Proxies the ArcGIS Living Atlas US_Drought_Intensity_v1 live feed
    (National Drought Mitigation Center / USDA). CONUS coverage only.

    Query params: south, west, north, east (decimal degrees)

    Returns
    -------
    JSON: { "polygons": [ { dm, code, label, color, rings [[lat,lng]] } ],
            "count" }
    """
    from services.arcgis_live_feeds import fetch_drought_map

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    polygons = fetch_drought_map(south, west, north, east)
    resp = jsonify({"polygons": polygons, "count": len(polygons)})
    resp.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=300"
    return resp


@bp.route("/api/map/precipitation", methods=["GET"])
def map_precipitation() -> Any:
    """Return NDFD precipitation forecast polygons for the bounding box.

    Proxies the ArcGIS Living Atlas NDFD_Precipitation_v1 live feed
    (NOAA National Digital Forecast Database). Each polygon covers a 6-hour
    forecast period with a rainfall amount category.

    Query params: south, west, north, east (decimal degrees)

    Returns
    -------
    JSON: { "polygons": [ { from_time, to_time, category, label, color,
                             rings [[lat,lng]] } ], "count" }
    """
    from services.arcgis_live_feeds import fetch_precipitation_map

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    polygons = fetch_precipitation_map(south, west, north, east)
    resp = jsonify({"polygons": polygons, "count": len(polygons)})
    resp.headers["Cache-Control"] = "public, max-age=1800, stale-while-revalidate=120"
    return resp


@bp.route("/api/map/temperature", methods=["GET"])
def map_temperature() -> Any:
    """Return NDFD daily min/max temperature polygons for the bounding box.

    Proxies the ArcGIS Living Atlas NDFD_DailyTemperature_v1 live feed
    (layers 0 = min, 1 = max).  Each polygon covers one forecast day with a
    temperature value colour-coded from blue (cold) to red (hot).

    Query params: south, west, north, east (decimal degrees)
    Optional:     layer = "min" | "max" | "both" (default "max")

    Returns
    -------
    JSON: { "min": [...], "max": [...] }
    Each entry: { temp_f, period (YYYY-MM-DD), color, rings [[lat,lng]] }
    """
    from services.arcgis_live_feeds import fetch_ndfd_temperature_map

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    data = fetch_ndfd_temperature_map(south, west, north, east)
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=300"
    return resp


@bp.route("/api/map/buoys", methods=["GET"])
def map_buoys() -> Any:
    """Return NDBC weather buoy observations for the bounding box.

    Proxies the ArcGIS Living Atlas NDBC_Observations_v1 live feed.
    Buoys report wave height, sea surface temperature, wind speed/direction,
    and pressure updated approximately every hour.

    Query params: south, west, north, east (decimal degrees)

    Returns
    -------
    JSON: { "buoys": [ { lat, lng, id, name, water_temp_f, wave_ht_ft,
                          wind_kt, wind_dir, period_s, pressure_mb, updated } ],
            "count" }
    """
    from services.arcgis_live_feeds import fetch_ndbc_buoys

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    buoys = fetch_ndbc_buoys(south, west, north, east)
    resp = jsonify({"buoys": buoys, "count": len(buoys)})
    resp.headers["Cache-Control"] = "public, max-age=900, stale-while-revalidate=120"
    return resp


@bp.route("/api/map/hfradar", methods=["GET"])
def map_hfradar() -> Any:
    """Return NOAA HF Radar surface current vectors for the bounding box.

    Merges East Coast, Gulf of Mexico, and West Coast HF Radar networks.
    Each vector represents the hourly surface current at a grid point.

    Query params: south, west, north, east (decimal degrees)

    Returns
    -------
    JSON: { "vectors": [ { lat, lng, speed_cms, speed_kts, dir_deg,
                            u, v, color, updated } ], "count" }
    """
    from services.arcgis_live_feeds import fetch_hfradar_currents

    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "south, west, north, east required")
        ), 400

    vectors = fetch_hfradar_currents(south, west, north, east)
    resp = jsonify({"vectors": vectors, "count": len(vectors)})
    resp.headers["Cache-Control"] = "public, max-age=1800, stale-while-revalidate=180"
    return resp


@bp.route("/api/map/tropical-outlook", methods=["GET"])
def map_tropical_outlook() -> Any:
    """Return NHC tropical weather outlook development-area polygons.

    Proxies the ArcGIS Living Atlas NHC_Tropical_Weather_Outlook_v1 live feed.
    Returns an empty list when no areas of interest are active.

    Returns
    -------
    JSON: { "areas": [ { probability, prob_label, color, basin,
                          rings [[lat,lng]], discussion } ], "count" }
    """
    from services.arcgis_live_feeds import fetch_tropical_outlook

    areas = fetch_tropical_outlook()
    resp = jsonify({"areas": areas, "count": len(areas)})
    resp.headers["Cache-Control"] = "public, max-age=1800, stale-while-revalidate=300"
    return resp


# ── Admin: custom map marker CRUD ─────────────────────────────────────────────


def _require_map_admin():
    """Return a 403 response if the current user is not an admin, else None."""
    if not g.user or not g.user.get("is_admin"):
        return jsonify({"error": "Forbidden"}), 403
    return None


@bp.route("/api/map/custom-markers", methods=["GET"])
def custom_markers_list() -> Any:
    """Return all non-deleted custom map markers (public read)."""
    markers = get_custom_markers()
    return jsonify({"markers": markers, "count": len(markers)})


@bp.route("/api/map/custom-markers", methods=["POST"])
def custom_markers_create() -> Any:
    """Create a new custom marker (admin only)."""
    err = _require_map_admin()
    if err:
        return err
    from storage.sqlite import create_custom_marker

    data = request.get_json(silent=True) or {}
    try:
        lat = float(data["lat"])
        lng = float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "lat and lng are required floats"}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({"error": "lat/lng out of range"}), 400
    name = str(data.get("name", ""))[:120]
    type_ = str(data.get("type", "fishing"))
    description = str(data.get("description", ""))[:500]
    marker = create_custom_marker(lat, lng, name, type_, description, g.user["id"])
    return jsonify(marker), 201


@bp.route("/api/map/custom-markers/<int:marker_id>", methods=["PUT"])
def custom_markers_update(marker_id: int) -> Any:
    """Update position, name, type, or description of a custom marker (admin only)."""
    err = _require_map_admin()
    if err:
        return err
    from storage.sqlite import update_custom_marker

    data = request.get_json(silent=True) or {}
    lat = float(data["lat"]) if "lat" in data else None
    lng = float(data["lng"]) if "lng" in data else None
    name = str(data["name"])[:120] if "name" in data else None
    type_ = str(data.get("type")) if "type" in data else None
    description = str(data["description"])[:500] if "description" in data else None
    updated = update_custom_marker(
        marker_id, lat=lat, lng=lng, name=name, type_=type_, description=description
    )
    if updated is None:
        return jsonify({"error": "Marker not found"}), 404
    return jsonify(updated)


@bp.route("/api/map/custom-markers/<int:marker_id>", methods=["DELETE"])
def custom_markers_delete(marker_id: int) -> Any:
    """Soft-delete a custom marker (admin only)."""
    err = _require_map_admin()
    if err:
        return err
    from storage.sqlite import delete_custom_marker

    ok = delete_custom_marker(marker_id)
    if not ok:
        return jsonify({"error": "Marker not found"}), 404
    return jsonify({"deleted": marker_id})
