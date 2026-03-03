"""JSON API routes: preferences, fishing log, forecast data, sharing."""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

from flask import Blueprint, g, jsonify, redirect, request, session, url_for

from domain.forecast import build_share_text, generate_forecast
from locations import get_location
from storage.cache import load_cached_forecast, save_forecast
from storage.sqlite import (
    add_log_entry,
    delete_log_entry,
    get_log_entries,
    get_log_stats,
    get_preferences,
    save_preferences,
)
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

bp = Blueprint("api", __name__)


def _json_error(err: ApiError) -> Any:
    return jsonify(error_envelope(err.code, err.message, details=err.details)), err.status


def _v1_forecast_payload(query: ForecastQuery) -> Dict[str, Any]:
    location = get_location(query.location_id) if query.location_id else get_session_location()
    if not location:
        raise ApiError("location_not_found", "No valid location selected", status=404)

    loc_id = location["id"]
    user_id = g.user["id"] if g.user else None
    if query.force_refresh:
        forecast_data = generate_forecast(location)
        save_forecast(forecast_data, loc_id, user_id=user_id)
    else:
        forecast_data = load_cached_forecast(loc_id, user_id=user_id)
        if not forecast_data:
            forecast_data = generate_forecast(location)
            save_forecast(forecast_data, loc_id, user_id=user_id)

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
        uid, payload.location_id, payload.species,
        size=payload.size,
        notes=payload.notes,
    )
    return jsonify({"ok": True, "id": entry_id}), 201


@bp.route("/api/v1/log", methods=["GET", "POST"])
def log_v1() -> Any:
    if g.user is None:
        return jsonify(error_envelope("unauthorized", "Not logged in")), 401

    uid = g.user["id"]
    loc_id = (request.args.get("location_id") or request.args.get("location") or session.get("location_id") or "").strip()

    if request.method == "GET":
        entries = get_log_entries(uid, loc_id) if loc_id else []
        stats = normalize_log_stats(get_log_stats(uid, loc_id) if loc_id else {})
        return jsonify(success_envelope({"location_id": loc_id or None, "entries": entries, "stats": stats}))

    data = request.get_json(silent=True) or {}
    try:
        payload = LogCreatePayload.from_json(data, loc_id)
    except ApiError as err:
        return _json_error(err)

    entry_id = add_log_entry(uid, payload.location_id, payload.species, size=payload.size, notes=payload.notes)
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
    deleted = delete_log_entry(g.user["id"], entry_id)
    if not deleted:
        return jsonify(error_envelope("not_found", "Log entry not found")), 404
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


@bp.route("/api/refresh", methods=["POST"])
def refresh() -> Any:
    """Trigger generation of a new forecast."""
    location = get_session_location()
    if location is None:
        return redirect(url_for("views.setup"))
    try:
        new_forecast = generate_forecast(location)
        user_id = g.user["id"] if g.user else None
        save_forecast(new_forecast, location["id"], user_id=user_id)
        return redirect(url_for("views.index"))
    except Exception as exc:
        logger.error("Error refreshing forecast: %s", exc)
        return redirect(url_for("views.index", cached="true"))


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
