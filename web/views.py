"""Page view routes: dashboard, setup, profile, shared forecast."""

from __future__ import annotations

import json as _json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import urlparse

import requests

_CAM_CHECK_TIMEOUT: tuple[float, float] = (2.5, 7.0)

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
from domain.forecast import (
    generate_forecast,
    personalize_forecast,
    recompute_current_uv,
    build_trip_setup,
)
from services.forecast_refresh import enqueue_forecast_refresh, is_refreshing as _is_refreshing
from services.nws import _KT_TO_MPH
from storage.cache import (
    CACHE_MAX_AGE_HOURS,
    _forecast_age_minutes,
    _human_age,
    load_cached_forecast,
    save_forecast,
)
from storage.sqlite import save_preferences, get_log_stats
from web.helpers import get_session_location, get_prefs_cached
from web.rate_limit import (
    is_rate_limited as _rl_is_rate_limited,
    record_attempt as _rl_record_attempt,
)
from regulations import get_official_regulations_url as _get_official_regulations_url

bp = Blueprint("views", __name__)
logger = logging.getLogger(__name__)


# -- Setup endpoint rate limiting --------------------------------------------
# Zip-code lookup and coordinate searches hit external geocoding services.
# Limit each IP to 30 requests per 10 minutes to prevent abuse.
_SETUP_RATE_LIMIT_MAX = 30
_SETUP_RATE_LIMIT_WINDOW_S = 10 * 60
_setup_rate_limit_store: dict[str, tuple[float, int]] = {}
_setup_rate_limit_lock = threading.Lock()


def _setup_is_rate_limited() -> bool:
    if _rl_is_rate_limited(
        _setup_rate_limit_store, _setup_rate_limit_lock,
        _SETUP_RATE_LIMIT_MAX, _SETUP_RATE_LIMIT_WINDOW_S,
    ):
        return True
    _rl_record_attempt(_setup_rate_limit_store, _setup_rate_limit_lock, _SETUP_RATE_LIMIT_WINDOW_S)
    return False


# -- Camera status cache -----------------------------------------------------
_CAM_STATUS_TTL_SECONDS = 30 * 60
_CAM_STATUS_CACHE_MAX = 500  # prevent unbounded growth with many unique URLs
_CAM_CHECK_POOL_WORKERS = 6
_cam_status_cache: dict[str, dict[str, Any]] = {}
_cam_status_lock = threading.Lock()
# Shared daemon pool for background cam probes — never blocks a WSGI worker.
_cam_check_pool = ThreadPoolExecutor(max_workers=_CAM_CHECK_POOL_WORKERS, thread_name_prefix="cam-check")
_CAM_STATUS_UNKNOWN: dict[str, Any] = {"is_live": False, "status_label": "Checking…"}

_KT_RANGE_RE = re.compile(
    r"(?P<low>\d+(?:\.\d+)?)\s*-\s*(?P<high>\d+(?:\.\d+)?)\s*kt\b", re.IGNORECASE
)
_KT_VALUE_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*kt\b", re.IGNORECASE)


def _convert_wind_text_units(text: str, wind_units: str) -> str:
    """Convert wind text containing kt values to the requested display units."""
    if wind_units != "mph" or not text:
        return text

    def _to_mph_range(match: re.Match[str]) -> str:
        low = round(float(match.group("low")) * _KT_TO_MPH)
        high = round(float(match.group("high")) * _KT_TO_MPH)
        return f"{low}-{high} mph"

    converted = _KT_RANGE_RE.sub(_to_mph_range, text)

    def _to_mph(match: re.Match[str]) -> str:
        kt = float(match.group("value"))
        mph = round(kt * _KT_TO_MPH)
        return f"{mph} mph"

    return _KT_VALUE_RE.sub(_to_mph, converted)


def _apply_wind_unit_preference(forecast: dict[str, Any], wind_units: str) -> None:
    """Update wind labels in a forecast to match a user's preferred wind units.

    Operates on isolated copies of the conditions dict and each outlook day so
    the in-process caches (_MEM_CACHE, _PERSONALIZE_CACHE) are never mutated.
    """
    if wind_units != "mph":
        return

    conditions = forecast.get("conditions")
    if conditions and conditions.get("wind"):
        conditions = dict(conditions)
        conditions["wind"] = _convert_wind_text_units(conditions["wind"], wind_units)
        forecast["conditions"] = conditions

    if forecast.get("outlook"):
        new_outlook = []
        for day in forecast["outlook"]:
            if day.get("wind"):
                day = dict(day)
                day["wind"] = _convert_wind_text_units(day["wind"], wind_units)
            new_outlook.append(day)
        forecast["outlook"] = new_outlook


def _fetch_cam_status(url: str) -> None:
    """Probe a single cam URL and write the result into ``_cam_status_cache``.

    Runs in the background thread pool so it never blocks a WSGI worker.
    """
    now = time.time()
    status: dict[str, Any] = {
        "is_live": False,
        "status_label": "Unavailable",
        "checked_at_ts": now,
    }
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SurfPierForecast/1.0)"}
    try:
        resp = requests.get(
            url,
            timeout=_CAM_CHECK_TIMEOUT,
            # Disable redirect following: the cam URLs are hardcoded, so
            # redirects are unexpected and could lead to unintended hosts.
            allow_redirects=False,
            headers=headers,
        )
        if resp.status_code < 400:
            status["is_live"] = True
            status["status_label"] = "Live now"
        # Do not surface the raw HTTP status code from the external site —
        # it leaks information about third-party infrastructure.
    except requests.RequestException:
        pass
    with _cam_status_lock:
        # Evict the oldest entry when the cache is full to prevent unbounded growth.
        if (
            url not in _cam_status_cache
            and len(_cam_status_cache) >= _CAM_STATUS_CACHE_MAX
        ):
            oldest = min(
                _cam_status_cache,
                key=lambda u: _cam_status_cache[u].get("checked_at_ts", 0),
            )
            del _cam_status_cache[oldest]
        _cam_status_cache[url] = status


def _cam_status_cached(url: str) -> dict[str, Any]:
    """Return the cached cam status, scheduling a background refresh if stale.

    Never blocks — always returns immediately.  The first call for a URL
    returns ``_CAM_STATUS_UNKNOWN`` ("Checking…") while the probe runs; the
    next page load will see real data.
    """
    now = time.time()
    with _cam_status_lock:
        cached = _cam_status_cache.get(url)

    if (
        cached is None
        or (now - cached.get("checked_at_ts", 0)) >= _CAM_STATUS_TTL_SECONDS
    ):
        _cam_check_pool.submit(_fetch_cam_status, url)

    return cached or _CAM_STATUS_UNKNOWN


def _build_live_cam_context(
    location: dict[str, Any], profile: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Build nearby live cam data.

    Returns cached availability immediately; stale/unseen URLs trigger a
    background re-probe so the next load gets fresh data without blocking
    the current request.
    """
    raw_types = (
        (profile or {}).get("fishing_types")
        or (profile or {}).get("fishing_type")
        or []
    )
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

    enhanced_cams = []
    for cam in cams:
        entry = dict(cam)
        entry.update(_cam_status_cached(cam["url"]))
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
    prefs = get_prefs_cached(g.user["id"])
    has_location = bool(
        (prefs.get("location_id") or session.get("location_id") or "").strip()
    )
    fp = prefs.get("fishing_profile")
    if isinstance(fp, dict) and fp:
        # Legacy profiles lack a ``completed`` key — treat them as complete so
        # existing users are not permanently bounced to the profile wizard.
        has_profile = bool(fp.get("completed", True))
    else:
        has_profile = bool(fp)
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
        if (
            g.user is not None
            and request.endpoint not in _PROFILE_SETUP_EXEMPT_ENDPOINTS
            and _user_requires_profile_setup()
        ):
            return redirect(url_for("views.profile"))
        return

    if g.user is None:
        # Clear stale per-user state from the cookie.
        session.pop("location_id", None)
        return redirect(url_for("auth.landing"))
    if (
        request.endpoint not in _PROFILE_SETUP_EXEMPT_ENDPOINTS
        and _user_requires_profile_setup()
    ):
        return redirect(url_for("views.profile"))


def _setup_context(**kwargs: Any) -> dict[str, Any]:
    """Build common template context for the setup page."""
    current_loc = get_session_location()
    favorite_ids = []
    favorite_locations = []
    if g.user:
        prefs = get_prefs_cached(g.user["id"])
        favorite_locations = [
            loc
            for loc_id in prefs.get("favorites", [])
            for loc in (get_location(loc_id),)
            if loc is not None
        ]
        favorite_ids = [loc["id"] for loc in favorite_locations]

    context: dict[str, Any] = {
        "results": None,
        "all_locations": all_locations_sorted(),
        "current_location": current_loc,
        "error": None,
        "favorite_ids": favorite_ids,
        "favorite_locations": favorite_locations,
    }
    context.update(kwargs)
    return context


_PROFILE_PARAM_MAX_LEN = 200  # per query-string parameter (chars)
_PROFILE_PARAM_MAX_ITEMS = 20  # max comma-separated values per parameter


_VALID_EXPERIENCE_PARAMS = frozenset({"beginner", "intermediate", "experienced"})
_VALID_BAIT_PARAMS = frozenset({"yes", "sometimes", "no"})


def _extract_profile_from_request() -> Optional[dict[str, Any]]:
    """Extract fishing profile from query parameters.

    Expected params: fishing_types (comma-separated), targets (comma-separated),
    experience, live_bait, cut_bait, lures.
    Returns None if no profile params are present.
    """
    # Cap raw values before any processing to prevent DoS via enormous inputs.
    ft = request.args.get("fishing_types", "")[:_PROFILE_PARAM_MAX_LEN].strip()
    tg = request.args.get("targets", "")[:_PROFILE_PARAM_MAX_LEN].strip()
    exp = request.args.get("experience", "")[:20].strip()
    live_bait = request.args.get("live_bait", "")[:20].strip()
    cut_bait = request.args.get("cut_bait", "")[:20].strip()
    lures = request.args.get("lures", "")[:20].strip()

    if not ft and not tg and not exp and not live_bait and not cut_bait and not lures:
        return None

    profile: dict[str, Any] = {}
    if ft:
        profile["fishing_types"] = [t.strip() for t in ft.split(",") if t.strip()][
            :_PROFILE_PARAM_MAX_ITEMS
        ]
    if tg:
        profile["targets"] = [t.strip() for t in tg.split(",") if t.strip()][
            :_PROFILE_PARAM_MAX_ITEMS
        ]
    if exp in _VALID_EXPERIENCE_PARAMS:
        profile["experience"] = exp
    if live_bait in _VALID_BAIT_PARAMS:
        profile["live_bait"] = live_bait
    if cut_bait in _VALID_BAIT_PARAMS:
        profile["cut_bait"] = cut_bait
    if lures in _VALID_BAIT_PARAMS:
        profile["lures"] = lures
    return profile


def _render_forecast(
    location: dict[str, Any], cached_flag: Optional[str] = None
) -> Any:
    """Load (or refresh) the forecast for a location and render the dashboard."""
    loc_id = location["id"]
    forecast = load_cached_forecast(loc_id, user_id=None, include_stale=True)

    is_stale = False
    if forecast:
        age = _forecast_age_minutes(forecast)
        is_stale = bool(age is not None and age > CACHE_MAX_AGE_HOURS * 60)

    if forecast is None:
        # If a background job is already generating this forecast (e.g. pre-warmed
        # by setup_select), show a lightweight polling page instead of blocking the
        # request thread for 15-20 s.
        if _is_refreshing(loc_id):
            logger.info("cache.miss.background_running location_id=%s", loc_id)
            status_url = url_for("api.forecast_status_v1", location_id=loc_id)
            dest_url = request.url
            return render_template(
                "forecast_loading.html",
                status_url=status_url,
                dest_url=dest_url,
            )
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
    user_prefs: dict[str, Any] = {}
    stored_profile: dict[str, Any] = {}
    if g.user:
        user_prefs = get_prefs_cached(g.user["id"])
        stored_profile = user_prefs.get("fishing_profile") or {}

    profile = _extract_profile_from_request()
    if not profile and any(
        stored_profile.get(k)
        for k in (
            "fishing_types",
            "targets",
            "experience",
            "live_bait",
            "cut_bait",
            "lures",
        )
    ):
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
    if not forecast.get("official_regulations_url"):
        _st = forecast.get("location_state") or location.get("state", "")
        if _st:
            forecast["official_regulations_url"] = _get_official_regulations_url(_st)
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

    trip_setup = build_trip_setup(forecast, client_profile or None)

    # Build favorite locations list for the quick-switch bar.
    favorite_locations = []
    caught_species: set[str] = set()
    if g.user:
        fav_ids = user_prefs.get("favorites") or []
        for fav_id in fav_ids:
            fav_loc = get_location(fav_id)
            if fav_loc:
                favorite_locations.append({"id": fav_id, "name": fav_loc["name"]})

        # Collect species the user has logged at this location for badge display.
        try:
            log_stats = get_log_stats(g.user["id"], loc_id)
            caught_species = {
                entry["species"].lower()
                for entry in log_stats.get("species_breakdown", [])
            }
        except Exception:
            pass

    profile_incomplete = bool(
        g.user
        and not stored_profile.get("completed")
        and not stored_profile.get("fishing_types")
    )

    loc_lat = location.get("lat", 0)
    loc_lng = location.get("lng", 0)

    return render_template(
        "index.html",
        forecast=forecast,
        cached=cached_flag,
        share_id=loc_id,
        profile=client_profile,
        trip_setup=trip_setup,
        favorite_locations=favorite_locations,
        caught_species=caught_species,
        location_lat=loc_lat,
        location_lng=loc_lng,
        profile_incomplete=profile_incomplete,
    )


@bp.route("/")
def index() -> Any:
    """Render the dashboard with the current forecast."""
    location = get_session_location()
    if location is None:
        # Send unauthenticated visitors to login/register first,
        # not directly to location setup.
        if g.user is None:
            return redirect(url_for("auth.landing"))
        return redirect(url_for("views.setup"))

    # Whitelist the cached= flag to its only known values so arbitrary strings
    # are never forwarded into the template context.
    _raw_cached = request.args.get("cached", "")
    cached_flag = "refreshing" if _raw_cached == "refreshing" else None
    return _render_forecast(location, cached_flag)


@bp.route("/live-cams")
def live_cams() -> Any:
    """Render the dedicated live cams page for the selected location."""
    location = get_session_location()
    if location is None:
        return redirect(url_for("views.setup"))

    profile = _extract_profile_from_request()
    if not profile and g.user:
        stored = get_prefs_cached(g.user["id"]).get("fishing_profile") or {}
        if stored.get("fishing_types") or stored.get("targets"):
            profile = stored

    cam_context = _build_live_cam_context(location, profile)
    return render_template("live_cams.html", location=location, **cam_context)


@bp.route("/fishing-log")
def fishing_log() -> Any:
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
    if _setup_is_rate_limited():
        return render_template(
            "setup.html",
            **_setup_context(
                error="Too many searches. Please wait a few minutes and try again."
            ),
        )
    zipcode = request.form.get("zipcode", "").strip()
    if not zipcode or not zipcode.isdigit() or len(zipcode) != 5:
        return render_template(
            "setup.html",
            **_setup_context(
                error="Please enter a valid 5-digit US zip code.",
                zipcode=zipcode,
            ),
        )

    coords = geocode_zip(zipcode)
    if coords is None:
        return render_template(
            "setup.html",
            **_setup_context(
                error=f"Could not find zip code {zipcode}. Please try another.",
                zipcode=zipcode,
            ),
        )

    lat, lng = coords
    nearby = find_nearest_locations(lat, lng, n=6)
    if not nearby:
        return render_template(
            "setup.html",
            **_setup_context(
                error="No supported fishing locations found within 300 miles. Try a coastal zip code.",
                zipcode=zipcode,
            ),
        )

    return render_template(
        "setup.html", **_setup_context(results=nearby, zipcode=zipcode)
    )


@bp.route("/setup/coords", methods=["POST"])
def setup_coords() -> Any:
    """Accept lat/lon from the map picker and show nearby locations."""
    if _setup_is_rate_limited():
        return render_template(
            "setup.html",
            **_setup_context(
                error="Too many searches. Please wait a few minutes and try again."
            ),
        )
    raw_lat = request.form.get("location_lat", "").strip()
    raw_lon = request.form.get("location_lon", "").strip()
    try:
        lat = float(raw_lat)
        lon = float(raw_lon)
    except (ValueError, TypeError):
        return render_template(
            "setup.html",
            **_setup_context(
                error="Invalid coordinates. Please click the map to set your location.",
            ),
        )

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return render_template(
            "setup.html",
            **_setup_context(
                error="Coordinates out of range. Please click the map again.",
            ),
        )

    nearby = find_nearest_locations(lat, lon, n=6)
    if not nearby:
        return render_template(
            "setup.html",
            **_setup_context(
                error="No supported fishing locations found within 300 miles of that point. Try a coastal area.",
            ),
        )

    return render_template("setup.html", **_setup_context(results=nearby))


@bp.route("/setup/select/<location_id>", methods=["POST"])
def setup_select(location_id: str) -> Any:
    """Save the selected location and redirect to the dashboard."""
    loc = get_location(location_id)
    if loc is None:
        return redirect(url_for("views.setup"))
    session["location_id"] = location_id
    session.permanent = True
    enqueue_forecast_refresh(location_id, user_id=None)
    if g.user:
        save_preferences(
            g.user["id"], location_id=location_id, default_location_id=location_id
        )
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

    prefs = get_prefs_cached(g.user["id"])
    favorites = [
        loc_id for loc_id in prefs.get("favorites", []) if get_location(loc_id)
    ]
    if location_id in favorites:
        favorites = [loc_id for loc_id in favorites if loc_id != location_id]
    else:
        favorites.append(location_id)
    save_preferences(g.user["id"], favorites=favorites)

    next_url = request.form.get("next", "")
    # Only redirect to same-origin relative paths.  Use urlparse to reject
    # anything with a scheme ("http:") or authority ("//evil.com"), which
    # covers URL-encoded variants, protocol-relative URLs, and backslash
    # tricks that browsers normalise to external navigations.
    _parsed = urlparse(next_url)
    if (
        next_url
        and not _parsed.scheme
        and not _parsed.netloc
        and next_url.startswith("/")
    ):
        return redirect(next_url)
    return redirect(url_for("views.setup"))


@bp.route("/profile")
def profile() -> Any:
    """Show the fishing profile setup page."""
    if g.user is None:
        return redirect(url_for("auth.login"))
    prefs = get_prefs_cached(g.user["id"])
    return render_template("profile.html", prefs=prefs)


@bp.route("/f/<location_id>")
def shared_forecast(location_id: str) -> Any:
    """View a forecast for a specific location via shareable link."""
    location = get_location(location_id)
    if location is None:
        return render_template(
            "error.html",
            message="Location not found. It may have been removed.",
        ), 404

    session["location_id"] = location_id
    return _render_forecast(location)
