"""Authentication routes: login, register, logout, account."""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from typing import Any, Dict, Tuple

import logging

from flask import (
    Blueprint,
    current_app,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

logger = logging.getLogger(__name__)

from locations import get_location
from storage.db import (
    authenticate_user,
    bump_session_version,
    change_password,
    confirm_email,
    create_user,
    delete_user,
    get_all_user_photo_paths,
    get_preferences,
    get_recent_logs,
    get_user_by_email,
    get_user_by_verification_token,
    get_user_password_hash,
    save_preferences,
    set_email_verification_token,
)
from services.email import send_verification_email

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
#
# Even an authenticated attacker (via XSS, session hijack, or a shared
# computer) should not be able to rapidly brute-force the "current password"
# field on these forms.  Each IP is limited to 5 attempts per 15 minutes.
_ACCOUNT_ACTION_RATE_LIMIT_MAX_ATTEMPTS = 5
_ACCOUNT_ACTION_RATE_LIMIT_WINDOW_S = 15 * 60

_account_action_rate_limit_store: Dict[str, Tuple[float, int]] = {}
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
#
# IP-based rate limiting alone does not stop a distributed brute-force attack
# where many different IPs target the same account.  Tracking failures per
# username (case-folded) provides a second, independent layer: after
# _ACCOUNT_LOCKOUT_MAX_FAILURES consecutive wrong passwords for a given
# username the account is locked for _ACCOUNT_LOCKOUT_WINDOW_S seconds,
# regardless of how many source IPs are involved.
#
# On successful login the counter for that username is cleared.
_ACCOUNT_LOCKOUT_MAX_FAILURES = 10
_ACCOUNT_LOCKOUT_WINDOW_S = 30 * 60  # 30 minutes

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

_refresh_rate_limit_store: Dict[str, Tuple[float, int]] = {}
_refresh_rate_limit_lock = threading.Lock()

# Keyed by lowercase username rather than IP.
_account_lockout_store: Dict[str, Tuple[float, int]] = {}
_account_lockout_lock = threading.Lock()

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


_PRUNE_EVERY = 200  # prune expired entries every N rate-limit checks
_prune_counter = 0


def _prune_store(store: Dict[str, Tuple[float, int]], window_s: float) -> None:
    """Remove entries whose rate-limit window has expired.

    Called periodically (every _PRUNE_EVERY checks) to keep the in-memory
    stores from growing without bound when the app is hit from many unique IPs.
    Must be called while holding the relevant lock.
    """
    now = time.time()
    expired = [ip for ip, (start, _) in store.items() if now - start > window_s]
    for ip in expired:
        del store[ip]


def _is_rate_limited(
    store: Dict[str, Tuple[float, int]],
    lock: threading.Lock,
    max_attempts: int,
    window_s: float,
) -> bool:
    """Return True if the current client IP has exceeded the given rate limit."""
    global _prune_counter
    ip = _client_ip()
    now = time.time()
    with lock:
        _prune_counter += 1
        if _prune_counter % _PRUNE_EVERY == 0:
            _prune_store(store, window_s)
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


def refresh_is_rate_limited() -> bool:
    """Return True if this IP has exceeded the forecast force-refresh rate limit."""
    return _is_rate_limited(
        _refresh_rate_limit_store, _refresh_rate_limit_lock,
        _REFRESH_RATE_LIMIT_MAX_ATTEMPTS, _REFRESH_RATE_LIMIT_WINDOW_S,
    )


def record_refresh_attempt() -> None:
    _record_attempt(
        _refresh_rate_limit_store, _refresh_rate_limit_lock,
        _REFRESH_RATE_LIMIT_WINDOW_S,
    )


# -- Per-username account lockout -------------------------------------------

def _account_is_locked(username: str) -> bool:
    """Return True if *username* has exceeded the per-account failure threshold."""
    key = username.lower()
    now = time.time()
    with _account_lockout_lock:
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
        return render_template(
            "login.html",
            error="Too many attempts. Please wait a few minutes and try again.",
            username=username,
        )
    if _account_is_locked(username):
        return render_template(
            "login.html",
            error="Too many failed attempts. Please try again in 30 minutes.",
            username=username,
        )
    user = authenticate_user(username, password)
    if user is None:
        _record_login_failure()
        _record_account_failure(username)
        return render_template(
            "login.html", error="Invalid username or password.", username=username
        )
    _clear_login_failures()
    _clear_account_failures(username)
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
            "register.html", error="Please fill in all fields.",
            username=username, email=email,
        )
    if len(username) < 2 or len(username) > 30:
        return render_template(
            "register.html",
            error="Username must be 2-30 characters.",
            username=username, email=email,
        )
    if not re.match(r"^[A-Za-z0-9_-]+$", username):
        return render_template(
            "register.html",
            error="Username may only contain letters, numbers, underscores, and hyphens.",
            username=username, email=email,
        )
    if not _EMAIL_RE.match(email):
        return render_template(
            "register.html",
            error="Please enter a valid email address.",
            username=username, email=email,
        )
    if len(email) > 254:
        return render_template(
            "register.html",
            error="Email address is too long.",
            username=username, email=email,
        )
    if get_user_by_email(email):
        return render_template(
            "register.html",
            error="An account with that email address already exists.",
            username=username, email=email,
        )
    complexity_error = _password_complexity_error(password)
    if complexity_error:
        return render_template(
            "register.html", error=complexity_error, username=username, email=email,
        )
    if password != confirm:
        return render_template(
            "register.html", error="Passwords do not match.", username=username, email=email,
        )
    _record_register_attempt()
    user_id = create_user(username, password, email)
    if user_id is None:
        return render_template(
            "register.html", error="That username is already taken.",
            username=username, email=email,
        )
    # Send verification email (best-effort; account is created regardless).
    token = secrets.token_urlsafe(32)
    set_email_verification_token(user_id, token)
    base_url = request.host_url
    send_verification_email(email, username, token, base_url)
    # Regenerate session to prevent session fixation.
    loc_id = session.get("location_id")
    session.clear()
    session["user_id"] = user_id
    session["session_version"] = 0  # New user; session_version starts at 0
    session.permanent = True
    # Carry over current location if one is set
    if loc_id:
        session["location_id"] = loc_id
        save_preferences(user_id, location_id=loc_id, default_location_id=loc_id)
    return redirect(url_for("views.index"))


@bp.route("/verify-email/<token>")
def verify_email(token: str) -> Any:
    """Confirm a user's email address via the one-time token link."""
    user = get_user_by_verification_token(token)
    if user is None:
        return render_template(
            "verify_email.html",
            success=False,
            message="This verification link is invalid or has expired.",
        )
    if user["email_confirmed"]:
        return render_template(
            "verify_email.html",
            success=True,
            message="Your email is already verified.",
        )
    confirm_email(user["id"])
    # If the user is currently logged in, refresh g.user so templates reflect
    # the confirmed state immediately.
    if g.user and g.user["id"] == user["id"]:
        g.user["email_confirmed"] = True
    return render_template(
        "verify_email.html",
        success=True,
        message="Your email has been verified. Welcome to Surf & Pier!",
    )


@bp.route("/resend-verification", methods=["POST"])
def resend_verification() -> Any:
    """Resend the email verification link to the logged-in user."""
    if g.user is None:
        return redirect(url_for("auth.login"))
    if g.user.get("email_confirmed"):
        return redirect(url_for("auth.account"))
    email = g.user.get("email")
    if not email:
        return redirect(url_for("auth.account"))
    token = secrets.token_urlsafe(32)
    set_email_verification_token(g.user["id"], token)
    base_url = request.host_url
    send_verification_email(email, g.user["username"], token, base_url)
    return redirect(url_for("auth.account", verify_sent="1"))


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


@bp.route("/account/change-password", methods=["POST"])
def change_password_route() -> Any:
    """Change the current user's password."""
    if g.user is None:
        return redirect(url_for("auth.login"))

    def _pw_error(msg: str) -> Any:
        prefs = get_preferences(g.user["id"])
        prefs.setdefault("notification_prefs", {})
        return render_template(
            "account.html",
            prefs=prefs,
            saved_location=None,
            recent_logs=[],
            favorite_locations=[],
            pw_error=msg,
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
        prefs = get_preferences(g.user["id"])
        prefs.setdefault("notification_prefs", {})
        return render_template(
            "account.html",
            prefs=prefs,
            saved_location=None,
            recent_logs=[],
            favorite_locations=[],
            delete_error=msg,
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

    # Remove all uploaded photo files from disk before deleting the DB rows.
    upload_root = current_app.config.get("UPLOAD_FOLDER", "")
    if upload_root:
        upload_root_real = os.path.realpath(upload_root)
        for rel_path in get_all_user_photo_paths(user_id):
            if not rel_path:
                continue
            sub = rel_path[len("uploads/"):] if rel_path.startswith("uploads/") else rel_path
            abs_path = os.path.realpath(os.path.join(upload_root, sub))
            if abs_path.startswith(upload_root_real + os.sep):
                try:
                    os.remove(abs_path)
                except OSError:
                    logger.warning("Could not remove photo file during account deletion: %s", rel_path)

    delete_user(user_id)
    session.clear()
    return redirect(url_for("auth.landing"))
