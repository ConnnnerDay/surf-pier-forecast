"""Authentication routes: login, register, logout, account."""

from __future__ import annotations

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
from storage.sqlite import (
    authenticate_user,
    count_saved_locations,
    create_user,
    get_preferences,
    get_user_account,
    save_preferences,
)
from web.feature_gates import tier_config

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login() -> Any:
    """Log-in page and form handler."""
    if request.method == "GET":
        return render_template("login.html", error=None)
    identifier = request.form.get("identifier", request.form.get("username", "")).strip()
    password = request.form.get("password", "")
    if not identifier or not password:
        return render_template("login.html", error="Please enter both fields.",
                               username=identifier)
    user = authenticate_user(identifier, password)
    if user is None:
        return render_template("login.html", error="Invalid email/username or password.",
                               username=identifier)
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    # Restore saved location preference
    prefs = get_preferences(user["id"])
    if prefs.get("location_id"):
        session["location_id"] = prefs["location_id"]
    return redirect(url_for("views.index"))


@bp.route("/register", methods=["GET", "POST"])
def register() -> Any:
    """Registration page and form handler."""
    if request.method == "GET":
        return render_template("register.html", error=None)
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    if not username or not email or not password:
        return render_template("register.html", error="Please fill in all fields.",
                               username=username, email=email)
    if len(username) < 2 or len(username) > 30:
        return render_template("register.html",
                               error="Username must be 2-30 characters.",
                               username=username, email=email)
    if len(password) < 4:
        return render_template("register.html",
                               error="Password must be at least 4 characters.",
                               username=username, email=email)
    if password != confirm:
        return render_template("register.html", error="Passwords do not match.",
                               username=username, email=email)
    if "@" not in email or "." not in email.split("@")[-1]:
        return render_template("register.html", error="Please enter a valid email.",
                               username=username, email=email)

    user_id = create_user(username, password, email=email)
    if user_id is None:
        return render_template("register.html",
                               error="That username is already taken.",
                               username=username, email=email)
    session.clear()
    session["user_id"] = user_id
    session.permanent = True
    # Carry over current location if one is set
    loc_id = session.get("location_id")
    if loc_id:
        save_preferences(user_id, location_id=loc_id)
    return redirect(url_for("views.index"))


@bp.route("/logout", methods=["POST"])
def logout() -> Any:
    """Log out the current user."""
    session.clear()
    return redirect(url_for("views.index"))


@bp.route("/account")
def account() -> str:
    """Account settings page for logged-in users."""
    if g.user is None:
        return redirect(url_for("auth.login"))
    prefs = get_preferences(g.user["id"])
    loc = None
    if prefs.get("location_id"):
        loc = get_location(prefs["location_id"])
    acct = get_user_account(g.user["id"]) or {"tier": "free", "is_paid": False}
    gates = tier_config(acct.get("tier", "free"))
    return render_template("account.html", prefs=prefs, saved_location=loc, account=acct, gates=gates, saved_locations_count=count_saved_locations(g.user["id"]))
