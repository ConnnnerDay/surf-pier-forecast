"""Page view routes: dashboard, setup, profile, shared forecast."""

from __future__ import annotations

import json as _json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

import requests

from flask import (
    Blueprint,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from locations import (
    all_locations_sorted,
    find_nearby_live_cams,
    find_nearest_locations,
    geocode_zip,
    get_location,
)
from domain.forecast import generate_forecast, personalize_forecast, recompute_current_uv
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

_CAM_STATUS_TTL_SECONDS = 30 * 60
_cam_status_cache: Dict[str, Dict[str, Any]] = {}

_KT_RANGE_RE = re.compile(r"(?P<low>\d+(?:\.\d+)?)\s*-\s*(?P<high>\d+(?:\.\d+)?)\s*kt\b", re.IGNORECASE)
_KT_VALUE_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*kt\b", re.IGNORECASE)


def _convert_wind_text_units(text: str, wind_units: str) -> str:
    """Convert wind text containing kt values to the requested display units."""
    if wind_units != "mph" or not text:
        return text

    def _to_mph_range(match: re.Match[str]) -> str:
        low = round(float(match.group("low")) * 1.15078)
        high = round(float(match.group("high")) * 1.15078)
        return f"{low}-{high} mph"

    converted = _KT_RANGE_RE.sub(_to_mph_range, text)

    def _to_mph(match: re.Match[str]) -> str:
        kt = float(match.group("value"))
        mph = round(kt * 1.15078)
        return f"{mph} mph"

    return _KT_VALUE_RE.sub(_to_mph, converted)


def _apply_wind_unit_preference(forecast: Dict[str, Any], wind_units: str) -> None:
    """Mutate wind labels in a forecast to match a user's preferred wind units."""
    if wind_units != "mph":
        return

    conditions = forecast.get("conditions") or {}
    if conditions.get("wind"):
        conditions["wind"] = _convert_wind_text_units(conditions["wind"], wind_units)

    for day in forecast.get("outlook") or []:
        if day.get("wind"):
            day["wind"] = _convert_wind_text_units(day["wind"], wind_units)


def _cam_status(url: str) -> Dict[str, Any]:
    """Check whether a cam URL appears reachable, with short-lived caching."""
    now = time.time()
    cached = _cam_status_cache.get(url)
    if cached and (now - cached["checked_at_ts"]) < _CAM_STATUS_TTL_SECONDS:
        return cached

    status = {"is_live": False, "status_label": "Unavailable", "checked_at_ts": now}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SurfPierForecast/1.0)"}
    try:
        resp = requests.get(url, timeout=(2.5, 7.0), allow_redirects=True, headers=headers)
        if resp.status_code < 400:
            status["is_live"] = True
            status["status_label"] = "Live now"
        else:
            status["status_label"] = f"HTTP {resp.status_code}"
    except requests.RequestException:
        status["status_label"] = "Unavailable"

    _cam_status_cache[url] = status
    return status


def _build_live_cam_context(location: Dict[str, Any], profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build nearby live cam data and availability indicators."""

    raw_types = (profile or {}).get("fishing_types") or (profile or {}).get("fishing_type") or []
    if isinstance(raw_types, str):
        fishing_types = {t.strip().lower() for t in raw_types.split(",") if t.strip()}
    else:
        fishing_types = {str(t).strip().lower() for t in raw_types if str(t).strip()}

    include_pier_cams = (not fishing_types) or ("pier" in fishing_types)
    cams = find_nearby_live_cams(
        location["lat"],
        location["lng"],
        max_miles=15.0,
        include_pier_cams=include_pier_cams,
    )

    statuses: Dict[str, Dict[str, Any]] = {}
    if cams:
        max_workers = min(6, len(cams))
        def _safe_status(cam: Dict[str, Any]) -> Dict[str, Any]:
            try:
                return _cam_status(cam["url"])
            except Exception:
                logger.exception("cam.status_check_failed url=%s", cam.get("url"))
                return {"is_live": False, "status_label": "Unavailable"}

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for cam, status in zip(cams, pool.map(_safe_status, cams)):
                statuses[cam["url"]] = status

    enhanced_cams = []
    for cam in cams:
        entry = dict(cam)
        entry.update(statuses.get(cam["url"], {"is_live": False, "status_label": "Unavailable"}))
        enhanced_cams.append(entry)

    return {
        "nearby_live_cams": enhanced_cams,
        "live_cam_radius_miles": 15,
        "pier_cams_enabled": include_pier_cams,
    }

# Routes that are accessible without authentication.
# Keep the core forecast flow public so visitors can select a location and use
# the app without creating an account.
_PUBLIC_ENDPOINTS = {
    "views.index",
    "views.setup",
    "views.setup_search",
    "views.setup_coords",
    "views.setup_select",
    "views.live_cams",
    "views.fishing_log",
    "views.shared_forecast",
}
_PROFILE_SETUP_EXEMPT_ENDPOINTS = {
    "views.profile",
    "views.setup",
    "views.setup_search",
    "views.setup_coords",
    "views.setup_select",
    "views.setup_favorite",
    "auth.logout",
    "auth.account",
    "auth.account_settings",
}


def _user_requires_profile_setup() -> bool:
    """Return True when a logged-in user has picked a location but no profile."""
    if g.user is None:
        return False
    prefs = get_preferences(g.user["id"])
    has_location = bool((prefs.get("location_id") or session.get("location_id") or "").strip())
    has_profile = bool(prefs.get("fishing_profile"))
    return has_location and not has_profile


@bp.before_request
def _require_login() -> Any:
    """Redirect unauthenticated users to the registration page.

    Shareable /f/<id> links remain public so they can be shared freely.
    When no authenticated user is present we also clear any stale
    location_id that may have been left in the session from a previous
    login, so it cannot bleed across accounts.
    """
    if request.endpoint is None:
        return

    if request.endpoint in _PUBLIC_ENDPOINTS:
        if g.user is not None and request.endpoint not in _PROFILE_SETUP_EXEMPT_ENDPOINTS and _user_requires_profile_setup():
            return redirect(url_for("views.profile"))
        return

    if g.user is None:
        # Clear stale per-user state from the cookie.
        session.pop("location_id", None)
        return redirect(url_for("auth.landing"))
    if request.endpoint not in _PROFILE_SETUP_EXEMPT_ENDPOINTS and _user_requires_profile_setup():
        return redirect(url_for("views.profile"))


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
            logger.exception("forecast.generate_failed location_id=%s", loc_id)
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
    user_prefs: Dict[str, Any] = {}
    stored_profile: Dict[str, Any] = {}
    if g.user:
        user_prefs = get_preferences(g.user["id"])
        stored_profile = user_prefs.get("fishing_profile") or {}

    profile = _extract_profile_from_request()
    if not profile and (stored_profile.get("fishing_types") or stored_profile.get("targets")):
        profile = stored_profile
    if profile:
        forecast = personalize_forecast(forecast, profile, location)

    _apply_wind_unit_preference(forecast, user_prefs.get("wind_units", "knots"))

    forecast.update(_build_live_cam_context(location, profile))

    forecast["age_human"] = _human_age(_forecast_age_minutes(forecast))
    # Backfill for cached forecasts created before these fields existed.
    if not forecast.get("location_id"):
        forecast["location_id"] = loc_id
    if not forecast.get("location_state"):
        forecast["location_state"] = location.get("state", "")
    # tide_chart was stored as a JSON string in older cache entries; parse it
    # back to a dict so the template can access fields directly.
    tc = forecast.get("tide_chart")
    if isinstance(tc, str) and tc:
        try:
            forecast["tide_chart"] = _json.loads(tc)
        except Exception:
            forecast.pop("tide_chart", None)

    # Always recompute UV for the current time at the selected location so the
    # displayed value reflects *now*, not the moment the forecast was cached.
    forecast["uv"] = recompute_current_uv(location)

    client_profile = dict(stored_profile)
    if profile:
        client_profile.update(profile)

    return render_template(
        "index.html",
        forecast=forecast,
        cached=cached_flag,
        share_id=loc_id,
        profile=client_profile,
    )


@bp.route("/")
def index() -> str:
    """Render the dashboard with the current forecast."""
    location = get_session_location()
    if location is None:
        return redirect(url_for("views.setup"))

    cached_flag = request.args.get("cached")
    return _render_forecast(location, cached_flag)




@bp.route("/live-cams")
def live_cams() -> str:
    """Render the dedicated live cams page for the selected location."""
    location = get_session_location()
    if location is None:
        return redirect(url_for("views.setup"))

    profile = _extract_profile_from_request()
    if not profile and g.user:
        stored = get_preferences(g.user["id"]).get("fishing_profile") or {}
        if stored.get("fishing_types") or stored.get("targets"):
            profile = stored

    cam_context = _build_live_cam_context(location, profile)
    return render_template("live_cams.html", location=location, **cam_context)


@bp.route("/fishing-log")
def fishing_log() -> str:
    """Render the dedicated fishing log page for the selected location."""
    location = get_session_location()
    if location is None:
        return redirect(url_for("views.setup"))
    return render_template("fishing_log.html", location=location)


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


@bp.route("/setup/coords", methods=["POST"])
def setup_coords() -> Any:
    """Accept lat/lon from the map picker and show nearby locations."""
    raw_lat = request.form.get("location_lat", "").strip()
    raw_lon = request.form.get("location_lon", "").strip()
    try:
        lat = float(raw_lat)
        lon = float(raw_lon)
    except (ValueError, TypeError):
        return render_template("setup.html", **_setup_context(
            error="Invalid coordinates. Please click the map to set your location.",
        ))

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return render_template("setup.html", **_setup_context(
            error="Coordinates out of range. Please click the map again.",
        ))

    nearby = find_nearest_locations(lat, lon, n=6)
    if not nearby:
        return render_template("setup.html", **_setup_context(
            error="No supported fishing locations found within 300 miles of that point. Try a coastal area.",
        ))

    return render_template("setup.html", **_setup_context(results=nearby))


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
        if _user_requires_profile_setup():
            return redirect(url_for("views.profile"))
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
    # Only allow same-origin relative paths. Block // (protocol-relative) and
    # backslash tricks (/\evil.com) that Chrome/Edge normalise to external URLs.
    if next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url:
        return redirect(next_url)
    return redirect(url_for("views.setup"))


@bp.route("/profile")
def profile() -> str:
    """Show the fishing profile setup page."""
    if g.user is None:
        return redirect(url_for("auth.login"))
    prefs = get_preferences(g.user["id"])
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
