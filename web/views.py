"""Page view routes: dashboard, setup, profile, shared forecast."""

from __future__ import annotations

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
from storage.cache import (
    CACHE_MAX_AGE_HOURS,
    _forecast_age_minutes,
    _human_age,
    load_cached_forecast,
    save_forecast,
)
from storage.db import save_preferences
from web.helpers import get_session_location

bp = Blueprint("views", __name__)


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
    forecast = load_cached_forecast(loc_id)

    needs_refresh = forecast is None
    if forecast and not needs_refresh:
        age = _forecast_age_minutes(forecast)
        if age is not None and age > CACHE_MAX_AGE_HOURS * 60:
            needs_refresh = True

    if needs_refresh:
        try:
            forecast = generate_forecast(location)
            save_forecast(forecast, loc_id)
            cached_flag = None
        except Exception:
            if forecast is None:
                return render_template(
                    "error.html",
                    message="Could not load forecast. Please try refreshing later.",
                ), 500
            cached_flag = "true"

    # Apply profile-based personalization (re-rank species for this user)
    profile = _extract_profile_from_request()
    if profile:
        forecast = personalize_forecast(forecast, profile, location)

    forecast["age_human"] = _human_age(_forecast_age_minutes(forecast))

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
    current_loc = get_session_location()
    return render_template(
        "setup.html",
        results=None,
        all_locations=all_locations_sorted(),
        current_location=current_loc,
        error=None,
    )


@bp.route("/setup/search", methods=["POST"])
def setup_search() -> str:
    """Process a zip code search and show nearby locations."""
    zipcode = request.form.get("zipcode", "").strip()
    if not zipcode or not zipcode.isdigit() or len(zipcode) != 5:
        return render_template(
            "setup.html",
            results=None,
            all_locations=all_locations_sorted(),
            error="Please enter a valid 5-digit US zip code.",
        )

    coords = geocode_zip(zipcode)
    if coords is None:
        return render_template(
            "setup.html",
            results=None,
            all_locations=all_locations_sorted(),
            error=f"Could not find zip code {zipcode}. Please try another.",
        )

    lat, lng = coords
    nearby = find_nearest_locations(lat, lng, n=6)
    if not nearby:
        return render_template(
            "setup.html",
            results=None,
            all_locations=all_locations_sorted(),
            error="No supported fishing locations found within 300 miles. Try a coastal zip code.",
        )

    return render_template(
        "setup.html",
        results=nearby,
        zipcode=zipcode,
        all_locations=all_locations_sorted(),
        error=None,
    )


@bp.route("/setup/select/<location_id>", methods=["POST"])
def setup_select(location_id: str) -> Any:
    """Save the selected location and redirect to the dashboard."""
    loc = get_location(location_id)
    if loc is None:
        return redirect(url_for("views.setup"))
    session["location_id"] = location_id
    session.permanent = True
    if g.user:
        save_preferences(g.user["id"], location_id=location_id)
    return redirect(url_for("views.index"))


@bp.route("/profile")
def profile() -> str:
    """Show the fishing profile setup page."""
    return render_template("profile.html")


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
