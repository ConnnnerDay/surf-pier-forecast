"""JSON API routes: preferences, fishing log, forecast data, sharing."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, redirect, request, session, url_for

from domain.forecast import build_share_text, generate_forecast
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

bp = Blueprint("api", __name__)


@bp.route("/api/preferences", methods=["GET", "POST"])
def preferences() -> Any:
    """Get or update preferences for the logged-in user."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    uid = g.user["id"]
    if request.method == "GET":
        return jsonify(get_preferences(uid))
    data = request.get_json(silent=True) or {}
    allowed = {"location_id", "theme", "units", "fishing_profile", "favorites"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if updates:
        save_preferences(uid, **updates)
        # Keep session location in sync
        if "location_id" in updates and updates["location_id"]:
            session["location_id"] = updates["location_id"]
    return jsonify({"ok": True})


@bp.route("/api/log", methods=["GET", "POST"])
def log() -> Any:
    """Get or add fishing log entries for the logged-in user."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    uid = g.user["id"]
    loc_id = request.args.get("location") or session.get("location_id", "")
    if request.method == "GET":
        entries = get_log_entries(uid, loc_id)
        stats = get_log_stats(uid, loc_id) if loc_id else {}
        return jsonify({"entries": entries, "stats": stats})
    data = request.get_json(silent=True) or {}
    species = data.get("species", "").strip()
    if not species or not loc_id:
        return jsonify({"error": "species and location required"}), 400
    entry_id = add_log_entry(
        uid, loc_id, species,
        size=data.get("size", ""),
        notes=data.get("notes", ""),
    )
    return jsonify({"ok": True, "id": entry_id}), 201


@bp.route("/api/log/<int:entry_id>", methods=["DELETE"])
def log_delete(entry_id: int) -> Any:
    """Delete a fishing log entry."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    deleted = delete_log_entry(g.user["id"], entry_id)
    if not deleted:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@bp.route("/api/forecast")
def forecast() -> Any:
    """Return the current forecast as JSON."""
    location = get_session_location()
    loc_id = location["id"] if location else ""
    forecast_data = load_cached_forecast(loc_id)
    if forecast_data:
        return jsonify(forecast_data)
    return jsonify({"error": "No forecast available"}), 503


@bp.route("/api/refresh", methods=["POST"])
def refresh() -> Any:
    """Trigger generation of a new forecast."""
    location = get_session_location()
    if location is None:
        return redirect(url_for("views.setup"))
    try:
        new_forecast = generate_forecast(location)
        save_forecast(new_forecast, location["id"])
        return redirect(url_for("views.index"))
    except Exception as exc:
        print(f"Error refreshing forecast: {exc}")
        return redirect(url_for("views.index", cached="true"))


@bp.route("/api/share-text")
def share_text() -> Any:
    """Return a plain-text forecast summary for copy/paste sharing."""
    location = get_session_location()
    loc_id = location["id"] if location else ""
    forecast_data = load_cached_forecast(loc_id)
    if not forecast_data:
        return jsonify({"error": "No forecast available"}), 503
    text = build_share_text(forecast_data)
    return jsonify({"text": text, "location_id": loc_id})
