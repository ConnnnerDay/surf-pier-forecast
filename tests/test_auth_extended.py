"""Extended tests for web/auth.py covering missed branches.

Full-suite missing lines:
  121, 134, 166-173, 187, 190-191, 202, 209-211, 217, 221, 223,
  231, 240, 245, 256-261, 278-302, 313, 316, 332, 339, 353,
  374-403, 409-410, 423, 441, 445, 448, 464
"""
from __future__ import annotations

import re
import time

import pytest

import re


def csrf_token_from_html(html: bytes) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html.decode("utf-8"))
    assert m is not None, "No CSRF token found in HTML"
    return m.group(1)
from storage.sqlite import (
    create_user,
    get_preferences,
    save_preferences,
)
from locations import all_locations_sorted


def _loc_id():
    return all_locations_sorted()[0]["id"]


def _login(client, user_id, location_id=None):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["session_version"] = 0
        if location_id:
            sess["location_id"] = location_id


def _csrf(client, token="test-token"):
    with client.session_transaction() as sess:
        sess["csrf_token"] = token
    return token


# ---------------------------------------------------------------------------
# Lines 121, 134, 166-173, 187, 190-191, 202, 209-211: auth helper functions
# ---------------------------------------------------------------------------


class TestClearLoginFailures:
    def test_clears_rate_limit_entry(self, app):
        """Line 121: _clear_login_failures pops the current IP's entry."""
        from web import auth as am
        from web.rate_limit import client_ip

        with app.test_request_context("/login"):
            ip = client_ip()
            am._rate_limit_store[ip] = (time.time(), 5)
            am._clear_login_failures()
            assert ip not in am._rate_limit_store


class TestRecordRegisterAttempt:
    def test_records_attempt(self, app):
        """Line 134: _record_register_attempt increments register store."""
        from web import auth as am
        from web.rate_limit import client_ip

        with app.test_request_context("/register"):
            ip = client_ip()
            am._register_rate_limit_store.pop(ip, None)
            am._record_register_attempt()
            assert ip in am._register_rate_limit_store


class TestPruneLockoutStore:
    def test_expired_entries_removed(self):
        """Lines 166-173: _prune_lockout_store removes expired entries."""
        from web import auth as am

        old_store = am._account_lockout_store
        am._account_lockout_store = {
            "expireduser": (time.time() - 999999, 3),
            "activeuser": (time.time(), 1),
        }
        try:
            with am._account_lockout_lock:
                am._prune_lockout_store()
            assert "expireduser" not in am._account_lockout_store
            assert "activeuser" in am._account_lockout_store
        finally:
            am._account_lockout_store = old_store


class TestAccountIsLocked:
    def test_prune_triggered_at_interval(self, monkeypatch):
        """Line 187: _prune_lockout_store is called when counter hits threshold."""
        from web import auth as am

        prune_calls = []
        monkeypatch.setattr(am, "_LOCKOUT_PRUNE_EVERY", 1)
        monkeypatch.setattr(am, "_lockout_prune_counter", 0)

        original_prune = am._prune_lockout_store

        def counting_prune():
            prune_calls.append(1)
            original_prune()

        monkeypatch.setattr(am, "_prune_lockout_store", counting_prune)
        am._account_is_locked("testuser")
        assert len(prune_calls) >= 1

    def test_expired_window_resets_counter(self):
        """Lines 190-191: when the lockout window has expired the counter resets
        to zero and the function returns False."""
        from web import auth as am

        key = "resettableuser"
        with am._account_lockout_lock:
            am._account_lockout_store[key] = (time.time() - 999999, 99)

        result = am._account_is_locked("resettableuser")
        assert result is False
        with am._account_lockout_lock:
            _, failures = am._account_lockout_store.get(key, (0, 0))
        assert failures == 0


class TestRecordAccountFailure:
    def test_expired_window_resets_to_one(self):
        """Line 202: when the window has expired record_account_failure starts at 1."""
        from web import auth as am

        key = "resetfail_user"
        with am._account_lockout_lock:
            am._account_lockout_store[key] = (time.time() - 999999, 50)

        am._record_account_failure("resetfail_user")

        with am._account_lockout_lock:
            _, failures = am._account_lockout_store.get(key, (0, -1))
        assert failures == 1


class TestClearAccountFailures:
    def test_removes_lockout_entry(self):
        """Lines 209-211: _clear_account_failures removes the entry."""
        from web import auth as am

        key = "clearme"
        with am._account_lockout_lock:
            am._account_lockout_store[key] = (time.time(), 3)

        am._clear_account_failures("clearme")

        with am._account_lockout_lock:
            assert key not in am._account_lockout_store


# ---------------------------------------------------------------------------
# Lines 217, 221, 223: _password_complexity_error branches
# ---------------------------------------------------------------------------


class TestPasswordComplexityError:
    def test_too_short_returns_error(self):
        """Line 217: password shorter than 8 chars returns an error message."""
        from web.auth import _password_complexity_error
        err = _password_complexity_error("abc")
        assert err != ""
        assert "8 characters" in err

    def test_no_uppercase_returns_error(self):
        """Password with no uppercase returns an error message."""
        from web.auth import _password_complexity_error
        err = _password_complexity_error("alllower1")
        assert err != ""
        assert "uppercase" in err.lower()

    def test_no_lowercase_returns_error(self):
        """Line 221: password with no lowercase returns an error message."""
        from web.auth import _password_complexity_error
        err = _password_complexity_error("ALLUPPER1")
        assert err != ""
        assert "lowercase" in err.lower()

    def test_no_digit_returns_error(self):
        """Line 223: password with no digit returns an error message."""
        from web.auth import _password_complexity_error
        err = _password_complexity_error("NoDigitHere")
        assert err != ""
        assert "number" in err.lower()


# ---------------------------------------------------------------------------
# Line 231: landing() with logged-in user → redirect to index
# ---------------------------------------------------------------------------


class TestLandingLoggedIn:
    def test_logged_in_user_redirected_from_landing(self, client):
        """Line 231: /welcome redirects a logged-in user to the dashboard."""
        uid = create_user("landing_user", "Pass1234")
        _login(client, uid)
        resp = client.get("/welcome", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")


# ---------------------------------------------------------------------------
# Line 240: login() GET with logged-in user → redirect to index
# Line 245: login() POST with empty username/password
# Lines 256-261: login() POST with locked account
# Lines 278-302: login() POST with valid credentials (success)
# ---------------------------------------------------------------------------


class TestLoginRoute:
    def test_get_login_while_logged_in_redirects(self, client):
        """Line 240: GET /login for a logged-in user redirects to the dashboard."""
        uid = create_user("login_logged_in", "Pass1234")
        _login(client, uid)
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")

    def test_post_login_with_empty_fields_shows_error(self, client):
        """Line 245: POST /login with no username/password returns error page."""
        page = client.get("/login")
        token = csrf_token_from_html(page.data)
        resp = client.post(
            "/login",
            data={"csrf_token": token, "username": "", "password": ""},
        )
        assert resp.status_code == 200
        assert b"enter both fields" in resp.data.lower()

    def test_post_login_with_locked_account_shows_error(self, client, monkeypatch):
        """Lines 256-261: POST /login when account is locked returns error."""
        from web import auth as am

        monkeypatch.setattr(am, "_account_is_locked", lambda u: True)
        create_user("locked_acct", "Pass1234")
        page = client.get("/login")
        token = csrf_token_from_html(page.data)
        resp = client.post(
            "/login",
            data={"csrf_token": token, "username": "locked_acct", "password": "Pass1234"},
        )
        assert resp.status_code == 200
        assert b"30 minutes" in resp.data

    def test_successful_login_redirects_to_index(self, client):
        """Lines 278-302: a correct username/password clears failures, sets session,
        and redirects to the dashboard."""
        create_user("good_user", "Pass1234")
        page = client.get("/login")
        token = csrf_token_from_html(page.data)
        resp = client.post(
            "/login",
            data={"csrf_token": token, "username": "good_user", "password": "Pass1234"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")

    def test_successful_login_restores_db_location(self, client):
        """Line 296-297: if the user's profile has a location_id it is put in session."""
        loc_id = _loc_id()
        uid = create_user("loc_user", "Pass1234")
        save_preferences(uid, location_id=loc_id)

        page = client.get("/login")
        token = csrf_token_from_html(page.data)
        client.post(
            "/login",
            data={"csrf_token": token, "username": "loc_user", "password": "Pass1234"},
        )
        with client.session_transaction() as sess:
            assert sess.get("location_id") == loc_id

    def test_successful_login_uses_default_location_id(self, client, monkeypatch):
        """Line 299: when prefs have no location_id but user dict has default_location_id,
        the session gets default_location_id after login.
        authenticate_user() doesn't return default_location_id normally, so we patch it
        to simulate the case the login route was designed to handle."""
        from web import auth as am
        loc_id = _loc_id()
        create_user("default_loc_user", "Pass1234")

        real_auth = am.authenticate_user

        def patched_auth(username, password):
            user = real_auth(username, password)
            if user is not None:
                user["default_location_id"] = loc_id
            return user

        monkeypatch.setattr(am, "authenticate_user", patched_auth)

        page = client.get("/login")
        token = csrf_token_from_html(page.data)
        client.post(
            "/login",
            data={"csrf_token": token, "username": "default_loc_user", "password": "Pass1234"},
        )
        with client.session_transaction() as sess:
            assert sess.get("location_id") == loc_id

    def test_successful_login_preserves_prior_location(self, client):
        """Lines 300-301: when prefs have no location, the anonymous session location
        is preserved after login."""
        loc_id = _loc_id()
        create_user("noloc_user", "Pass1234")

        with client.session_transaction() as sess:
            sess["location_id"] = loc_id

        page = client.get("/login")
        token = csrf_token_from_html(page.data)
        resp = client.post(
            "/login",
            data={"csrf_token": token, "username": "noloc_user", "password": "Pass1234"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("location_id") == loc_id


# ---------------------------------------------------------------------------
# Line 313: register() GET with logged-in user → redirect to index
# Line 316: register() POST rate limited
# Line 332: register() POST with bad username length
# Line 339: register() POST with invalid username chars
# Line 353: register() POST with too-long email
# Lines 374-380: register() POST mismatched passwords
# Lines 381-389: register() POST username taken
# Lines 390-403: register() POST success
# ---------------------------------------------------------------------------


class TestRegisterRoute:
    def test_get_register_while_logged_in_redirects(self, client):
        """Line 313: GET /register for a logged-in user redirects to dashboard."""
        uid = create_user("reg_logged_in", "Pass1234")
        _login(client, uid)
        resp = client.get("/register", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")

    def test_register_rate_limited_shows_error(self, client, monkeypatch):
        """Line 316: POST /register when rate limited returns error page."""
        from web import auth as am

        monkeypatch.setattr(am, "_register_is_rate_limited", lambda: True)
        page = client.get("/register")
        token = csrf_token_from_html(page.data)
        resp = client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "newuser",
                "email": "new@example.com",
                "password": "Pass1234",
                "confirm": "Pass1234",
            },
        )
        assert resp.status_code == 200
        assert b"many registration attempts" in resp.data.lower()

    def test_register_short_username_rejected(self, client):
        """Line 332: username shorter than 2 chars returns error."""
        page = client.get("/register")
        token = csrf_token_from_html(page.data)
        resp = client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "x",
                "email": "x@example.com",
                "password": "Pass1234",
                "confirm": "Pass1234",
            },
        )
        assert resp.status_code == 200
        assert b"2-30 characters" in resp.data

    def test_register_invalid_chars_in_username_rejected(self, client):
        """Line 339: username with invalid characters returns error."""
        page = client.get("/register")
        token = csrf_token_from_html(page.data)
        resp = client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "bad user!",
                "email": "bad@example.com",
                "password": "Pass1234",
                "confirm": "Pass1234",
            },
        )
        assert resp.status_code == 200
        assert b"only contain letters" in resp.data

    def test_register_too_long_email_rejected(self, client):
        """Line 353: email longer than 254 chars returns error."""
        page = client.get("/register")
        token = csrf_token_from_html(page.data)
        long_email = "a" * 250 + "@x.com"  # 256 chars > 254
        resp = client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "okuser",
                "email": long_email,
                "password": "Pass1234",
                "confirm": "Pass1234",
            },
        )
        assert resp.status_code == 200
        assert b"too long" in resp.data

    def test_register_mismatched_passwords_rejected(self, client):
        """Lines 374-380: passwords that don't match returns error."""
        page = client.get("/register")
        token = csrf_token_from_html(page.data)
        resp = client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "matchuser",
                "email": "match@example.com",
                "password": "Pass1234",
                "confirm": "Different1",
            },
        )
        assert resp.status_code == 200
        assert b"do not match" in resp.data

    def test_register_taken_username_shows_error(self, client):
        """Lines 381-389: when create_user returns None (username taken), error shown."""
        create_user("taken_name", "Pass1234")
        page = client.get("/register")
        token = csrf_token_from_html(page.data)
        resp = client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "taken_name",
                "email": "other@example.com",
                "password": "Pass1234",
                "confirm": "Pass1234",
            },
        )
        assert resp.status_code == 200
        assert b"already taken" in resp.data

    def test_successful_registration_redirects_to_setup(self, client):
        """Lines 390-403: valid new registration redirects to /setup."""
        page = client.get("/register")
        token = csrf_token_from_html(page.data)
        resp = client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "brandnewuser",
                "email": "brand@example.com",
                "password": "NewPass1",
                "confirm": "NewPass1",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/setup" in resp.headers["Location"]

    def test_successful_registration_preserves_location(self, client):
        """Lines 400-402: a prior anonymous location_id is carried into the new session."""
        loc_id = _loc_id()
        with client.session_transaction() as sess:
            sess["location_id"] = loc_id

        page = client.get("/register")
        token = csrf_token_from_html(page.data)
        client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": "loc_reg_user",
                "email": "locreg@example.com",
                "password": "LocPass1",
                "confirm": "LocPass1",
            },
        )
        with client.session_transaction() as sess:
            assert sess.get("location_id") == loc_id


# ---------------------------------------------------------------------------
# Lines 409-410: logout() clears session and redirects
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_clears_session_and_redirects(self, client):
        """Lines 409-410: POST /logout clears the session and redirects to landing."""
        uid = create_user("logout_user", "Pass1234")
        _login(client, uid)
        token = _csrf(client)
        resp = client.post(
            "/logout",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/welcome" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert "user_id" not in sess


# ---------------------------------------------------------------------------
# Line 423: account() with a saved location in prefs
# ---------------------------------------------------------------------------


class TestAccountRoute:
    def test_account_page_with_saved_location(self, client):
        """Line 423: account page resolves and displays the user's saved location."""
        loc_id = _loc_id()
        uid = create_user("acct_user", "Pass1234")
        save_preferences(uid, location_id=loc_id)
        _login(client, uid)
        resp = client.get("/account")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 441, 445, 448, 464: account_settings() branches
# ---------------------------------------------------------------------------


class TestAccountSettingsRoute:
    def test_no_user_redirects_to_login(self, app):
        """Line 441: account_settings() defense-in-depth guard when g.user is None."""
        from web.auth import account_settings
        import flask

        with app.test_request_context("/account/settings", method="POST"):
            flask.g.user = None
            resp = account_settings()
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_invalid_wind_units_falls_back_to_knots(self, client):
        """Line 445: unrecognised wind_units value is silently coerced to 'knots'."""
        loc_id = _loc_id()
        uid = create_user("ws_user", "Pass1234")
        save_preferences(uid, location_id=loc_id)
        _login(client, uid)
        token = _csrf(client)
        resp = client.post(
            "/account/settings",
            data={
                "csrf_token": token,
                "wind_units": "beaufort",  # invalid → falls back to knots
                "temp_units": "F",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        prefs = get_preferences(uid)
        assert prefs.get("wind_units") == "knots"

    def test_invalid_temp_units_falls_back_to_fahrenheit(self, client):
        """Line 448: unrecognised temp_units value is silently coerced to 'F'."""
        loc_id = _loc_id()
        uid = create_user("tu_user", "Pass1234")
        save_preferences(uid, location_id=loc_id)
        _login(client, uid)
        token = _csrf(client)
        resp = client.post(
            "/account/settings",
            data={
                "csrf_token": token,
                "wind_units": "knots",
                "temp_units": "K",  # invalid → falls back to F
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        prefs = get_preferences(uid)
        assert prefs.get("temp_units") == "F"

    def test_invalid_default_location_id_is_rejected(self, client):
        """Line 464: a default_location_id that doesn't resolve is set to None."""
        uid = create_user("dloc_user", "Pass1234")
        _login(client, uid)
        token = _csrf(client)
        resp = client.post(
            "/account/settings",
            data={
                "csrf_token": token,
                "wind_units": "knots",
                "temp_units": "F",
                "default_location_id": "nonexistent-location-xyz",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        prefs = get_preferences(uid)
        assert prefs.get("default_location_id") is None
