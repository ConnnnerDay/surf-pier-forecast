"""Tests for app factory and blueprint registration."""

import pytest

from app import create_app


@pytest.fixture
def app():
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


class TestAppFactory:
    def test_creates_flask_app(self, app):
        assert app is not None
        assert app.config["TESTING"] is True

    def test_all_blueprints_registered(self, app):
        assert "auth" in app.blueprints
        assert "api" in app.blueprints
        assert "views" in app.blueprints

    def test_expected_routes_exist(self, app):
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/" in rules
        assert "/login" in rules
        assert "/register" in rules
        assert "/setup" in rules
        assert "/live-cams" in rules
        assert "/api/forecast" in rules
        assert "/api/v1/forecast" in rules
        assert "/api/v1/forecast/<location_id>/outlook" in rules
        assert "/api/v1/forecast/<location_id>/solunar" in rules
        assert "/api/v1/forecast/<location_id>/status" in rules
        assert "/api/v1/profile" in rules
        assert "/api/v1/log" in rules
        assert "/api/openapi.json" in rules
        assert "/api/refresh" in rules


class TestBasicRoutes:
    def test_index_loads_for_anon(self, client):
        """Unauthenticated users visiting / land on the login/register page."""
        resp = client.get("/", follow_redirects=True)
        assert resp.status_code == 200
        assert resp.request.path == "/welcome"

    def test_setup_redirects_for_anon(self, client):
        """Unauthenticated users are sent to login/register before /setup.

        See tests/test_auth_security.py::test_setup_shows_favorites_for_logged_in_user
        for the logged-in-renders-200 case — this file's app/client fixtures
        point at the real data/app.db (not an isolated per-test DB), so tests
        here should avoid writing users/preferences.
        """
        resp = client.get("/setup", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/welcome")

    def test_login_page_loads(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"Log In" in resp.data or b"Log in" in resp.data

    def test_register_page_loads(self, client):
        resp = client.get("/register")
        assert resp.status_code == 200
        assert b"Create" in resp.data

    def test_api_forecast_no_location(self, client):
        resp = client.get("/api/forecast")
        assert resp.status_code == 503

    def test_unknown_shared_forecast_404(self, client):
        resp = client.get("/f/nonexistent-location")
        assert resp.status_code == 404

    def test_shared_forecast_renders_full_dashboard_shell(self, client):
        from locations import all_locations_sorted

        sample_location = all_locations_sorted()[0]["id"]
        resp = client.get(f"/f/{sample_location}")

        assert resp.status_code == 200
        assert b"When to Fish" in resp.data
        assert b"Today's Tides" in resp.data
        assert b"Surf &amp; Pier Fishing Outlook" in resp.data

    def test_setup_select_requires_csrf(self, client):
        """POST to /setup/select without a CSRF token returns 400."""
        from locations import all_locations_sorted

        loc_id = all_locations_sorted()[0]["id"]
        resp = client.post(f"/setup/select/{loc_id}", data={})
        assert resp.status_code == 400


def test_live_cams_tab_present_in_nav():
    from pathlib import Path

    nav = Path("templates/partials/_main_nav.html").read_text(encoding="utf-8")
    assert "Live Cams" in nav


def test_account_nav_is_single_link():
    from pathlib import Path

    nav = Path("templates/partials/_main_nav.html").read_text(encoding="utf-8")
    assert "Account" in nav
    assert "app-nav-submenu" not in nav
    assert "views.setup" not in nav
    assert "views.profile" not in nav


def test_live_cams_have_dedicated_template():
    from pathlib import Path

    template = Path("templates/live_cams.html").read_text(encoding="utf-8")
    assert "Open live cam" in template
    assert "live-cam-status" in template
