"""Tests for the security-sensitive account routes: change-password and delete-account.

These flows require re-authentication with the current password before a
destructive/high-trust change is allowed, and are rate-limited separately
from login. They previously had zero test coverage.
"""

from __future__ import annotations

import re

import pytest

from app import create_app
from storage.sqlite import create_user, get_user, get_user_password_hash, init_db


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


def _login_session(client, uid: int, session_version: int = 0) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["session_version"] = session_version


def _anon_csrf_token(client) -> str:
    """Get a valid CSRF token for a request with no logged-in user."""
    page = client.get("/login")
    return _csrf_from_html(page.data)


class TestChangePassword:
    def test_requires_login(self, client):
        token = _anon_csrf_token(client)
        resp = client.post(
            "/account/change-password",
            data={
                "csrf_token": token,
                "current_password": "x",
                "new_password": "y",
                "confirm_password": "y",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/login")

    def test_wrong_current_password_rejected(self, client):
        uid = create_user("pw_user1", "Aa123456")
        _login_session(client, uid)
        page = client.get("/account")
        token = _csrf_from_html(page.data)

        resp = client.post(
            "/account/change-password",
            data={
                "csrf_token": token,
                "current_password": "WrongPass1",
                "new_password": "Bb234567",
                "confirm_password": "Bb234567",
            },
        )
        assert resp.status_code == 200
        assert b"Current password is incorrect" in resp.data
        # Password hash must be unchanged.
        assert get_user_password_hash(uid) is not None

    def test_new_password_must_meet_complexity(self, client):
        uid = create_user("pw_user2", "Aa123456")
        _login_session(client, uid)
        page = client.get("/account")
        token = _csrf_from_html(page.data)

        resp = client.post(
            "/account/change-password",
            data={
                "csrf_token": token,
                "current_password": "Aa123456",
                "new_password": "alllowercase",
                "confirm_password": "alllowercase",
            },
        )
        assert resp.status_code == 200
        assert b"uppercase" in resp.data

    def test_mismatched_confirmation_rejected(self, client):
        uid = create_user("pw_user3", "Aa123456")
        _login_session(client, uid)
        page = client.get("/account")
        token = _csrf_from_html(page.data)

        resp = client.post(
            "/account/change-password",
            data={
                "csrf_token": token,
                "current_password": "Aa123456",
                "new_password": "Bb234567",
                "confirm_password": "Cc345678",
            },
        )
        assert resp.status_code == 200
        assert b"do not match" in resp.data

    def test_successful_change_updates_hash_and_session_version(self, client):
        uid = create_user("pw_user4", "Aa123456")
        _login_session(client, uid)
        page = client.get("/account")
        token = _csrf_from_html(page.data)
        old_hash = get_user_password_hash(uid)

        resp = client.post(
            "/account/change-password",
            data={
                "csrf_token": token,
                "current_password": "Aa123456",
                "new_password": "Bb234567",
                "confirm_password": "Bb234567",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/account?saved=1")

        new_hash = get_user_password_hash(uid)
        assert new_hash != old_hash

        # Session version bumped server-side; old (stale) session cookie must
        # now be rejected by the auth gate even though the cookie's user_id
        # is still valid.
        with client.session_transaction() as sess:
            assert sess["session_version"] == 1

    def test_old_session_version_invalidated_on_other_device(self, app):
        uid = create_user("pw_user5", "Aa123456")
        client_a = app.test_client()
        client_b = app.test_client()
        _login_session(client_a, uid)
        _login_session(client_b, uid)

        page = client_a.get("/account")
        token = _csrf_from_html(page.data)
        client_a.post(
            "/account/change-password",
            data={
                "csrf_token": token,
                "current_password": "Aa123456",
                "new_password": "Bb234567",
                "confirm_password": "Bb234567",
            },
        )

        # client_b's session still has session_version=0, which is now stale.
        resp = client_b.get("/account", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/login")

    def test_rate_limited_after_repeated_failures(self, client, monkeypatch):
        from web import auth as auth_module

        monkeypatch.setattr(auth_module, "_account_action_rate_limit_store", {})

        uid = create_user("pw_user6", "Aa123456")
        _login_session(client, uid)
        page = client.get("/account")
        token = _csrf_from_html(page.data)

        for _ in range(auth_module._ACCOUNT_ACTION_RATE_LIMIT_MAX_ATTEMPTS):
            client.post(
                "/account/change-password",
                data={
                    "csrf_token": token,
                    "current_password": "WrongPass1",
                    "new_password": "Bb234567",
                    "confirm_password": "Bb234567",
                },
            )

        resp = client.post(
            "/account/change-password",
            data={
                "csrf_token": token,
                "current_password": "Aa123456",
                "new_password": "Bb234567",
                "confirm_password": "Bb234567",
            },
        )
        assert resp.status_code == 200
        assert b"Too many attempts" in resp.data
        # Correct password was supplied but request was still blocked.
        assert get_user_password_hash(uid) is not None


class TestDeleteAccount:
    def test_requires_login(self, client):
        token = _anon_csrf_token(client)
        resp = client.post(
            "/account/delete",
            data={"csrf_token": token, "password": "x"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/login")

    def test_wrong_password_rejected_and_account_survives(self, client):
        uid = create_user("del_user1", "Aa123456")
        _login_session(client, uid)
        page = client.get("/account")
        token = _csrf_from_html(page.data)

        resp = client.post(
            "/account/delete",
            data={"csrf_token": token, "password": "WrongPass1"},
        )
        assert resp.status_code == 200
        assert b"Incorrect password" in resp.data
        assert get_user(uid) is not None

    def test_correct_password_deletes_account_and_clears_session(self, client):
        uid = create_user("del_user2", "Aa123456")
        _login_session(client, uid)
        page = client.get("/account")
        token = _csrf_from_html(page.data)

        resp = client.post(
            "/account/delete",
            data={"csrf_token": token, "password": "Aa123456"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/welcome")
        assert get_user(uid) is None

        # Session must be cleared — no leftover user_id.
        with client.session_transaction() as sess:
            assert "user_id" not in sess

    def test_rate_limited_after_repeated_failures(self, client, monkeypatch):
        from web import auth as auth_module

        monkeypatch.setattr(auth_module, "_account_action_rate_limit_store", {})

        uid = create_user("del_user3", "Aa123456")
        _login_session(client, uid)
        page = client.get("/account")
        token = _csrf_from_html(page.data)

        for _ in range(auth_module._ACCOUNT_ACTION_RATE_LIMIT_MAX_ATTEMPTS):
            client.post(
                "/account/delete",
                data={"csrf_token": token, "password": "WrongPass1"},
            )

        resp = client.post(
            "/account/delete",
            data={"csrf_token": token, "password": "Aa123456"},
        )
        assert resp.status_code == 200
        assert b"Too many attempts" in resp.data
        assert get_user(uid) is not None
