"""Authentication routes: login, register, logout, account."""

from __future__ import annotations

import re
import time
from typing import Any, Dict

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

_LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
_LOGIN_RATE_LIMIT_WINDOW_S = 15 * 60


@bp.route("/welcome")
def landing() -> Any:
    """Public landing page for unauthenticated visitors."""
    if g.user is not None:
        return redirect(url_for("views.index"))
    return render_template("landing.html")


def _password_complexity_error(password: str) -> str:
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must include at least one lowercase letter."
    if not re.search(r"\d", password):
        return "Password must include at least one number."
    return ""


def _session_int(key: str, default: int = 0) -> int:
    """Read an integer from the session, returning *default* on bad/missing values."""
    try:
        return int(session.get(key, default))
    except (ValueError, TypeError):
        return default


def _login_is_rate_limited() -> bool:
    now = int(time.time())
    start = _session_int("login_attempt_window_start")
    attempts = _session_int("login_attempts")
    if now - start > _LOGIN_RATE_LIMIT_WINDOW_S:
        session["login_attempt_window_start"] = now
        session["login_attempts"] = 0
        return False
    return attempts >= _LOGIN_RATE_LIMIT_MAX_ATTEMPTS


def _record_login_failure() -> None:
    now = int(time.time())
    start = _session_int("login_attempt_window_start")
    if now - start > _LOGIN_RATE_LIMIT_WINDOW_S:
        session["login_attempt_window_start"] = now
        session["login_attempts"] = 1
        return
    session["login_attempts"] = _session_int("login_attempts") + 1


def _clear_login_failures() -> None:
    session.pop("login_attempts", None)
    session.pop("login_attempt_window_start", None)


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
        return render_template("login.html", error="Please enter both fields.",
                               username=username)
    if _login_is_rate_limited():
        return render_template(
            "login.html",
            error="Too many attempts. Please wait a few minutes and try again.",
            username=username,
        )
    user = authenticate_user(username, password)
    if user is None:
        _record_login_failure()
        return render_template("login.html", error="Invalid username or password.",
                               username=username)
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
    if not re.match(r"^[A-Za-z0-9_-]+$", username):
        return render_template("register.html",
                               error="Username may only contain letters, numbers, underscores, and hyphens.",
                               username=username)
    complexity_error = _password_complexity_error(password)
    if complexity_error:
        return render_template("register.html",
                               error=complexity_error,
                               username=username)
    if password != confirm:
        return render_template("register.html", error="Passwords do not match.",
                               username=username)
    user_id = create_user(username, password)
    if user_id is None:
        return render_template("register.html",
                               error="That username is already taken.",
                               username=username)
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
def account() -> str:
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
    favorite_ids = [loc_id.strip() for loc_id in request.form.get("favorites_csv", "").split(",") if loc_id.strip()]
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
