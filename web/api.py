"""JSON API routes: preferences, fishing log, forecast data, sharing."""

from __future__ import annotations

import concurrent.futures as _cf
import logging
import threading
from zoneinfo import available_timezones
from typing import Any, Optional


from flask import (
    Blueprint,
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
from locations import get_location
from storage.reg_scraper import invalidate_cache as _reg_invalidate_cache
from regulations import lookup_regulation
from storage.cache import (
    CACHE_MAX_AGE_HOURS,
    _forecast_age_minutes,
    load_cached_forecast,
    save_forecast,
)
from services.datagov import get_water_quality_summary, fetch_beach_closures
from services.arcgis_live_feeds import (
    fetch_air_quality,
    fetch_drought,
    fetch_metar_stations,
    fetch_ndbc_buoys,
    fetch_precip_forecast,
    fetch_stream_gauges,
    fetch_temp_forecast,
    fetch_tropical_outlook,
    fetch_wildfire_incidents,
    fetch_wind_forecast,
)
from storage.sqlite import (
    add_log_entry,
    add_push_subscription,
    delete_log_entry,
    delete_push_subscription,
    get_catch_conditions,
    get_log_entries,
    get_log_stats,
    get_page_layout,
    get_preferences,
    get_recent_catch_activity,
    save_page_layout,
    save_preferences,
)
from domain.catch_insights import analyze_catch_patterns
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


@bp.route("/api/v1/push/public-key", methods=["GET"])
def push_public_key_v1() -> Any:
    """Return the VAPID public key (and whether push is configured)."""
    from services.push import get_public_key, is_push_configured

    return jsonify(
        success_envelope(
            {"publicKey": get_public_key(), "configured": is_push_configured()}
        )
    )


@bp.route("/api/v1/push/subscribe", methods=["POST"])
def push_subscribe_v1() -> Any:
    """Store a browser Web Push subscription for the logged-in user."""
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401

    data = request.get_json(silent=True) or {}
    sub = data.get("subscription") if isinstance(data.get("subscription"), dict) else data
    endpoint = sub.get("endpoint") if isinstance(sub, dict) else None
    keys = sub.get("keys") if isinstance(sub, dict) else None
    if (
        not isinstance(endpoint, str)
        or not endpoint
        or len(endpoint) > 1024
        or not isinstance(keys, dict)
    ):
        return _json_error(
            ApiError("invalid_subscription", "Malformed push subscription", status=400)
        )
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not isinstance(p256dh, str) or not isinstance(auth, str) or not p256dh or not auth:
        return _json_error(
            ApiError("invalid_subscription", "Subscription missing keys", status=400)
        )

    add_push_subscription(g.user["id"], endpoint, p256dh, auth)
    return jsonify(success_envelope({"ok": True}))


@bp.route("/api/v1/push/unsubscribe", methods=["POST"])
def push_unsubscribe_v1() -> Any:
    """Remove a browser Web Push subscription by endpoint."""
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401

    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        return _json_error(
            ApiError("invalid_param", "endpoint is required", status=400)
        )
    delete_push_subscription(endpoint)
    return jsonify(success_envelope({"ok": True}))


@bp.route("/api/v1/notifications/test", methods=["POST"])
def notifications_test_v1() -> Any:
    """Send a test alert to the current user over their enabled channels.

    Only ever targets the logged-in user's own email / push subscriptions, so
    it can't be used to message anyone else. Reports which channels actually
    sent (False when the channel is off or unconfigured).
    """
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401

    from storage.sqlite import get_push_subscriptions
    from services.notifications import build_email

    uid = g.user["id"]
    prefs = get_preferences(uid).get("notification_prefs") or {}
    import os as _os

    site_url = _os.environ.get("SITE_URL", "").rstrip("/")
    results = {"email": False, "push": False}

    decision: dict[str, Any] = {
        "verdict": "Test alert",
        "score": None,
        "summary": "If you can read this, your fishing alerts are set up correctly.",
        "best_times": [],
        "window": "",
    }

    if prefs.get("email") and g.user.get("email"):
        from services.email import send_email

        manage_url = f"{site_url}/account" if site_url else ""
        subject, text_body, html_body = build_email(
            "your saved spot", decision, manage_url=manage_url
        )
        results["email"] = bool(
            send_email(g.user["email"], f"[Test] {subject}", text_body, html_body)
        )

    if prefs.get("push"):
        from services.push import send_push

        url = f"{site_url}/account" if site_url else "/account"
        for sub in get_push_subscriptions(uid):
            if send_push(
                sub, "Test alert", "Your push notifications are working.", url
            ):
                results["push"] = True

    return jsonify(success_envelope({"sent": results}))


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
        bait=payload.bait,
        rig=payload.rig,
        conditions=_catch_conditions_snapshot(payload.location_id),
    )
    resp = jsonify({"ok": True, "id": entry_id})
    resp.headers["Deprecation"] = _DEPRECATION_HEADER
    resp.headers["Link"] = '</api/v1/log>; rel="successor-version"'
    return resp, 201


def _catch_conditions_snapshot(location_id: str) -> dict[str, Any]:
    """Pull the current forecast conditions for a location to stamp on a catch.

    Best-effort: returns an empty dict if there's no cached forecast yet so a
    catch is still logged (just without a condition snapshot).
    """
    if not location_id:
        return {}
    forecast = load_cached_forecast(location_id, user_id=None, include_stale=True)
    if not forecast:
        return {}
    conditions = forecast.get("conditions", {}) or {}
    solunar = forecast.get("solunar", {}) or {}
    return {
        "tide_state": forecast.get("tide_state", ""),
        "wind_dir": conditions.get("wind_dir", ""),
        "water_temp_f": conditions.get("water_temp_f"),
        "moon_phase": solunar.get("moon_phase", ""),
    }


@bp.route("/api/v1/log/patterns", methods=["GET"])
def log_patterns_v1() -> Any:
    """Return learned catch patterns for the logged-in user."""
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401
    uid = g.user["id"]
    loc_id = (request.args.get("location_id") or request.args.get("location") or "").strip()
    catches = get_catch_conditions(uid, loc_id)
    # When scoped to a location, compare patterns against its current forecast.
    current = _catch_conditions_snapshot(loc_id) if loc_id else None
    return jsonify(success_envelope(analyze_catch_patterns(catches, current=current)))


@bp.route("/api/v1/community/activity", methods=["GET"])
def community_activity_v1() -> Any:
    """Anonymized, aggregated recent-catch activity for a location.

    Returns counts and top species only when enough distinct opted-in anglers
    contributed (k-anonymity in the storage layer); otherwise ``available``
    is false. No individual user data is ever exposed.
    """
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401
    loc_id = (request.args.get("location_id") or request.args.get("location") or "").strip()
    activity = get_recent_catch_activity(loc_id) if loc_id else None
    if not activity:
        return jsonify(success_envelope({"available": False}))
    activity["available"] = True
    return jsonify(success_envelope(activity))


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
        bait=payload.bait,
        rig=payload.rig,
        conditions=_catch_conditions_snapshot(payload.location_id),
    )
    created = {
        "id": entry_id,
        "species": payload.species,
        "size": payload.size,
        "notes": payload.notes,
        "bait": payload.bait,
        "rig": payload.rig,
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
    deleted = delete_log_entry(uid, entry_id)
    if not deleted:
        return jsonify(error_envelope("not_found", "Log entry not found")), 404
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


@bp.route("/api/weather/env-context", methods=["GET"])
def weather_env_context() -> Any:
    """Return air-quality + drought data for a location in one round trip."""
    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "lat and lng are required floats")
        ), 400

    with _cf.ThreadPoolExecutor(max_workers=2) as pool:
        fut_aqi = pool.submit(fetch_air_quality, lat, lng)
        fut_drought = pool.submit(fetch_drought, lat, lng)
        try:
            aqi_result = fut_aqi.result(timeout=20)
        except Exception:
            aqi_result = None
        try:
            drought_result = fut_drought.result(timeout=20)
        except Exception:
            drought_result = None

    resp = jsonify({"aqi": aqi_result, "drought": drought_result})
    resp.headers["Cache-Control"] = "public, max-age=900, stale-while-revalidate=60"
    return resp


@bp.route("/api/map/stat-cards", methods=["GET"])
def map_stat_cards() -> Any:
    """Return all live stat-card data for a location in one round trip."""
    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "lat and lng are required floats")
        ), 400

    def _bbox(pad: float) -> tuple[float, float, float, float]:
        return lat - pad, lng - pad, lat + pad, lng + pad

    b_s, b_w, b_n, b_e = _bbox(3.0)
    m_s, m_w, m_n, m_e = _bbox(1.5)
    f_s, f_w, f_n, f_e = _bbox(2.5)
    g_s, g_w, g_n, g_e = _bbox(1.5)

    with _cf.ThreadPoolExecutor(max_workers=5) as pool:
        fut_buoys = pool.submit(fetch_ndbc_buoys, b_s, b_w, b_n, b_e)
        fut_metar = pool.submit(fetch_metar_stations, m_s, m_w, m_n, m_e)
        fut_fires = pool.submit(fetch_wildfire_incidents, f_s, f_w, f_n, f_e)
        fut_gauges = pool.submit(fetch_stream_gauges, g_s, g_w, g_n, g_e)
        fut_tropical = pool.submit(fetch_tropical_outlook)
        try:
            buoys = fut_buoys.result(timeout=20)
        except Exception:
            buoys = []
        try:
            stations = fut_metar.result(timeout=20)
        except Exception:
            stations = []
        try:
            fires = fut_fires.result(timeout=20)
        except Exception:
            fires = []
        try:
            gauges = fut_gauges.result(timeout=20)
        except Exception:
            gauges = []
        try:
            areas = fut_tropical.result(timeout=20)
        except Exception:
            areas = []

    resp = jsonify({
        "buoys": {"buoys": buoys, "count": len(buoys)},
        "metar": {"stations": stations, "count": len(stations)},
        "fires": {"fires": fires, "count": len(fires)},
        "gauges": {"gauges": gauges, "count": len(gauges)},
        "tropical": {"areas": areas, "count": len(areas)},
    })
    resp.headers["Cache-Control"] = "public, max-age=900, stale-while-revalidate=120"
    return resp


@bp.route("/api/weather/combined-forecast", methods=["GET"])
def weather_combined_forecast() -> Any:
    """Fetch wind, precipitation, and temperature forecasts in a single request."""
    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error_envelope("invalid_params", "lat and lng are required floats")
        ), 400

    with _cf.ThreadPoolExecutor(max_workers=3) as pool:
        fut_wind = pool.submit(fetch_wind_forecast, lat, lng)
        fut_precip = pool.submit(fetch_precip_forecast, lat, lng)
        fut_temp = pool.submit(fetch_temp_forecast, lat, lng)

        try:
            wind_periods = fut_wind.result(timeout=20)
        except Exception:
            wind_periods = None
        try:
            precip_periods = fut_precip.result(timeout=20)
        except Exception:
            precip_periods = None
        try:
            temp_days = fut_temp.result(timeout=20)
        except Exception:
            temp_days = None

    payload: dict[str, Any] = {
        "wind": {"periods": wind_periods, "count": len(wind_periods)}
        if wind_periods is not None
        else None,
        "precip": {"periods": precip_periods, "count": len(precip_periods)}
        if precip_periods is not None
        else None,
        "temp": {"days": temp_days} if temp_days is not None else None,
    }
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "public, max-age=1800, stale-while-revalidate=120"
    return resp


@bp.route("/api/v1/geo/environmental")
def geo_environmental() -> Any:
    """Return water quality data for the water-conditions dashboard widget."""
    try:
        lat = float(request.args["lat"])
        lng = float(request.args["lng"])
    except (KeyError, ValueError, TypeError):
        return jsonify(error_envelope("invalid_params", "lat and lng are required")), 400

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify(error_envelope("invalid_params", "lat/lng out of range")), 400

    state = request.args.get("state", "").upper().strip()
    wq_summary = get_water_quality_summary(lat, lng)
    beach_closures: list[Any] = []
    if state and len(state) == 2:
        try:
            beach_closures = fetch_beach_closures(state)[:10]
        except Exception:
            beach_closures = []

    resp = jsonify({
        "ok": True,
        "data": {
            "water_quality": wq_summary,
            "beach_closures": beach_closures,
            "location": {"lat": lat, "lng": lng},
        },
    })
    resp.headers["Cache-Control"] = "public, max-age=1800, stale-while-revalidate=60"
    return resp
