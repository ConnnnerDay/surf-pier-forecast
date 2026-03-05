"""JSON API routes: preferences, fishing log, forecast data, sharing."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

from flask import Blueprint, current_app, g, jsonify, redirect, request, session, url_for

from domain.forecast import build_share_text, generate_forecast
from services.forecast_refresh import enqueue_forecast_refresh, is_refreshing
from locations import get_location
from regulations import lookup_regulation
from storage.cache import CACHE_MAX_AGE_HOURS, _forecast_age_minutes, load_cached_forecast, save_forecast
from storage.sqlite import (
    add_log_entry,
    attach_photos_to_entry,
    delete_log_entry,
    get_entry_photo_paths,
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
        logger.info("cache.force_refresh location_id=%s", loc_id)
        forecast_data = generate_forecast(location)
        save_forecast(forecast_data, loc_id, user_id=user_id)
        logger.info("cache.regenerated location_id=%s", loc_id)
    else:
        forecast_data = load_cached_forecast(loc_id, user_id=user_id)
        if forecast_data:
            logger.info("cache.hit location_id=%s", loc_id)
        if not forecast_data:
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
    forecast_data = load_cached_forecast(location_id, user_id=None, include_stale=True)
    if not forecast_data:
        return jsonify(success_envelope({
            "location_id": location_id,
            "last_generated_at": None,
            "is_stale": True,
            "is_refreshing": is_refreshing(location_id, user_id=None),
        }))

    age = _forecast_age_minutes(forecast_data)
    is_stale = bool(age is not None and age > CACHE_MAX_AGE_HOURS * 60)
    return jsonify(success_envelope({
        "location_id": location_id,
        "last_generated_at": forecast_data.get("generated_at"),
        "is_stale": is_stale,
        "is_refreshing": is_refreshing(location_id, user_id=None),
    }))




@bp.route("/api/v1/forecast/<location_id>/outlook", methods=["GET"])
def forecast_outlook_v1(location_id: str) -> Any:
    """Return cached 3-day outlook payload for lazy dashboard hydration."""
    user_id = g.user["id"] if g.user else None
    forecast_data = load_cached_forecast(location_id, user_id=user_id, include_stale=True)
    if not forecast_data:
        return _json_error(ApiError("forecast_not_cached", "No cached forecast available", status=404))

    return jsonify(success_envelope({
        "location_id": location_id,
        "outlook": forecast_data.get("outlook") or [],
        "best_day": forecast_data.get("best_day"),
        "activity_timeline": forecast_data.get("activity_timeline") or [],
    }))


@bp.route("/api/v1/forecast/<location_id>/solunar", methods=["GET"])
def forecast_solunar_v1(location_id: str) -> Any:
    """Return cached solunar payload for lazy dashboard hydration."""
    user_id = g.user["id"] if g.user else None
    forecast_data = load_cached_forecast(location_id, user_id=user_id, include_stale=True)
    if not forecast_data:
        return _json_error(ApiError("forecast_not_cached", "No cached forecast available", status=404))

    return jsonify(success_envelope({
        "location_id": location_id,
        "solunar": forecast_data.get("solunar") or {},
    }))

@bp.route("/api/refresh", methods=["POST"])
def refresh() -> Any:
    """Queue generation of a new forecast and return immediately."""
    location = get_session_location()
    if location is None:
        return redirect(url_for("views.setup"))
    enqueue_forecast_refresh(location["id"], user_id=None)
    return redirect(url_for("views.index", cached="refreshing"))


@bp.route("/api/v1/regulations/refresh", methods=["POST"])
def regulations_refresh_v1() -> Any:
    """Invalidate the live-scrape regulation cache.

    Optionally filter to a single state via the ``state`` query param.
    The next regulation lookup for affected entries will re-scrape the
    official state agency website.
    """
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
    species_name = request.args.get("species", "").strip()
    if not species_name:
        return _json_error(ApiError("missing_param", "'species' query parameter is required", status=400))

    # Resolve state: explicit param takes priority, else derive from location_id,
    # else fall back to the current session location.
    state = request.args.get("state", "").strip().upper()
    if not state:
        location_id = request.args.get("location_id", "").strip()
        loc = get_location(location_id) if location_id else None
        if not loc:
            loc = get_session_location()
        if loc:
            state = (loc.get("state") or "").upper()

    reg = lookup_regulation(species_name, state) if state else None

    return jsonify(success_envelope({
        "species": species_name,
        "state": state or None,
        "regulation": reg,
    }))


_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB per photo


def _save_upload(file_storage, user_id: int) -> Tuple[str, str]:
    """Validate + write an uploaded photo.

    Returns ``(relative_path, absolute_path)`` where relative_path is suitable
    for storing in the DB and serving via ``/static/...``.

    Raises ApiError on validation failure.
    """
    mime = file_storage.mimetype or ""
    if mime not in _ALLOWED_MIME:
        raise ApiError("invalid_file_type", f"Unsupported file type '{mime}'. Use JPEG, PNG, or WebP.", status=400)

    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    if ext not in _ALLOWED_EXT:
        raise ApiError("invalid_file_type", f"Unsupported extension '{ext}'. Use .jpg, .png, or .webp.", status=400)

    data = file_storage.read()
    if len(data) > _MAX_PHOTO_BYTES:
        raise ApiError("file_too_large", "Photo must be 8 MB or smaller.", status=413)

    upload_root = current_app.config["UPLOAD_FOLDER"]
    user_dir = os.path.join(upload_root, str(user_id))
    os.makedirs(user_dir, exist_ok=True)

    filename = f"{uuid.uuid4()}{ext}"
    abs_path = os.path.join(user_dir, filename)
    with open(abs_path, "wb") as fh:
        fh.write(data)

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
    sub = rel_path[len("uploads/"):] if rel_path.startswith("uploads/") else rel_path
    abs_path = os.path.join(upload_root, sub)
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

    uid = g.user["id"]
    paths = get_entry_photo_paths(uid, entry_id)
    if paths is None:
        return jsonify(error_envelope("not_found", "Log entry not found")), 404

    photo1_file = request.files.get("photo1")
    photo2_file = request.files.get("photo2")

    if not photo1_file and not photo2_file:
        return _json_error(ApiError("missing_param", "Provide at least one of: photo1, photo2", status=400))

    saved: Dict[str, str] = {}
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
