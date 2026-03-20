"""Auth + account security/settings regression tests."""

import re

import pytest

from app import create_app
from storage.sqlite import (
    confirm_email,
    create_user,
    get_preferences,
    get_user,
    init_db,
    save_preferences,
    set_email_verification_token,
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    init_db()
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _csrf_from_html(html: bytes) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html.decode("utf-8"))
    assert m is not None
    return m.group(1)


def test_login_post_requires_csrf(client):
    resp = client.post("/login", data={"username": "u", "password": "p"})
    assert resp.status_code == 400


def test_register_requires_complex_password(client):
    page = client.get("/register")
    token = _csrf_from_html(page.data)
    resp = client.post(
        "/register",
        data={
            "csrf_token": token,
            "username": "complex_user",
            "email": "complex@example.com",
            "password": "alllowercase",
            "confirm": "alllowercase",
        },
    )
    assert resp.status_code == 200
    assert b"uppercase" in resp.data


def test_login_rate_limit_message(client, monkeypatch):
    from web import auth as auth_module

    # Isolate this test from any state left by other tests.
    monkeypatch.setattr(auth_module, "_rate_limit_store", {})

    create_user("rate_user", "Aa123456")

    page = client.get("/login")
    token = _csrf_from_html(page.data)

    # Exhaust the IP-based rate limit with bad passwords.
    for _ in range(auth_module._LOGIN_RATE_LIMIT_MAX_ATTEMPTS):
        client.post(
            "/login",
            data={
                "csrf_token": token,
                "username": "rate_user",
                "password": "WrongPass1",
            },
        )

    # Next attempt (even with the correct password) must be blocked.
    resp = client.post(
        "/login",
        data={"csrf_token": token, "username": "rate_user", "password": "Aa123456"},
    )
    assert resp.status_code == 200
    assert b"Too many attempts" in resp.data


def test_account_settings_updates_preferences(client):
    uid = create_user("settings_user", "Aa123456")
    assert uid is not None

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0

    page = client.get("/account")
    token = _csrf_from_html(page.data)

    resp = client.post(
        "/account/settings",
        data={
            "csrf_token": token,
            "wind_units": "mph",
            "temp_units": "C",
            "weekly_email": "on",
            "favorites_csv": "wrightsville-beach-nc,outer-banks-nc",
            "default_location_id": "wrightsville-beach-nc",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    prefs = get_preferences(uid)
    user = get_user(uid)
    assert prefs["wind_units"] == "mph"
    assert prefs["temp_units"] == "C"
    assert prefs["notification_prefs"].get("weekly_email") is True
    assert prefs["favorites"] == ["wrightsville-beach-nc", "outer-banks-nc"]
    assert user is not None
    assert user["default_location_id"] == "wrightsville-beach-nc"


def test_setup_shows_favorites_for_logged_in_user(client):
    uid = create_user("setup_fav_user", "Aa123456")
    assert uid is not None
    save_preferences(uid, favorites=["wrightsville-beach-nc"])

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0

    resp = client.get("/setup")
    assert resp.status_code == 200
    assert b"Favorites" in resp.data
    assert b"Wrightsville Beach" in resp.data


def test_setup_favorite_toggle_updates_preferences(client):
    uid = create_user("toggle_fav_user", "Aa123456")
    assert uid is not None

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0

    page = client.get("/setup")
    token = _csrf_from_html(page.data)

    add_resp = client.post(
        "/setup/favorite/wrightsville-beach-nc",
        data={"csrf_token": token, "next": "/setup"},
        follow_redirects=False,
    )
    assert add_resp.status_code == 302
    assert "wrightsville-beach-nc" in get_preferences(uid)["favorites"]

    remove_resp = client.post(
        "/setup/favorite/wrightsville-beach-nc",
        data={"csrf_token": token, "next": "/setup"},
        follow_redirects=False,
    )
    assert remove_resp.status_code == 302
    assert "wrightsville-beach-nc" not in get_preferences(uid)["favorites"]


def test_setup_location_select_forms_include_valid_csrf(client):
    uid = create_user("setup_select_csrf_user", "Aa123456")
    assert uid is not None

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0

    page = client.get("/setup")
    html = page.data.decode("utf-8")
    page_token = _csrf_from_html(page.data)

    select_match = re.search(
        r'action="(/setup/select/[^"]+)"[^>]*>\s*\n\s*<input type="hidden" name="csrf_token" value="([^"]*)"',
        html,
    )
    assert select_match is not None

    action, select_token = select_match.groups()
    assert select_token == page_token

    resp = client.post(
        action, data={"csrf_token": select_token}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/profile")


def test_index_requires_profile_after_location_selected(client):
    uid = create_user("profile_gate_user", "Aa123456")
    assert uid is not None
    save_preferences(
        uid,
        location_id="wrightsville-beach-nc",
        default_location_id="wrightsville-beach-nc",
    )

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0
        sess["location_id"] = "wrightsville-beach-nc"

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/profile")


def test_index_allowed_once_profile_exists(client):
    uid = create_user("profile_gate_done_user", "Aa123456")
    assert uid is not None
    save_preferences(
        uid,
        location_id="wrightsville-beach-nc",
        default_location_id="wrightsville-beach-nc",
        fishing_profile={"fishing_types": ["surf"], "targets": ["anything"]},
    )

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0
        sess["location_id"] = "wrightsville-beach-nc"

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200


def test_setup_favorite_rejects_external_next_redirect(client):
    uid = create_user("toggle_fav_user_next", "Aa123456")
    assert uid is not None

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0

    page = client.get("/setup")
    token = _csrf_from_html(page.data)

    resp = client.post(
        "/setup/favorite/wrightsville-beach-nc",
        data={"csrf_token": token, "next": "https://example.com/phish"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/setup")


# ---------------------------------------------------------------------------
# Email verification tests
# ---------------------------------------------------------------------------


def test_register_requires_email(client):
    """Registration without an email address is rejected."""
    page = client.get("/register")
    token = _csrf_from_html(page.data)
    resp = client.post(
        "/register",
        data={
            "csrf_token": token,
            "username": "no_email_user",
            "password": "Aa123456",
            "confirm": "Aa123456",
        },
    )
    assert resp.status_code == 200
    assert b"fill in all fields" in resp.data


def test_register_rejects_invalid_email(client):
    """Registration with a malformed email is rejected."""
    page = client.get("/register")
    token = _csrf_from_html(page.data)
    resp = client.post(
        "/register",
        data={
            "csrf_token": token,
            "username": "bad_email_user",
            "email": "not-an-email",
            "password": "Aa123456",
            "confirm": "Aa123456",
        },
    )
    assert resp.status_code == 200
    assert b"valid email" in resp.data


def test_register_rejects_duplicate_email(client):
    """Two accounts cannot share the same email address."""
    uid = create_user("first_email_user", "Aa123456", "shared@example.com")
    assert uid is not None

    page = client.get("/register")
    token = _csrf_from_html(page.data)
    resp = client.post(
        "/register",
        data={
            "csrf_token": token,
            "username": "second_email_user",
            "email": "shared@example.com",
            "password": "Aa123456",
            "confirm": "Aa123456",
        },
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data


def test_unverified_user_blocked_from_dashboard(client):
    """A logged-in user with an unconfirmed email cannot reach the dashboard."""
    uid = create_user("unverified_dash_user", "Aa123456", "unverified@example.com")
    assert uid is not None
    save_preferences(
        uid,
        location_id="wrightsville-beach-nc",
        default_location_id="wrightsville-beach-nc",
        fishing_profile={"fishing_types": ["surf"], "targets": ["anything"]},
    )

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0
        sess["location_id"] = "wrightsville-beach-nc"

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/verify-pending" in resp.headers["Location"]


def test_verified_user_can_access_dashboard(client):
    """A user whose email is confirmed can reach the dashboard normally."""
    uid = create_user("verified_dash_user", "Aa123456", "verified@example.com")
    assert uid is not None
    confirm_email(uid)
    save_preferences(
        uid,
        location_id="wrightsville-beach-nc",
        default_location_id="wrightsville-beach-nc",
        fishing_profile={"fishing_types": ["surf"], "targets": ["anything"]},
    )

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0
        sess["location_id"] = "wrightsville-beach-nc"

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200


def test_unverified_user_can_access_account_page(client):
    """Unverified users must still be able to reach /account to resend the link."""
    uid = create_user("unverified_acct_user", "Aa123456", "unverified_acct@example.com")
    assert uid is not None

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0

    resp = client.get("/account", follow_redirects=False)
    assert resp.status_code == 200


def test_verify_email_token_confirms_account(client):
    """Clicking a valid verification link sets email_confirmed = True."""
    uid = create_user("token_verify_user", "Aa123456", "tokenverify@example.com")
    assert uid is not None

    token = "validtoken123"
    set_email_verification_token(uid, token)

    resp = client.get(f"/verify-email/{token}", follow_redirects=False)
    assert resp.status_code == 200
    assert b"verified" in resp.data.lower()

    user = get_user(uid)
    assert user is not None
    assert user["email_confirmed"] is True


def test_verify_email_invalid_token_rejected(client):
    """An unknown verification token returns the error page."""
    resp = client.get("/verify-email/totallybogustoken", follow_redirects=False)
    assert resp.status_code == 200
    assert b"invalid or has expired" in resp.data


def test_csrf_comparison_uses_constant_time(app):
    """CSRF protection must use hmac.compare_digest (not ==) for constant-time comparison."""
    import inspect
    import app as app_module

    source = inspect.getsource(app_module.create_app)
    assert "hmac.compare_digest" in source, (
        "CSRF token comparison must use hmac.compare_digest() to prevent timing attacks"
    )


def test_resend_verification_rate_limited_per_account(client, monkeypatch):
    """A second resend within the throttle window is rejected."""
    import time as _time

    uid = create_user("resend_throttle_user", "Aa123456", "resend_throttle@example.com")
    assert uid is not None

    # Plant a recent sent_at timestamp so the per-account throttle fires.
    monkeypatch.setattr("storage.sqlite.DB_PATH", monkeypatch._patches[-1].temp_path
                        if hasattr(monkeypatch, "_patches") else None) if False else None

    # Set a very recent sent_at by doing a legitimate first resend.
    set_email_verification_token(uid, "firsttoken")

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0

    page = client.get("/verify-pending")
    token = _csrf_from_html(page.data)

    # Immediate second resend should be throttled.
    resp = client.post(
        "/resend-verification",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    # Throttled → rendered page (200) with an error, not a redirect.
    assert resp.status_code == 200
    assert b"just sent" in resp.data or b"wait" in resp.data.lower()
