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
    def test_index_redirects_to_setup(self, client):
        """Without a location set, index should redirect to setup."""
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/setup" in resp.headers["Location"]

    def test_setup_page_loads(self, client):
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert b"Choose" in resp.data or b"location" in resp.data.lower()

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


    def test_setup_anon_favorite_toggle_is_client_side(self, client):
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert b'data-favorite-btn' in resp.data
        assert b'/setup/favorite/' not in resp.data


def test_live_cams_tab_present_in_nav():
    from pathlib import Path
    nav = Path("templates/partials/_main_nav.html").read_text(encoding="utf-8")
    assert "Live Cams" in nav




def test_location_and_profile_live_under_account_nav():
    from pathlib import Path
    nav = Path("templates/partials/_main_nav.html").read_text(encoding="utf-8")
    assert "Account" in nav
    assert "app-nav-submenu" in nav
    assert "views.setup" in nav
    assert "views.profile" in nav

def test_live_cams_have_dedicated_template():
    from pathlib import Path
    template = Path("templates/live_cams.html").read_text(encoding="utf-8")
    assert "Open live cam" in template
    assert "live-cam-status" in template
