"""Authentication routes: login, register, logout, account."""

from __future__ import annotations

import re
import secrets
import threading
import time
from typing import Any
from werkzeug.security import check_password_hash

from flask import (
    Blueprint,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import logging

from locations import get_location
from storage.sqlite import (
    authenticate_user,
    bump_session_version,
    change_password,
    confirm_email,
    create_user,
    delete_user,
    get_preferences,
    get_recent_logs,
    get_user_by_email,
    get_user_password_hash,
    save_preferences,
)
from web.helpers import get_prefs_cached
from web.rate_limit import (
    client_ip as _client_ip,
    is_rate_limited as _is_rate_limited,
    record_attempt as _record_attempt,
    clear_attempts as _clear_attempts,
)

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

_LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10
_LOGIN_RATE_LIMIT_WINDOW_S = 15 * 60

_REGISTER_RATE_LIMIT_MAX_ATTEMPTS = 5
_REGISTER_RATE_LIMIT_WINDOW_S = 60 * 60  # 1 hour

# Forecast force-refresh is expensive (external API calls + DB writes).
# Limit each IP to 4 force refreshes per 5-minute window.
_REFRESH_RATE_LIMIT_MAX_ATTEMPTS = 4
_REFRESH_RATE_LIMIT_WINDOW_S = 5 * 60

# Sensitive account-action rate limiting (password change, account delete).
_ACCOUNT_ACTION_RATE_LIMIT_MAX_ATTEMPTS = 5
_ACCOUNT_ACTION_RATE_LIMIT_WINDOW_S = 15 * 60

_account_action_rate_limit_store: dict[str, tuple[float, int]] = {}
_account_action_rate_limit_lock = threading.Lock()


def _account_action_is_rate_limited() -> bool:
    return _is_rate_limited(
        _account_action_rate_limit_store,
        _account_action_rate_limit_lock,
        _ACCOUNT_ACTION_RATE_LIMIT_MAX_ATTEMPTS,
        _ACCOUNT_ACTION_RATE_LIMIT_WINDOW_S,
    )


def _record_account_action_failure() -> None:
    _record_attempt(
        _account_action_rate_limit_store,
        _account_action_rate_limit_lock,
        _ACCOUNT_ACTION_RATE_LIMIT_WINDOW_S,
    )


def _clear_account_action_failures() -> None:
    _clear_attempts(_account_action_rate_limit_store, _account_action_rate_limit_lock)


# Per-username account lockout.
_ACCOUNT_LOCKOUT_MAX_FAILURES = 10
_ACCOUNT_LOCKOUT_WINDOW_S = 30 * 60  # 30 minutes

_rate_limit_store: dict[str, tuple[float, int]] = {}
_rate_limit_lock = threading.Lock()

_register_rate_limit_store: dict[str, tuple[float, int]] = {}
_register_rate_limit_lock = threading.Lock()

_refresh_rate_limit_store: dict[str, tuple[float, int]] = {}
_refresh_rate_limit_lock = threading.Lock()

# Keyed by lowercase username rather than IP.
_account_lockout_store: dict[str, tuple[float, int]] = {}
_account_lockout_lock = threading.Lock()


def _login_is_rate_limited() -> bool:
    return _is_rate_limited(
        _rate_limit_store,
        _rate_limit_lock,
        _LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
        _LOGIN_RATE_LIMIT_WINDOW_S,
    )


def _record_login_failure() -> None:
    _record_attempt(_rate_limit_store, _rate_limit_lock, _LOGIN_RATE_LIMIT_WINDOW_S)


def _clear_login_failures() -> None:
    _clear_attempts(_rate_limit_store, _rate_limit_lock)


def _register_is_rate_limited() -> bool:
    return _is_rate_limited(
        _register_rate_limit_store,
        _register_rate_limit_lock,
        _REGISTER_RATE_LIMIT_MAX_ATTEMPTS,
        _REGISTER_RATE_LIMIT_WINDOW_S,
    )


def _record_register_attempt() -> None:
    _record_attempt(
        _register_rate_limit_store,
        _register_rate_limit_lock,
        _REGISTER_RATE_LIMIT_WINDOW_S,
    )


def refresh_is_rate_limited() -> bool:
    """Return True if this IP has exceeded the forecast force-refresh rate limit."""
    return _is_rate_limited(
        _refresh_rate_limit_store,
        _refresh_rate_limit_lock,
        _REFRESH_RATE_LIMIT_MAX_ATTEMPTS,
        _REFRESH_RATE_LIMIT_WINDOW_S,
    )


def record_refresh_attempt() -> None:
    _record_attempt(
        _refresh_rate_limit_store,
        _refresh_rate_limit_lock,
        _REFRESH_RATE_LIMIT_WINDOW_S,
    )


# -- Per-username account lockout -------------------------------------------

_LOCKOUT_PRUNE_EVERY = 500  # prune expired lockout entries every N checks


def _prune_lockout_store() -> None:
    """Remove expired entries from the lockout store (call while holding the lock)."""
    now = time.time()
    expired = [
        k
        for k, (start, _) in _account_lockout_store.items()
        if now - start > _ACCOUNT_LOCKOUT_WINDOW_S
    ]
    for k in expired:
        del _account_lockout_store[k]


_lockout_prune_counter = 0


def _account_is_locked(username: str) -> bool:
    """Return True if *username* has exceeded the per-account failure threshold."""
    global _lockout_prune_counter
    key = username.lower()
    now = time.time()
    with _account_lockout_lock:
        _lockout_prune_counter += 1
        if _lockout_prune_counter % _LOCKOUT_PRUNE_EVERY == 0:
            _prune_lockout_store()
        start, failures = _account_lockout_store.get(key, (now, 0))
        if now - start > _ACCOUNT_LOCKOUT_WINDOW_S:
            _account_lockout_store[key] = (now, 0)
            return False
        return failures >= _ACCOUNT_LOCKOUT_MAX_FAILURES


def _record_account_failure(username: str) -> None:
    """Increment the per-username failure counter."""
    key = username.lower()
    now = time.time()
    with _account_lockout_lock:
        start, failures = _account_lockout_store.get(key, (now, 0))
        if now - start > _ACCOUNT_LOCKOUT_WINDOW_S:
            _account_lockout_store[key] = (now, 1)
        else:
            _account_lockout_store[key] = (start, failures + 1)


def _clear_account_failures(username: str) -> None:
    """Clear the failure counter for *username* after a successful login."""
    key = username.lower()
    with _account_lockout_lock:
        _account_lockout_store.pop(key, None)


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
        logger.warning("security.login_ip_rate_limited ip=%s", _client_ip())
        return render_template(
            "login.html",
            error="Too many attempts. Please wait a few minutes and try again.",
            username=username,
        )
    if _account_is_locked(username):
        logger.warning(
            "security.login_account_locked username=%r ip=%s",
            username,
            _client_ip(),
        )
        return render_template(
            "login.html",
            error="Too many failed attempts. Please try again in 30 minutes.",
            username=username,
        )
    user = authenticate_user(username, password)
    if user is None:
        _record_login_failure()
        _record_account_failure(username)
        logger.warning(
            "security.login_failed username=%r ip=%s",
            username,
            _client_ip(),
        )
        return render_template(
            "login.html", error="Invalid username or password.", username=username
        )
    _clear_login_failures()
    _clear_account_failures(username)
    logger.info("security.login_success user_id=%s ip=%s", user["id"], _client_ip())
    # Regenerate session to prevent session fixation: preserve the anonymous
    # location choice, then clear everything else before setting credentials.
    prior_location_id = session.get("location_id")
    session.clear()
    # Bump the session version so any existing sessions on other devices are
    # immediately invalidated the next time they hit _load_user().
    new_version = bump_session_version(user["id"])
    session["user_id"] = user["id"]
    session["session_version"] = new_version
    session.permanent = True
    # Issue a fresh CSRF token post-login so any token captured before
    # authentication is no longer valid for authenticated endpoints.
    session["csrf_token"] = secrets.token_urlsafe(24)
    # Restore saved location preference (DB preference wins over anonymous choice).
    prefs = get_preferences(user["id"])
    if prefs.get("location_id"):
        session["location_id"] = prefs["location_id"]
    elif user.get("default_location_id"):
        session["location_id"] = user["default_location_id"]
    elif prior_location_id:
        session["location_id"] = prior_location_id
    return redirect(url_for("views.index"))


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    if not username or not email or not password:
        return render_template(
            "register.html",
            error="Please fill in all fields.",
            username=username,
            email=email,
        )
    if len(username) < 2 or len(username) > 30:
        return render_template(
            "register.html",
            error="Username must be 2-30 characters.",
            username=username,
            email=email,
        )
    if not re.match(r"^[A-Za-z0-9_-]+$", username):
        return render_template(
            "register.html",
            error="Username may only contain letters, numbers, underscores, and hyphens.",
            username=username,
            email=email,
        )
    if not _EMAIL_RE.match(email):
        return render_template(
            "register.html",
            error="Please enter a valid email address.",
            username=username,
            email=email,
        )
    if len(email) > 254:
        return render_template(
            "register.html",
            error="Email address is too long.",
            username=username,
            email=email,
        )
    if get_user_by_email(email):
        return render_template(
            "register.html",
            error="Registration could not be completed. Please check your details and try again.",
            username=username,
            email=email,
        )
    complexity_error = _password_complexity_error(password)
    if complexity_error:
        return render_template(
            "register.html",
            error=complexity_error,
            username=username,
            email=email,
        )
    if password != confirm:
        return render_template(
            "register.html",
            error="Passwords do not match.",
            username=username,
            email=email,
        )
    _record_register_attempt()
    user_id = create_user(username, password, email)
    if user_id is None:
        return render_template(
            "register.html",
            error="That username is already taken.",
            username=username,
            email=email,
        )
    # Email is auto-confirmed — no email verification required.
    confirm_email(user_id)
    # Regenerate session to prevent session fixation.
    loc_id = session.get("location_id")
    session.clear()
    session["user_id"] = user_id
    session["session_version"] = 0  # New user; session_version starts at 0
    session.permanent = True
    session["csrf_token"] = secrets.token_urlsafe(24)
    # Carry over current location if one is set
    if loc_id:
        session["location_id"] = loc_id
        save_preferences(user_id, location_id=loc_id, default_location_id=loc_id)
    return redirect(url_for("views.setup"))


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
    uid = g.user["id"]
    prefs = get_prefs_cached(uid)
    prefs.setdefault("notification_prefs", {})
    loc = None
    if prefs.get("location_id"):
        loc = get_location(prefs["location_id"])
    favorites = [get_location(loc_id) for loc_id in prefs.get("favorites", [])]
    favorites = [loc_obj for loc_obj in favorites if loc_obj]
    recent_logs = get_recent_logs(uid, limit=5)
    has_password = bool(get_user_password_hash(uid))
    return render_template(
        "account.html",
        prefs=prefs,
        saved_location=loc,
        recent_logs=recent_logs,
        favorite_locations=favorites,
        has_password=has_password,
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
    # Merge into existing notification_prefs so toggling display settings here
    # doesn't wipe the alert settings managed by the Fishing Condition Alerts
    # panel (enabled / channels / min_rating / lead_hours).
    notif_prefs = dict(get_preferences(g.user["id"]).get("notification_prefs") or {})
    notif_prefs["weekly_email"] = weekly_email
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
        notification_prefs=notif_prefs,
        favorites=favorite_ids,
        default_location_id=default_location_id,
    )
    if default_location_id:
        session["location_id"] = default_location_id
    return redirect(url_for("auth.account", saved="1"))


@bp.route("/account/change-password", methods=["POST"])
def change_password_route() -> Any:
    """Change the current user's password."""
    if g.user is None:
        return redirect(url_for("auth.login"))

    def _pw_error(msg: str) -> Any:
        uid = g.user["id"]
        prefs = get_prefs_cached(uid)
        prefs.setdefault("notification_prefs", {})
        return render_template(
            "account.html",
            prefs=prefs,
            saved_location=None,
            recent_logs=[],
            favorite_locations=[],
            has_password=True,
            pw_error=msg,
            pw_section_open=True,
        )

    if _account_action_is_rate_limited():
        return _pw_error("Too many attempts. Please wait before trying again.")

    current_pw = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_password", "")

    # Verify the current password before allowing any change.
    stored_hash = get_user_password_hash(g.user["id"])
    if not stored_hash or not check_password_hash(stored_hash, current_pw):
        _record_account_action_failure()
        return _pw_error("Current password is incorrect.")

    complexity_error = _password_complexity_error(new_pw)
    if complexity_error:
        return _pw_error(complexity_error)

    if new_pw != confirm_pw:
        return _pw_error("New passwords do not match.")

    # change_password() atomically updates the hash and bumps session_version.
    # Store the new version in the session so the current browser stays logged in
    # while all other devices/sessions are immediately invalidated.
    new_version = change_password(g.user["id"], new_pw)
    session["session_version"] = new_version
    _clear_account_action_failures()
    return redirect(url_for("auth.account", saved="1"))


@bp.route("/account/delete", methods=["POST"])
def delete_account_route() -> Any:
    """Permanently delete the current user's account and all associated data."""
    if g.user is None:
        return redirect(url_for("auth.login"))

    def _del_error(msg: str) -> Any:
        uid = g.user["id"]
        prefs = get_prefs_cached(uid)
        prefs.setdefault("notification_prefs", {})
        return render_template(
            "account.html",
            prefs=prefs,
            saved_location=None,
            recent_logs=[],
            favorite_locations=[],
            has_password=bool(get_user_password_hash(uid)),
            delete_error=msg,
            danger_section_open=True,
        )

    if _account_action_is_rate_limited():
        return _del_error("Too many attempts. Please wait before trying again.")

    password = request.form.get("password", "")

    # Require password confirmation before destructive action.
    stored_hash = get_user_password_hash(g.user["id"])
    if not stored_hash or not check_password_hash(stored_hash, password):
        _record_account_action_failure()
        return _del_error("Incorrect password. Account not deleted.")

    user_id = g.user["id"]
    delete_user(user_id)
    session.clear()
    return redirect(url_for("auth.landing"))
