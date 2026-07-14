"""Extended tests for web/views.py covering missed branches.

Targets the lines missing from the full-suite report:
100, 163-165, 176-180, 273, 298, 317, 373-390, 435, 453-454,
483, 485, 487-489, 494-497, 514-516, 551, 569-571, 703, 737.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta

import pytest

from locations import all_locations_sorted
from storage.sqlite import create_user, save_preferences


def _fresh_ts() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()


def _login(client, user_id, location_id=None):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["session_version"] = 0
        if location_id:
            sess["location_id"] = location_id


def _csrf(client, token="test-csrf"):
    with client.session_transaction() as sess:
        sess["csrf_token"] = token
    return token


@pytest.fixture
def loc_id():
    return all_locations_sorted()[0]["id"]


def _fake_forecast(location_id, state="NC", **extra):
    """Minimal forecast dict that lets the template render without errors."""
    f = {
        "generated_at": _fresh_ts(),
        "location_id": location_id,
        "location_state": state,
        "official_regulations_url": "https://example.com",
        "conditions": {"wind": "5-10 kt", "wave_height_ft": 2.0, "temp_f": 75},
        "outlook": [],
        "species_ranking": [],
        "tide": {},
        "moon_phase": "Full",
        "solunar": {},
        "age_human": "5 min ago",
        "score": 70,
        "score_label": "Fair",
        "verdict": "Fair",
        "alerts": [],
        "tides": [],
        "sunrise": "6:00 AM",
        "sunset": "8:00 PM",
    }
    f.update(extra)
    return f


def _patch_render(monkeypatch):
    """Monkeypatch web.views.render_template to return a trivial 200 response."""
    from flask import make_response

    def _fake(template, **ctx):
        return make_response(f"ok:{template}", 200)

    monkeypatch.setattr("web.views.render_template", _fake)


def _patch_cam_and_uv(monkeypatch):
    """Silence the cam probe and UV recompute (both make network calls)."""
    monkeypatch.setattr("web.views._build_live_cam_context", lambda *a, **kw: {})
    monkeypatch.setattr("web.views.recompute_current_uv", lambda *a: None)


# ---------------------------------------------------------------------------
# Line 551: index() with logged-in user but no location → /setup
# ---------------------------------------------------------------------------


class TestIndexLoggedInNoLocation:
    def test_redirects_to_setup_when_no_location(self, client):
        """Line 551: `return redirect(url_for("views.setup"))` when user is logged in
        but no location_id is stored in the session."""
        uid = create_user("no_loc_user", "pass1234")
        _login(client, uid)  # NO location_id
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/setup")


# ---------------------------------------------------------------------------
# Line 306: before_request — public endpoint + user needs profile → /profile
# Line 317: before_request — non-public, non-exempt endpoint, needs profile
# ---------------------------------------------------------------------------


class TestRequireLoginProfileSetupRedirects:
    def test_public_endpoint_with_user_needing_profile_redirects(
        self, client, loc_id
    ):
        """Line 306: a public but non-profile-exempt endpoint (live-cams) with a
        logged-in user who has a location but no fishing profile → /profile."""
        uid = create_user("pub_needs_profile", "pass1234")
        save_preferences(uid, location_id=loc_id)  # has location, no profile
        _login(client, uid, location_id=loc_id)
        resp = client.get("/live-cams", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/profile")

    def test_non_public_endpoint_needing_profile_redirects(
        self, app, loc_id, monkeypatch
    ):
        """Line 317: _require_login redirects to /profile when the endpoint is
        non-public and non-exempt and the user needs profile setup.
        All view routes are currently either public or exempt, so we call
        _require_login directly inside a request context with the exempt set
        cleared."""
        from web.views import _require_login
        import flask

        uid = create_user("nonpub_needs_profile", "pass1234")
        save_preferences(uid, location_id=loc_id)  # has location, no profile

        # Remove setup_favorite from the exempt set so the non-exempt branch fires.
        monkeypatch.setattr("web.views._PROFILE_SETUP_EXEMPT_ENDPOINTS", set())

        with app.test_request_context(
            f"/setup/favorite/{loc_id}", method="POST"
        ):
            flask.g.user = {"id": uid, "username": "nonpub_needs_profile"}
            result = _require_login()

        assert result is not None
        assert result.status_code == 302
        assert "/profile" in result.headers["Location"]


# ---------------------------------------------------------------------------
# Lines 373-390: _extract_profile_from_request body — params present
# ---------------------------------------------------------------------------


class TestExtractProfileFromRequest:
    def test_all_profile_params_processed(self, client, loc_id, monkeypatch):
        """Lines 373-390: when fishing_types/targets/experience/bait params are
        in the query string, _extract_profile_from_request populates profile."""
        fc = _fake_forecast(loc_id)
        monkeypatch.setattr("web.views.load_cached_forecast", lambda *a, **kw: fc)
        _patch_cam_and_uv(monkeypatch)
        _patch_render(monkeypatch)

        resp = client.get(
            f"/f/{loc_id}"
            "?fishing_types=pier,surf&targets=drum&experience=beginner"
            "&live_bait=yes&cut_bait=no&lures=sometimes"
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Line 435: cached_flag reset to None after fresh forecast generation
# ---------------------------------------------------------------------------


class TestCachedFlagReset:
    def test_cached_flag_cleared_after_generation(self, client, loc_id, monkeypatch):
        """Line 435: when the cache is empty and ?cached=refreshing is requested,
        cached_flag is reset to None after successful generation."""
        fc = _fake_forecast(loc_id)
        monkeypatch.setattr("web.views.load_cached_forecast", lambda *a, **kw: None)
        monkeypatch.setattr("web.views._is_refreshing", lambda _: False)
        monkeypatch.setattr("web.views.generate_forecast", lambda _loc: fc)
        monkeypatch.setattr("web.views.save_forecast", lambda *a, **kw: None)
        _patch_cam_and_uv(monkeypatch)
        _patch_render(monkeypatch)

        uid = create_user("cachedflag_user", "pass1234")
        save_preferences(
            uid,
            location_id=loc_id,
            fishing_profile={"fishing_types": ["pier"], "completed": True},
        )
        _login(client, uid, location_id=loc_id)
        resp = client.get("/?cached=refreshing")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 453-454: exception in get_log_stats is swallowed
# ---------------------------------------------------------------------------


class TestLogStatsException:
    def test_log_stats_exception_swallowed(self, client, loc_id, monkeypatch):
        """Lines 453-454: RuntimeError from get_log_stats is caught; page still renders."""
        uid = create_user("log_exc_user", "pass1234")
        save_preferences(
            uid,
            location_id=loc_id,
            fishing_profile={"fishing_types": ["pier"], "completed": True},
        )
        fc = _fake_forecast(loc_id)
        monkeypatch.setattr("web.views.load_cached_forecast", lambda *a, **kw: fc)
        monkeypatch.setattr(
            "web.views.get_log_stats", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db"))
        )
        _patch_cam_and_uv(monkeypatch)
        _patch_render(monkeypatch)
        _login(client, uid, location_id=loc_id)
        resp = client.get("/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 483, 485, 487-489: forecast field backfill for cached entries
# ---------------------------------------------------------------------------


class TestForecastFieldBackfill:
    def test_missing_location_fields_backfilled(self, client, loc_id, monkeypatch):
        """Lines 483, 485, 487-489: location_id, location_state, and
        official_regulations_url are filled in when absent from the cached forecast."""
        fc = _fake_forecast(loc_id)
        fc.pop("location_id", None)
        fc.pop("location_state", None)
        fc.pop("official_regulations_url", None)
        monkeypatch.setattr("web.views.load_cached_forecast", lambda *a, **kw: dict(fc))
        _patch_cam_and_uv(monkeypatch)
        _patch_render(monkeypatch)

        uid = create_user("backfill_user", "pass1234")
        save_preferences(
            uid,
            location_id=loc_id,
            fishing_profile={"fishing_types": ["pier"], "completed": True},
        )
        _login(client, uid, location_id=loc_id)
        resp = client.get("/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 494-497: tide_chart stored as JSON string is parsed
# ---------------------------------------------------------------------------


class TestTideChartStringParsing:
    def test_tide_chart_string_parsed_to_dict(self, client, loc_id, monkeypatch):
        """Lines 494-496: a tide_chart value that is a non-empty JSON string is
        parsed back into a dict before the template renders."""
        fc = _fake_forecast(loc_id)
        fc["tide_chart"] = json.dumps({"next_high": "3:00 PM"})
        monkeypatch.setattr("web.views.load_cached_forecast", lambda *a, **kw: dict(fc))
        _patch_cam_and_uv(monkeypatch)
        _patch_render(monkeypatch)

        uid = create_user("tidechart_user", "pass1234")
        save_preferences(
            uid,
            location_id=loc_id,
            fishing_profile={"fishing_types": ["pier"], "completed": True},
        )
        _login(client, uid, location_id=loc_id)
        resp = client.get("/")
        assert resp.status_code == 200

    def test_tide_chart_invalid_string_removed(self, client, loc_id, monkeypatch):
        """Lines 496-497: if the tide_chart JSON string is invalid it is removed."""
        fc = _fake_forecast(loc_id)
        fc["tide_chart"] = "{not valid json"
        monkeypatch.setattr("web.views.load_cached_forecast", lambda *a, **kw: dict(fc))
        _patch_cam_and_uv(monkeypatch)
        _patch_render(monkeypatch)

        uid = create_user("tidechart_invalid_user", "pass1234")
        save_preferences(
            uid,
            location_id=loc_id,
            fishing_profile={"fishing_types": ["pier"], "completed": True},
        )
        _login(client, uid, location_id=loc_id)
        resp = client.get("/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 514-516: favorite locations loop builds the quick-switch bar list
# ---------------------------------------------------------------------------


class TestFavoriteLocationsInForecast:
    def test_favorites_built_for_logged_in_user(self, client, loc_id, monkeypatch):
        """Lines 514-516: user's stored favorites are resolved to {id, name} dicts."""
        uid = create_user("fav_fc_user", "pass1234")
        save_preferences(
            uid,
            location_id=loc_id,
            favorites=[loc_id],
            fishing_profile={"fishing_types": ["pier"], "completed": True},
        )
        fc = _fake_forecast(loc_id)
        monkeypatch.setattr("web.views.load_cached_forecast", lambda *a, **kw: fc)
        _patch_cam_and_uv(monkeypatch)
        _patch_render(monkeypatch)
        _login(client, uid, location_id=loc_id)
        resp = client.get("/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 569-571: live_cams loads stored profile when no query params
# ---------------------------------------------------------------------------


class TestLiveCamsStoredProfile:
    def test_stored_fishing_profile_used_for_live_cams(
        self, client, loc_id, monkeypatch
    ):
        """Lines 569-571: when no ?fishing_types param is given but the user has a
        stored profile with fishing_types, it is used for cam filtering."""
        uid = create_user("lc_stored_user", "pass1234")
        save_preferences(
            uid,
            fishing_profile={"fishing_types": ["pier"], "completed": True},
        )
        _login(client, uid, location_id=loc_id)
        monkeypatch.setattr("web.views.find_nearby_live_cams", lambda *a, **kw: [])
        resp = client.get("/live-cams")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 100, 163-165: _convert_wind_text_units and _fetch_cam_status
# ---------------------------------------------------------------------------


class TestConvertWindTextUnits:
    def test_non_mph_units_returns_unchanged(self):
        """Line 100: when wind_units is not 'mph', the text is returned unchanged."""
        from web.views import _convert_wind_text_units
        text = "10-15 kt"
        assert _convert_wind_text_units(text, "knots") == text

    def test_empty_text_returns_unchanged(self):
        """Line 100: empty text is returned unchanged regardless of units."""
        from web.views import _convert_wind_text_units
        assert _convert_wind_text_units("", "mph") == ""

    def test_kt_range_converted_to_mph(self):
        """Lines 103-105: a '10-15 kt' range is converted to mph."""
        from web.views import _convert_wind_text_units
        result = _convert_wind_text_units("Winds 10-15 kt", "mph")
        assert "mph" in result
        assert "kt" not in result

    def test_single_kt_value_converted(self):
        """Lines 109-112: a single '20 kt' value is converted to mph."""
        from web.views import _convert_wind_text_units
        result = _convert_wind_text_units("Gusts 20 kt", "mph")
        assert "mph" in result


class TestFetchCamStatus:
    def test_live_cam_is_live(self, monkeypatch):
        """Lines 163-165: a successful HTTP probe marks cam as live."""
        import requests
        from web.views import _fetch_cam_status, _cam_status_cache
        import web.views as views_mod

        mock_resp = type("R", (), {"status_code": 200})()
        monkeypatch.setattr("web.views.requests", type(
            "Mod", (), {
                "get": lambda *a, **kw: mock_resp,
                "RequestException": requests.RequestException,
            }
        )())
        # Use a unique URL to avoid cache hits from other tests.
        url = "http://test-cam-live.example/stream"
        with views_mod._cam_status_lock:
            views_mod._cam_status_cache.pop(url, None)

        _fetch_cam_status(url)

        with views_mod._cam_status_lock:
            status = views_mod._cam_status_cache.get(url)
        assert status is not None
        assert status["is_live"] is True
        assert status["status_label"] == "Live now"

    def test_cam_cache_eviction(self, monkeypatch):
        """Lines 176-180: when cam cache is full, oldest entry is evicted."""
        from web.views import _fetch_cam_status
        import web.views as views_mod

        # Fill the cache to max capacity with fake entries.
        monkeypatch.setattr("web.views._CAM_STATUS_CACHE_MAX", 2)
        now = time.time()
        with views_mod._cam_status_lock:
            views_mod._cam_status_cache.clear()
            views_mod._cam_status_cache["http://old.example/"] = {
                "is_live": False, "checked_at_ts": now - 1000
            }
            views_mod._cam_status_cache["http://new.example/"] = {
                "is_live": False, "checked_at_ts": now
            }

        import requests

        mock_resp = type("R", (), {"status_code": 200})()
        monkeypatch.setattr("web.views.requests", type(
            "Mod", (), {
                "get": lambda *a, **kw: mock_resp,
                "RequestException": requests.RequestException,
            }
        )())
        # Adding a third URL exceeds the max and should evict the oldest.
        _fetch_cam_status("http://evict-trigger.example/")

        with views_mod._cam_status_lock:
            remaining = set(views_mod._cam_status_cache.keys())
        assert "http://old.example/" not in remaining


# ---------------------------------------------------------------------------
# Lines 273, 298: direct function-call paths (defense-in-depth guards)
# ---------------------------------------------------------------------------


class TestUserRequiresProfileSetupGuard:
    def test_returns_false_when_no_user(self, app):
        """Line 273: _user_requires_profile_setup() returns False when g.user is None."""
        from web.views import _user_requires_profile_setup
        import flask

        with app.test_request_context("/"):
            flask.g.user = None
            assert _user_requires_profile_setup() is False


class TestRequireLoginEndpointNone:
    def test_returns_none_when_endpoint_is_none(self, app):
        """Line 298: _require_login returns None when request.endpoint is None."""
        from web.views import _require_login
        import flask

        with app.test_request_context("/nonexistent-404"):
            # Simulate Flask not matching any route (endpoint stays None).
            assert flask.request.endpoint is None
            result = _require_login()
            assert result is None


# ---------------------------------------------------------------------------
# Lines 703, 737: defense-in-depth guards in route functions
# ---------------------------------------------------------------------------


class TestSetupFavoriteGuard:
    def test_no_user_redirects_to_setup(self, app, loc_id):
        """Line 703: setup_favorite redirects to /setup if g.user is None
        (defense-in-depth; before_request normally intercepts first)."""
        from web.views import setup_favorite
        import flask

        with app.test_request_context(
            f"/setup/favorite/{loc_id}", method="POST"
        ):
            flask.g.user = None
            resp = setup_favorite(loc_id)
        assert resp.status_code == 302
        assert "/setup" in resp.headers["Location"]


class TestProfileRouteGuard:
    def test_no_user_redirects_to_login(self, app):
        """Line 737: profile() redirects to /login if g.user is None
        (defense-in-depth; before_request normally intercepts first)."""
        from web.views import profile
        import flask

        with app.test_request_context("/profile"):
            flask.g.user = None
            resp = profile()
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
