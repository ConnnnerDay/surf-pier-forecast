"""Tests for web/views.py page routes — dashboard, setup flow, profile,
live cams, fishing log, and the _render_forecast cache/refresh branches.
Previously these routes were only exercised indirectly via test_app.py.
"""

from __future__ import annotations

import pytest

from locations import all_locations_sorted
from storage.sqlite import create_user, save_preferences


def _login_session(client, user_id, location_id=None):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["session_version"] = 0
        if location_id:
            sess["location_id"] = location_id


def _set_csrf(client, token="test-csrf-token"):
    with client.session_transaction() as sess:
        sess["csrf_token"] = token
    return token


@pytest.fixture
def sample_location_id():
    return all_locations_sorted()[0]["id"]


class TestLiveCams:
    def test_redirects_when_no_location(self, client):
        resp = client.get("/live-cams")
        assert resp.status_code == 302
        assert "/setup" in resp.headers["Location"]

    def test_renders_with_location(self, client, sample_location_id, monkeypatch):
        with client.session_transaction() as sess:
            sess["location_id"] = sample_location_id
        monkeypatch.setattr("web.views.find_nearby_live_cams", lambda *a, **kw: [])
        resp = client.get("/live-cams")
        assert resp.status_code == 200


class TestFishingLog:
    def test_redirects_when_no_location(self, client):
        resp = client.get("/fishing-log")
        assert resp.status_code == 302
        assert "/setup" in resp.headers["Location"]

    def test_renders_with_location(self, client, sample_location_id):
        with client.session_transaction() as sess:
            sess["location_id"] = sample_location_id
        resp = client.get("/fishing-log")
        assert resp.status_code == 200


class TestSetupSearch:
    def test_rate_limited(self, client, monkeypatch):
        monkeypatch.setattr("web.views._SETUP_RATE_LIMIT_MAX", 1)
        token = _set_csrf(client)
        client.post("/setup/search", data={"csrf_token": token, "zipcode": "00000"})
        resp = client.post(
            "/setup/search", data={"csrf_token": token, "zipcode": "00000"}
        )
        assert resp.status_code == 200
        assert b"Too many searches" in resp.data

    def test_invalid_zipcode_format(self, client):
        token = _set_csrf(client)
        resp = client.post(
            "/setup/search", data={"csrf_token": token, "zipcode": "abc"}
        )
        assert resp.status_code == 200
        assert b"valid 5-digit" in resp.data

    def test_geocode_failure(self, client, monkeypatch):
        monkeypatch.setattr("web.views.geocode_zip", lambda zipcode: None)
        token = _set_csrf(client)
        resp = client.post(
            "/setup/search", data={"csrf_token": token, "zipcode": "99999"}
        )
        assert resp.status_code == 200
        assert b"Could not find zip code" in resp.data

    def test_no_nearby_locations(self, client, monkeypatch):
        # Inland zip: no curated spot and no coastal anchor → graceful error.
        monkeypatch.setattr("web.views.geocode_zip", lambda zipcode: (0.0, 0.0))
        monkeypatch.setattr("web.views.find_nearest_locations", lambda lat, lng, n=6: [])
        monkeypatch.setattr("web.views.dynamic_location_for_point", lambda lat, lng: None)
        token = _set_csrf(client)
        resp = client.post(
            "/setup/search", data={"csrf_token": token, "zipcode": "12345"}
        )
        assert resp.status_code == 200
        assert b"closer to the coast" in resp.data

    def test_coastal_point_offers_exact_spot(self, client, monkeypatch):
        # No curated spot nearby, but the point is coastal → offer an exact
        # forecast instead of dead-ending.
        monkeypatch.setattr("web.views.geocode_zip", lambda zipcode: (41.3, -72.9))
        monkeypatch.setattr("web.views.find_nearest_locations", lambda lat, lng, n=6: [])
        monkeypatch.setattr(
            "web.views.dynamic_location_for_point",
            lambda lat, lng: {
                "id": "pt_41.3000_-72.9000",
                "name": "Coastal spot (41.30, -72.90)",
                "state": "CT",
            },
        )
        token = _set_csrf(client)
        resp = client.post(
            "/setup/search", data={"csrf_token": token, "zipcode": "06510"}
        )
        assert resp.status_code == 200
        assert b"Your Exact Spot" in resp.data

    def test_exact_suppressed_when_curated_spot_is_close(self, client, monkeypatch):
        # A curated spot within the redundancy radius should hide the generic
        # exact-point option (the named spot is the better pick).
        monkeypatch.setattr("web.views.geocode_zip", lambda zipcode: (34.2, -77.8))
        monkeypatch.setattr(
            "web.views.find_nearest_locations",
            lambda lat, lng, n=6: [
                {"id": "x", "name": "Test Loc", "state": "NC", "distance_miles": 2.0}
            ],
        )
        called = {"n": 0}

        def _should_not_run(lat, lng):
            called["n"] += 1
            return {"id": "pt_x", "name": "Coastal spot", "state": "NC"}

        monkeypatch.setattr("web.views.dynamic_location_for_point", _should_not_run)
        token = _set_csrf(client)
        resp = client.post(
            "/setup/search", data={"csrf_token": token, "zipcode": "28401"}
        )
        assert resp.status_code == 200
        assert b"Your Exact Spot" not in resp.data
        assert called["n"] == 0  # short-circuited, never resolved an exact point

    def test_success_shows_results(self, client, monkeypatch, sample_location_id):
        monkeypatch.setattr("web.views.geocode_zip", lambda zipcode: (34.2, -77.8))
        monkeypatch.setattr(
            "web.views.find_nearest_locations",
            lambda lat, lng, n=6: [{"id": sample_location_id, "name": "Test Loc"}],
        )
        token = _set_csrf(client)
        resp = client.post(
            "/setup/search", data={"csrf_token": token, "zipcode": "28401"}
        )
        assert resp.status_code == 200
        assert b"Test Loc" in resp.data


class TestSetupCoords:
    def test_rate_limited(self, client, monkeypatch):
        monkeypatch.setattr("web.views._SETUP_RATE_LIMIT_MAX", 1)
        token = _set_csrf(client)
        client.post(
            "/setup/coords",
            data={"csrf_token": token, "location_lat": "34.2", "location_lon": "-77.8"},
        )
        resp = client.post(
            "/setup/coords",
            data={"csrf_token": token, "location_lat": "34.2", "location_lon": "-77.8"},
        )
        assert resp.status_code == 200
        assert b"Too many searches" in resp.data

    def test_invalid_coords(self, client):
        token = _set_csrf(client)
        resp = client.post(
            "/setup/coords",
            data={"csrf_token": token, "location_lat": "abc", "location_lon": "xyz"},
        )
        assert resp.status_code == 200
        assert b"Invalid coordinates" in resp.data

    def test_out_of_range_coords(self, client):
        token = _set_csrf(client)
        resp = client.post(
            "/setup/coords",
            data={"csrf_token": token, "location_lat": "999", "location_lon": "0"},
        )
        assert resp.status_code == 200
        assert b"Coordinates out of range" in resp.data

    def test_no_nearby_locations(self, client, monkeypatch):
        # Inland point: no curated spot and no coastal anchor → graceful error.
        monkeypatch.setattr("web.views.find_nearest_locations", lambda lat, lng, n=6: [])
        monkeypatch.setattr("web.views.dynamic_location_for_point", lambda lat, lng: None)
        token = _set_csrf(client)
        resp = client.post(
            "/setup/coords",
            data={"csrf_token": token, "location_lat": "39.0", "location_lon": "-98.0"},
        )
        assert resp.status_code == 200
        assert b"closer to the coast" in resp.data

    def test_success_shows_results(self, client, monkeypatch, sample_location_id):
        monkeypatch.setattr(
            "web.views.find_nearest_locations",
            lambda lat, lng, n=6: [{"id": sample_location_id, "name": "Coord Loc"}],
        )
        token = _set_csrf(client)
        resp = client.post(
            "/setup/coords",
            data={"csrf_token": token, "location_lat": "34.2", "location_lon": "-77.8"},
        )
        assert resp.status_code == 200
        assert b"Coord Loc" in resp.data


class TestSetupSelect:
    def test_unknown_location_redirects_to_setup(self, client):
        token = _set_csrf(client)
        resp = client.post(
            "/setup/select/not-a-real-location", data={"csrf_token": token}
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/setup")

    def test_anon_user_selects_location(self, client, sample_location_id, monkeypatch):
        monkeypatch.setattr("web.views.enqueue_forecast_refresh", lambda *a, **kw: None)
        token = _set_csrf(client)
        resp = client.post(
            f"/setup/select/{sample_location_id}", data={"csrf_token": token}
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")
        with client.session_transaction() as sess:
            assert sess["location_id"] == sample_location_id

    def test_logged_in_user_needing_profile_redirects_to_profile(
        self, client, sample_location_id, monkeypatch
    ):
        monkeypatch.setattr("web.views.enqueue_forecast_refresh", lambda *a, **kw: None)
        uid = create_user("setupselect_user", "pass1234")
        _login_session(client, uid)
        token = _set_csrf(client)
        resp = client.post(
            f"/setup/select/{sample_location_id}", data={"csrf_token": token}
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/profile")

    def test_logged_in_user_with_complete_profile_goes_to_index(
        self, client, sample_location_id, monkeypatch
    ):
        monkeypatch.setattr("web.views.enqueue_forecast_refresh", lambda *a, **kw: None)
        uid = create_user("setupselect_user2", "pass1234")
        save_preferences(
            uid,
            fishing_profile={"fishing_types": ["pier"], "completed": True},
        )
        _login_session(client, uid)
        token = _set_csrf(client)
        resp = client.post(
            f"/setup/select/{sample_location_id}", data={"csrf_token": token}
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")
        assert "/profile" not in resp.headers["Location"]


class TestSetupFavorite:
    def test_anon_redirects_to_login_gate(self, client, sample_location_id):
        # setup_favorite isn't in the public-endpoints whitelist, so the
        # global before_request login gate intercepts anonymous requests
        # before the route's own `if not g.user` check ever runs.
        token = _set_csrf(client)
        resp = client.post(
            f"/setup/favorite/{sample_location_id}", data={"csrf_token": token}
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/welcome")

    def test_unknown_location_redirects_to_setup(self, client):
        uid = create_user("fav_user1", "pass1234")
        _login_session(client, uid)
        token = _set_csrf(client)
        resp = client.post(
            "/setup/favorite/not-a-real-location", data={"csrf_token": token}
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/setup")

    def test_adds_favorite(self, client, sample_location_id):
        uid = create_user("fav_user2", "pass1234")
        _login_session(client, uid)
        token = _set_csrf(client)
        resp = client.post(
            f"/setup/favorite/{sample_location_id}", data={"csrf_token": token}
        )
        assert resp.status_code == 302
        from storage.sqlite import get_preferences

        assert sample_location_id in get_preferences(uid).get("favorites", [])

    def test_toggling_again_removes_favorite(self, client, sample_location_id):
        uid = create_user("fav_user3", "pass1234")
        save_preferences(uid, favorites=[sample_location_id])
        _login_session(client, uid)
        token = _set_csrf(client)
        client.post(f"/setup/favorite/{sample_location_id}", data={"csrf_token": token})
        from storage.sqlite import get_preferences

        assert sample_location_id not in get_preferences(uid).get("favorites", [])

    def test_redirects_to_safe_relative_next_url(self, client, sample_location_id):
        uid = create_user("fav_user4", "pass1234")
        _login_session(client, uid)
        token = _set_csrf(client)
        resp = client.post(
            f"/setup/favorite/{sample_location_id}",
            data={"csrf_token": token, "next": "/live-cams"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/live-cams")

    def test_rejects_external_next_url(self, client, sample_location_id):
        uid = create_user("fav_user5", "pass1234")
        _login_session(client, uid)
        token = _set_csrf(client)
        resp = client.post(
            f"/setup/favorite/{sample_location_id}",
            data={"csrf_token": token, "next": "https://evil.example/phish"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/setup")

    def test_rejects_protocol_relative_next_url(self, client, sample_location_id):
        uid = create_user("fav_user6", "pass1234")
        _login_session(client, uid)
        token = _set_csrf(client)
        resp = client.post(
            f"/setup/favorite/{sample_location_id}",
            data={"csrf_token": token, "next": "//evil.example/phish"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/setup")


class TestProfileRoute:
    def test_anon_redirects_to_login_gate(self, client):
        # /profile isn't in the public-endpoints whitelist either, so anon
        # users are redirected by the global before_request gate.
        resp = client.get("/profile")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/welcome")

    def test_logged_in_renders(self, client):
        uid = create_user("profile_user", "pass1234")
        _login_session(client, uid)
        resp = client.get("/profile")
        assert resp.status_code == 200


class TestIndexRedirects:
    def test_anon_with_no_location_redirects_to_landing(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/welcome")


class TestRenderForecastBranches:
    def test_background_refresh_shows_loading_page(
        self, client, sample_location_id, monkeypatch
    ):
        monkeypatch.setattr("web.views.load_cached_forecast", lambda *a, **kw: None)
        monkeypatch.setattr("web.views._is_refreshing", lambda loc_id: True)
        with client.session_transaction() as sess:
            sess["location_id"] = sample_location_id
        resp = client.get("/")
        assert resp.status_code == 200

    def test_generate_failure_renders_error_page(
        self, client, sample_location_id, monkeypatch
    ):
        monkeypatch.setattr("web.views.load_cached_forecast", lambda *a, **kw: None)
        monkeypatch.setattr("web.views._is_refreshing", lambda loc_id: False)

        def _boom(location):
            raise RuntimeError("forecast generation failed")

        monkeypatch.setattr("web.views.generate_forecast", _boom)
        with client.session_transaction() as sess:
            sess["location_id"] = sample_location_id
        resp = client.get("/")
        assert resp.status_code == 500
        assert b"Could not load forecast" in resp.data

    def test_stale_forecast_triggers_background_refresh(
        self, client, sample_location_id, monkeypatch
    ):
        # Populate the cache via the real cache-miss -> generate_forecast path
        # first (proven to work offline elsewhere, e.g. test_app.py), then
        # re-request with the age check forced over the staleness threshold.
        with client.session_transaction() as sess:
            sess["location_id"] = sample_location_id
        first = client.get("/")
        assert first.status_code == 200

        monkeypatch.setattr("web.views._forecast_age_minutes", lambda forecast: 99999)
        called = {}
        monkeypatch.setattr(
            "web.views.enqueue_forecast_refresh",
            lambda loc_id, user_id=None: called.setdefault("enqueued", True),
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert called.get("enqueued") is True
