"""Authentication routes: login, register, logout, account."""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, Tuple

from flask import (
    Blueprint,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from locations import get_location
from storage.db import (
    authenticate_user,
    create_user,
    get_preferences,
    get_recent_logs,
    save_preferences,
)

bp = Blueprint("auth", __name__)

_LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10
_LOGIN_RATE_LIMIT_WINDOW_S = 15 * 60

_REGISTER_RATE_LIMIT_MAX_ATTEMPTS = 5
_REGISTER_RATE_LIMIT_WINDOW_S = 60 * 60  # 1 hour

# ---------------------------------------------------------------------------
# Server-side IP-keyed rate limiting for the login and registration endpoints.
#
# Keying on the client IP prevents circumvention by clearing cookies or making
# requests without a session.
#
# X-Forwarded-For is only trusted when TRUSTED_PROXY=1 is set in the
# environment (i.e. the app is explicitly deployed behind a reverse proxy).
# Without that flag, request.remote_addr is used directly, preventing an
# attacker from spoofing arbitrary IPs to bypass rate limits.
#
# Data structure: {ip: (window_start_ts, attempt_count)}
# A single lock guards all reads and writes.
# ---------------------------------------------------------------------------
_rate_limit_store: Dict[str, Tuple[float, int]] = {}
_rate_limit_lock = threading.Lock()

_register_rate_limit_store: Dict[str, Tuple[float, int]] = {}
_register_rate_limit_lock = threading.Lock()

# Only trust X-Forwarded-For when running behind a known reverse proxy.
_TRUST_PROXY = os.environ.get("TRUSTED_PROXY", "").strip() == "1"


def _client_ip() -> str:
    """Return the best-effort client IP.

    X-Forwarded-For is only honoured when the app is explicitly configured to
    run behind a trusted reverse proxy (``TRUSTED_PROXY=1``).  Without that
    flag, blindly reading X-Forwarded-For would let any client forge a
    different IP on every request and trivially bypass IP-based rate limiting.
    """
    if _TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # Take the left-most entry — the original client IP.
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _is_rate_limited(
    store: Dict[str, Tuple[float, int]],
    lock: threading.Lock,
    max_attempts: int,
    window_s: float,
) -> bool:
    """Return True if the current client IP has exceeded the given rate limit."""
    ip = _client_ip()
    now = time.time()
    with lock:
        start, attempts = store.get(ip, (now, 0))
        if now - start > window_s:
            store[ip] = (now, 0)
            return False
        return attempts >= max_attempts


def _record_attempt(
    store: Dict[str, Tuple[float, int]],
    lock: threading.Lock,
    window_s: float,
) -> None:
    """Increment the attempt counter for the current client IP."""
    ip = _client_ip()
    now = time.time()
    with lock:
        start, attempts = store.get(ip, (now, 0))
        if now - start > window_s:
            store[ip] = (now, 1)
        else:
            store[ip] = (start, attempts + 1)


def _clear_attempts(
    store: Dict[str, Tuple[float, int]],
    lock: threading.Lock,
) -> None:
    """Clear the attempt counter for the current client IP."""
    ip = _client_ip()
    with lock:
        store.pop(ip, None)


def _login_is_rate_limited() -> bool:
    return _is_rate_limited(
        _rate_limit_store, _rate_limit_lock,
        _LOGIN_RATE_LIMIT_MAX_ATTEMPTS, _LOGIN_RATE_LIMIT_WINDOW_S,
    )


def _record_login_failure() -> None:
    _record_attempt(_rate_limit_store, _rate_limit_lock, _LOGIN_RATE_LIMIT_WINDOW_S)


def _clear_login_failures() -> None:
    _clear_attempts(_rate_limit_store, _rate_limit_lock)


def _register_is_rate_limited() -> bool:
    return _is_rate_limited(
        _register_rate_limit_store, _register_rate_limit_lock,
        _REGISTER_RATE_LIMIT_MAX_ATTEMPTS, _REGISTER_RATE_LIMIT_WINDOW_S,
    )


def _record_register_attempt() -> None:
    _record_attempt(
        _register_rate_limit_store, _register_rate_limit_lock,
        _REGISTER_RATE_LIMIT_WINDOW_S,
    )


def _password_complexity_error(password: str) -> str:
    """Return an error message if the password fails complexity requirements, else ''."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must include at least one lowercase letter."
    if not re.search(r"\d", password):
        return "Password must include at least one number."
    return ""


@bp.route("/welcome")
def landing() -> Any:
    """Public landing page for unauthenticated visitors."""
    if g.user is not None:
        return redirect(url_for("views.index"))
    return render_template("landing.html")


@bp.route("/login", methods=["GET", "POST"])
def login() -> Any:
    """Log-in page and form handler."""
    if request.method == "GET":
        if g.user is not None:
            return redirect(url_for("views.index"))
        return render_template("login.html", error=None)
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or not password:
        return render_template(
            "login.html", error="Please enter both fields.", username=username
        )
    if _login_is_rate_limited():
        return render_template(
            "login.html",
            error="Too many attempts. Please wait a few minutes and try again.",
            username=username,
        )
    user = authenticate_user(username, password)
    if user is None:
        _record_login_failure()
        return render_template(
            "login.html", error="Invalid username or password.", username=username
        )
    _clear_login_failures()
    # Regenerate session to prevent session fixation: preserve the anonymous
    # location choice, then clear everything else before setting credentials.
    prior_location_id = session.get("location_id")
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    # Restore saved location preference (DB preference wins over anonymous choice).
    prefs = get_preferences(user["id"])
    if prefs.get("location_id"):
        session["location_id"] = prefs["location_id"]
    elif user.get("default_location_id"):
        session["location_id"] = user["default_location_id"]
    elif prior_location_id:
        session["location_id"] = prior_location_id
    return redirect(url_for("views.index"))


@bp.route("/register", methods=["GET", "POST"])
def register() -> Any:
    """Registration page and form handler."""
    if request.method == "GET":
        if g.user is not None:
            return redirect(url_for("views.index"))
        return render_template("register.html", error=None)
    if _register_is_rate_limited():
        return render_template(
            "register.html",
            error="Too many registration attempts. Please try again later.",
        )
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    if not username or not password:
        return render_template(
            "register.html", error="Please fill in all fields.", username=username
        )
    if len(username) < 2 or len(username) > 30:
        return render_template(
            "register.html",
            error="Username must be 2-30 characters.",
            username=username,
        )
    if not re.match(r"^[A-Za-z0-9_-]+$", username):
        return render_template(
            "register.html",
            error="Username may only contain letters, numbers, underscores, and hyphens.",
            username=username,
        )
    complexity_error = _password_complexity_error(password)
    if complexity_error:
        return render_template(
            "register.html", error=complexity_error, username=username
        )
    if password != confirm:
        return render_template(
            "register.html", error="Passwords do not match.", username=username
        )
    _record_register_attempt()
    user_id = create_user(username, password)
    if user_id is None:
        return render_template(
            "register.html", error="That username is already taken.", username=username
        )
    # Regenerate session to prevent session fixation.
    loc_id = session.get("location_id")
    session.clear()
    session["user_id"] = user_id
    session.permanent = True
    # Carry over current location if one is set
    if loc_id:
        session["location_id"] = loc_id
        save_preferences(user_id, location_id=loc_id, default_location_id=loc_id)
    return redirect(url_for("views.index"))


@bp.route("/logout", methods=["POST"])
def logout() -> Any:
    """Log out the current user."""
    session.clear()
    return redirect(url_for("auth.landing"))


@bp.route("/account")
def account() -> Any:
    """Account settings page for logged-in users."""
    if g.user is None:
        return redirect(url_for("auth.login"))
    prefs = get_preferences(g.user["id"])
    prefs.setdefault("notification_prefs", {})
    loc = None
    if prefs.get("location_id"):
        loc = get_location(prefs["location_id"])
    favorites = [get_location(loc_id) for loc_id in prefs.get("favorites", [])]
    favorites = [loc_obj for loc_obj in favorites if loc_obj]
    recent_logs = get_recent_logs(g.user["id"], limit=5)
    return render_template(
        "account.html",
        prefs=prefs,
        saved_location=loc,
        recent_logs=recent_logs,
        favorite_locations=favorites,
    )


@bp.route("/account/settings", methods=["POST"])
def account_settings() -> Any:
    if g.user is None:
        return redirect(url_for("auth.login"))

    wind_units = request.form.get("wind_units", "knots")
    if wind_units not in {"knots", "mph"}:
        wind_units = "knots"
    temp_units = request.form.get("temp_units", "F")
    if temp_units not in {"F", "C"}:
        temp_units = "F"
    weekly_email = request.form.get("weekly_email") == "on"
    favorite_ids = [
        loc_id.strip()
        for loc_id in request.form.get("favorites_csv", "").split(",")
        if loc_id.strip()
    ]
    # Only keep favorites that resolve to real locations
    favorite_ids = [loc_id for loc_id in favorite_ids if get_location(loc_id)]
    default_location_id = request.form.get("default_location_id", "").strip() or None
    if default_location_id and not get_location(default_location_id):
        default_location_id = None

    save_preferences(
        g.user["id"],
        wind_units=wind_units,
        temp_units=temp_units,
        units=temp_units,
        notification_prefs={"weekly_email": weekly_email},
        favorites=favorite_ids,
        default_location_id=default_location_id,
    )
    if default_location_id:
        session["location_id"] = default_location_id
    return redirect(url_for("auth.account", saved="1"))
