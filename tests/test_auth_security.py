"""Auth + account security/settings regression tests."""

import re

import pytest

from app import create_app
from storage.sqlite import create_user, get_preferences, get_user, init_db


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
            "password": "alllowercase",
            "confirm": "alllowercase",
        },
    )
    assert resp.status_code == 200
    assert b"uppercase" in resp.data


def test_login_rate_limit_message(client):
    user_id = create_user("rate_user", "Aa123456")
    assert user_id is not None

    page = client.get("/login")
    token = _csrf_from_html(page.data)

    with client.session_transaction() as sess:
        sess["login_attempt_window_start"] = 9999999999
        sess["login_attempts"] = 5

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
