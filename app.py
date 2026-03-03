"""
Surf and Pier Fishing Forecast Application
----------------------------------------

Flask app that generates a 24-hour surf and pier fishing forecast for 100+
coastal locations.  Users select their location on first visit.  Fetches
marine conditions from the NWS API, water temperature from NOAA CO-OPS, and
buoy data from NDBC, then dynamically determines which species are likely
biting based on season, water temperature, and solunar conditions.  Rig
recommendations are matched to the active species.

Endpoints:
* ``/``              -- HTML dashboard (redirects to /setup if no location)
* ``/setup``         -- Location picker
* ``/f/<loc_id>``    -- Shareable forecast link
* ``/api/forecast``  -- Current forecast as JSON
* ``/api/refresh``   -- POST to regenerate forecast

No API keys required.  Data cached per-location to ``data/``.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Dict, Optional

from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from locations import (
    all_locations_sorted,
    find_nearest_locations,
    geocode_zip,
    get_location,
)
import db as userdb

from domain.forecast import generate_forecast, personalize_forecast, build_share_text
from storage.cache import (
    load_cached_forecast,
    save_forecast,
    CACHE_MAX_AGE_HOURS,
    _forecast_age_minutes,
    _human_age,
)


# Set up Flask app
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key-change-in-production")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)

# Initialize user database
userdb.init_db()


# -- Session helpers --------------------------------------------------------

def _get_session_location() -> Optional[Dict[str, Any]]:
    """Return the location config from the user's session, or None.

    For logged-in users, falls back to their saved location preference
    if the session doesn't have a location set.
    """
    loc_id = session.get("location_id")
    if not loc_id and getattr(g, "user", None):
        prefs = userdb.get_preferences(g.user["id"])
        loc_id = prefs.get("location_id")
        if loc_id:
            session["location_id"] = loc_id
    if loc_id:
        return get_location(loc_id)
    return None


# -- Auth helpers -----------------------------------------------------------

@app.before_request
def _load_user() -> None:
    """Populate g.user from the session on every request."""
    user_id = session.get("user_id")
    if user_id:
        g.user = userdb.get_user(user_id)
        if g.user is None:
            # Stale session — user was deleted
            session.pop("user_id", None)
    else:
        g.user = None


@app.context_processor
def _inject_user() -> Dict[str, Any]:
    """Make ``user`` available in every template."""
    return {"user": getattr(g, "user", None)}


# -- Auth routes ------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login() -> Any:
    """Log-in page and form handler."""
    if request.method == "GET":
        return render_template("login.html", error=None)
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or not password:
        return render_template("login.html", error="Please enter both fields.",
                               username=username)
    user = userdb.authenticate_user(username, password)
    if user is None:
        return render_template("login.html", error="Invalid username or password.",
                               username=username)
    session["user_id"] = user["id"]
    session.permanent = True
    # Restore saved location preference
    prefs = userdb.get_preferences(user["id"])
    if prefs.get("location_id"):
        session["location_id"] = prefs["location_id"]
    return redirect(url_for("index"))


@app.route("/register", methods=["GET", "POST"])
def register() -> Any:
    """Registration page and form handler."""
    if request.method == "GET":
        return render_template("register.html", error=None)
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    if not username or not password:
        return render_template("register.html", error="Please fill in all fields.",
                               username=username)
    if len(username) < 2 or len(username) > 30:
        return render_template("register.html",
                               error="Username must be 2-30 characters.",
                               username=username)
    if len(password) < 4:
        return render_template("register.html",
                               error="Password must be at least 4 characters.",
                               username=username)
    if password != confirm:
        return render_template("register.html", error="Passwords do not match.",
                               username=username)
    user_id = userdb.create_user(username, password)
    if user_id is None:
        return render_template("register.html",
                               error="That username is already taken.",
                               username=username)
    session["user_id"] = user_id
    session.permanent = True
    # Carry over current location if one is set
    loc_id = session.get("location_id")
    if loc_id:
        userdb.save_preferences(user_id, location_id=loc_id)
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout() -> Any:
    """Log out the current user."""
    session.pop("user_id", None)
    return redirect(url_for("index"))


@app.route("/account")
def account() -> str:
    """Account settings page for logged-in users."""
    if g.user is None:
        return redirect(url_for("login"))
    prefs = userdb.get_preferences(g.user["id"])
    loc = None
    if prefs.get("location_id"):
        loc = get_location(prefs["location_id"])
    return render_template("account.html", prefs=prefs, saved_location=loc)


# -- Preference & log sync API ---------------------------------------------

@app.route("/api/preferences", methods=["GET", "POST"])
def api_preferences() -> Any:
    """Get or update preferences for the logged-in user."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    uid = g.user["id"]
    if request.method == "GET":
        return jsonify(userdb.get_preferences(uid))
    data = request.get_json(silent=True) or {}
    allowed = {"location_id", "theme", "units", "fishing_profile", "favorites"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if updates:
        userdb.save_preferences(uid, **updates)
        # Keep session location in sync
        if "location_id" in updates and updates["location_id"]:
            session["location_id"] = updates["location_id"]
    return jsonify({"ok": True})


@app.route("/api/log", methods=["GET", "POST"])
def api_log() -> Any:
    """Get or add fishing log entries for the logged-in user."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    uid = g.user["id"]
    loc_id = request.args.get("location") or session.get("location_id", "")
    if request.method == "GET":
        entries = userdb.get_log_entries(uid, loc_id)
        stats = userdb.get_log_stats(uid, loc_id) if loc_id else {}
        return jsonify({"entries": entries, "stats": stats})
    data = request.get_json(silent=True) or {}
    species = data.get("species", "").strip()
    if not species or not loc_id:
        return jsonify({"error": "species and location required"}), 400
    entry_id = userdb.add_log_entry(
        uid, loc_id, species,
        size=data.get("size", ""),
        notes=data.get("notes", ""),
    )
    return jsonify({"ok": True, "id": entry_id}), 201


@app.route("/api/log/<int:entry_id>", methods=["DELETE"])
def api_log_delete(entry_id: int) -> Any:
    """Delete a fishing log entry."""
    if g.user is None:
        return jsonify({"error": "Not logged in"}), 401
    deleted = userdb.delete_log_entry(g.user["id"], entry_id)
    if not deleted:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


# -- Setup routes -----------------------------------------------------------

@app.route("/setup")
def setup() -> str:
    """Show the location setup page (zip code entry or browse)."""
    current_loc = _get_session_location()
    return render_template(
        "setup.html",
        results=None,
        all_locations=all_locations_sorted(),
        current_location=current_loc,
        error=None,
    )


@app.route("/setup/search", methods=["POST"])
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


@app.route("/setup/select/<location_id>", methods=["POST"])
def setup_select(location_id: str) -> Any:
    """Save the selected location to the session and redirect to forecast.

    If the ``first_visit`` query parameter is set, redirect to the profile
    setup page instead so the user can configure their fishing preferences.
    """
    loc = get_location(location_id)
    if loc is None:
        return redirect(url_for("setup"))
    session["location_id"] = location_id
    session.permanent = True
    # Persist for logged-in users
    if g.user:
        userdb.save_preferences(g.user["id"], location_id=location_id)
    return redirect(url_for("index"))


@app.route("/profile")
def profile() -> str:
    """Show the fishing profile setup page."""
    return render_template("profile.html")


# -- Main routes ------------------------------------------------------------

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


@app.route("/")
def index() -> str:
    """Render the dashboard with the current forecast.

    If no location is set, redirect to the setup page.
    If the cached forecast is stale, auto-refresh it.
    If profile query params are present, personalize the forecast.
    """
    location = _get_session_location()
    if location is None:
        return redirect(url_for("setup"))

    loc_id = location["id"]
    cached_flag = request.args.get("cached")
    forecast = load_cached_forecast(loc_id)

    # Auto-refresh if cache is missing or stale
    needs_refresh = forecast is None
    if forecast and not needs_refresh:
        age = _forecast_age_minutes(forecast)
        if age is not None and age > CACHE_MAX_AGE_HOURS * 60:
            needs_refresh = True

    if needs_refresh:
        try:
            forecast = generate_forecast(location)
            save_forecast(forecast, loc_id)
            cached_flag = None  # Fresh data
        except Exception:
            if forecast is None:
                return render_template(
                    "error.html",
                    message="Could not load forecast. Please try refreshing later.",
                ), 500
            # Fall through to serve stale cache
            cached_flag = "true"

    # Apply profile-based personalization (re-rank species for this user)
    profile = _extract_profile_from_request()
    if profile:
        forecast = personalize_forecast(forecast, profile, location)

    # Attach human-readable age for the template
    forecast["age_human"] = _human_age(_forecast_age_minutes(forecast))

    return render_template("index.html", forecast=forecast, cached=cached_flag,
                           share_id=loc_id)


@app.route("/api/forecast")
def api_forecast() -> Any:
    """Return the current forecast as JSON."""
    location = _get_session_location()
    loc_id = location["id"] if location else ""
    forecast = load_cached_forecast(loc_id)
    if forecast:
        return jsonify(forecast)
    return jsonify({"error": "No forecast available"}), 503


@app.route("/api/refresh", methods=["POST"])
def api_refresh() -> Any:
    """Trigger generation of a new forecast."""
    location = _get_session_location()
    if location is None:
        return redirect(url_for("setup"))
    try:
        new_forecast = generate_forecast(location)
        save_forecast(new_forecast, location["id"])
        return redirect(url_for("index"))
    except Exception as exc:
        print(f"Error refreshing forecast: {exc}")
        return redirect(url_for("index", cached="true"))


# -- Shareable forecast route -----------------------------------------------

@app.route("/f/<location_id>")
def shared_forecast(location_id: str) -> str:
    """View a forecast for a specific location via shareable link.

    This route doesn't require a session — anyone with the link can view the
    forecast.  It also sets the viewer's session to this location so they can
    continue browsing.
    """
    location = get_location(location_id)
    if location is None:
        return render_template(
            "error.html",
            message="Location not found. It may have been removed.",
        ), 404

    # Set the viewer's session to this location
    session["location_id"] = location_id

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
        except Exception:
            if forecast is None:
                return render_template(
                    "error.html",
                    message="Could not load forecast for this location.",
                ), 500

    # Apply profile-based personalization
    profile = _extract_profile_from_request()
    if profile:
        forecast = personalize_forecast(forecast, profile, location)

    forecast["age_human"] = _human_age(_forecast_age_minutes(forecast))

    return render_template("index.html", forecast=forecast, cached=None,
                           share_id=location_id)


@app.route("/api/share-text")
def api_share_text() -> Any:
    """Return a plain-text forecast summary for copy/paste sharing."""
    location = _get_session_location()
    loc_id = location["id"] if location else ""
    forecast = load_cached_forecast(loc_id)
    if not forecast:
        return jsonify({"error": "No forecast available"}), 503
    text = build_share_text(forecast)
    return jsonify({"text": text, "location_id": loc_id})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5757))
    app.run(host="0.0.0.0", port=port)
