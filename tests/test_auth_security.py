"""Auth + account security/settings regression tests."""

import re

import pytest

from app import create_app
from storage.sqlite import (
    create_user,
    get_preferences,
    get_user,
    init_db,
    save_preferences,
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

    monkeypatch.setattr(auth_module, "_rate_limit_store", {})

    create_user("rate_user", "Aa123456")

    page = client.get("/login")
    token = _csrf_from_html(page.data)

    for _ in range(auth_module._LOGIN_RATE_LIMIT_MAX_ATTEMPTS):
        client.post(
            "/login",
            data={
                "csrf_token": token,
                "username": "rate_user",
                "password": "WrongPass1",
            },
        )

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


def test_register_requires_email(client):
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
    uid = create_user("first_email_user", "Aa123456", "shared@gmail.com")
    assert uid is not None

    page = client.get("/register")
    token = _csrf_from_html(page.data)
    resp = client.post(
        "/register",
        data={
            "csrf_token": token,
            "username": "second_email_user",
            "email": "shared@gmail.com",
            "password": "Aa123456",
            "confirm": "Aa123456",
        },
    )
    assert resp.status_code == 200
    assert b"Registration could not be completed" in resp.data


def test_user_can_access_dashboard_after_registration(client):
    uid = create_user("dashboard_user", "Aa123456", "dashboard@example.com")
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


def test_user_can_access_account_page(client):
    uid = create_user("acct_user", "Aa123456", "acct@example.com")
    assert uid is not None

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = 0

    resp = client.get("/account", follow_redirects=False)
    assert resp.status_code == 200


def test_csrf_comparison_uses_constant_time(app):
    """CSRF protection must use hmac.compare_digest (not ==) for constant-time comparison."""
    import inspect
    import app as app_module

    source = inspect.getsource(app_module.create_app)
    assert "hmac.compare_digest" in source, (
        "CSRF token comparison must use hmac.compare_digest() to prevent timing attacks"
    )
