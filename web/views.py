"""Page view routes: dashboard, setup, profile, shared forecast."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import (
    Blueprint,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from locations import all_locations_sorted, find_nearest_locations, geocode_zip, get_location
from domain.forecast import generate_forecast, personalize_forecast
from services.forecast_refresh import enqueue_forecast_refresh
from storage.cache import (
    CACHE_MAX_AGE_HOURS,
    _forecast_age_minutes,
    _human_age,
    load_cached_forecast,
    save_forecast,
)
from storage.sqlite import get_preferences, save_preferences
from web.helpers import get_session_location

bp = Blueprint("views", __name__)
logger = logging.getLogger(__name__)


def _setup_context(**kwargs: Any) -> Dict[str, Any]:
    """Build common template context for the setup page."""
    current_loc = get_session_location()
    favorite_ids = []
    favorite_locations = []
    if g.user:
        prefs = get_preferences(g.user["id"])
        favorite_locations = [get_location(loc_id) for loc_id in prefs.get("favorites", [])]
        favorite_locations = [loc for loc in favorite_locations if loc]
        favorite_ids = [loc["id"] for loc in favorite_locations]

    context: Dict[str, Any] = {
        "results": None,
        "all_locations": all_locations_sorted(),
        "current_location": current_loc,
        "error": None,
        "favorite_ids": favorite_ids,
        "favorite_locations": favorite_locations,
    }
    context.update(kwargs)
    return context


def _extract_profile_from_request() -> Optional[Dict[str, Any]]:
    """Extract fishing profile from query parameters.

    Expected params: fishing_types (comma-separated), targets (comma-separated).
    Returns None if no profile params are present.
    """
    ft = request.args.get("fishing_types", "").strip()
    tg = request.args.get("targets", "").strip()
    if not ft and not tg:
        return None
    profile: Dict[str, Any] = {}
    if ft:
        profile["fishing_types"] = [t.strip() for t in ft.split(",") if t.strip()]
    if tg:
        profile["targets"] = [t.strip() for t in tg.split(",") if t.strip()]
    return profile


def _render_forecast(location: Dict[str, Any], cached_flag: Optional[str] = None) -> str:
    """Load (or refresh) the forecast for a location and render the dashboard."""
    loc_id = location["id"]
    forecast = load_cached_forecast(loc_id, user_id=None, include_stale=True)

    is_stale = False
    if forecast:
        age = _forecast_age_minutes(forecast)
        is_stale = bool(age is not None and age > CACHE_MAX_AGE_HOURS * 60)

    if forecast is None:
        logger.info("cache.miss location_id=%s", loc_id)
        try:
            forecast = generate_forecast(location)
            save_forecast(forecast, loc_id, user_id=None)
            logger.info("cache.regenerated location_id=%s", loc_id)
            cached_flag = None
        except Exception:
            return render_template(
                "error.html",
                message="Could not load forecast. Please try refreshing later.",
            ), 500
    elif is_stale:
        logger.info("cache.stale_served location_id=%s", loc_id)
        enqueue_forecast_refresh(loc_id, user_id=None)
        cached_flag = "refreshing"
    else:
        logger.info("cache.hit location_id=%s", loc_id)

    # Apply profile-based personalization (re-rank species for this user).
    # Query params take precedence; fall back to the user's stored DB profile.
    profile = _extract_profile_from_request()
    if not profile and g.user:
        stored = get_preferences(g.user["id"]).get("fishing_profile") or {}
        if stored.get("fishing_types") or stored.get("targets"):
            profile = stored
    if profile:
        forecast = personalize_forecast(forecast, profile, location)

    forecast["age_human"] = _human_age(_forecast_age_minutes(forecast))
    # Backfill for cached forecasts created before these fields existed.
    if not forecast.get("location_id"):
        forecast["location_id"] = loc_id
    if not forecast.get("location_state"):
        forecast["location_state"] = location.get("state", "")

    return render_template("index.html", forecast=forecast, cached=cached_flag,
                           share_id=loc_id)


@bp.route("/")
def index() -> str:
    """Render the dashboard with the current forecast."""
    location = get_session_location()
    if location is None:
        return redirect(url_for("views.setup"))

    cached_flag = request.args.get("cached")
    return _render_forecast(location, cached_flag)


@bp.route("/setup")
def setup() -> str:
    """Show the location setup page (zip code entry or browse)."""
    return render_template("setup.html", **_setup_context())


@bp.route("/setup/search", methods=["POST"])
def setup_search() -> str:
    """Process a zip code search and show nearby locations."""
    zipcode = request.form.get("zipcode", "").strip()
    if not zipcode or not zipcode.isdigit() or len(zipcode) != 5:
        return render_template("setup.html", **_setup_context(
            error="Please enter a valid 5-digit US zip code.",
            zipcode=zipcode,
        ))

    coords = geocode_zip(zipcode)
    if coords is None:
        return render_template("setup.html", **_setup_context(
            error=f"Could not find zip code {zipcode}. Please try another.",
            zipcode=zipcode,
        ))

    lat, lng = coords
    nearby = find_nearest_locations(lat, lng, n=6)
    if not nearby:
        return render_template("setup.html", **_setup_context(
            error="No supported fishing locations found within 300 miles. Try a coastal zip code.",
            zipcode=zipcode,
        ))

    return render_template("setup.html", **_setup_context(results=nearby, zipcode=zipcode))


@bp.route("/setup/select/<location_id>", methods=["POST"])
def setup_select(location_id: str) -> Any:
    """Save the selected location and redirect to the dashboard."""
    loc = get_location(location_id)
    if loc is None:
        return redirect(url_for("views.setup"))
    session["location_id"] = location_id
    session.permanent = True
    if g.user:
        save_preferences(g.user["id"], location_id=location_id, default_location_id=location_id)
    return redirect(url_for("views.index"))


@bp.route("/setup/favorite/<location_id>", methods=["POST"])
def setup_favorite(location_id: str) -> Any:
    """Toggle a favorite location for logged-in users from setup."""
    if not g.user:
        return redirect(url_for("views.setup"))
    if get_location(location_id) is None:
        return redirect(url_for("views.setup"))

    prefs = get_preferences(g.user["id"])
    favorites = [loc_id for loc_id in prefs.get("favorites", []) if get_location(loc_id)]
    if location_id in favorites:
        favorites = [loc_id for loc_id in favorites if loc_id != location_id]
    else:
        favorites.append(location_id)
    save_preferences(g.user["id"], favorites=favorites)

    next_url = request.form.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(url_for("views.setup"))


@bp.route("/profile")
def profile() -> str:
    """Show the fishing profile setup page."""
    prefs = get_preferences(g.user["id"]) if g.user else {}
    return render_template("profile.html", prefs=prefs)


@bp.route("/f/<location_id>")
def shared_forecast(location_id: str) -> str:
    """View a forecast for a specific location via shareable link."""
    location = get_location(location_id)
    if location is None:
        return render_template(
            "error.html",
            message="Location not found. It may have been removed.",
        ), 404

    session["location_id"] = location_id
    return _render_forecast(location)
