"""Tests for the v2 AI Fishing Map — community catches, social features, and
extended fishing-map filters.

These tests exercise:
  - New DB DAL functions (add_map_catch, toggle_map_catch_like, etc.)
  - GET /api/map/catches        (bbox fetch, auth vs public)
  - POST /api/map/catches       (create catch pin, auth required)
  - DELETE /api/map/catches/<id>  (only owner can delete)
  - POST /api/map/catches/<id>/like
  - GET/POST /api/map/catches/<id>/comments
  - GET /api/map/feed
  - GET /api/map/hotspots
  - GET /api/fishing-map  (new season/time_of_day/tide_phase/water-temp filters)
"""

from __future__ import annotations

import json
import pytest

from app import create_app
from storage.sqlite import (
    add_map_catch,
    add_map_catch_comment,
    confirm_email,
    create_user,
    get_community_hotspots,
    get_map_catch,
    get_map_catch_comments,
    get_map_catches_in_bbox,
    get_recent_public_catches,
    get_db,
    init_db,
    toggle_map_catch_like,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_fmapv2.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    init_db()
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(username="angler1", password="pass123"):
    uid = create_user(username, password, email=f"{username}@test.example")
    assert uid is not None
    # Confirm email so user can access authenticated endpoints
    confirm_email(uid)
    return uid


def _login(client, username="angler1", password="pass123"):
    """Log in via direct session injection (bypasses email-confirmation redirect)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, session_version FROM users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"User '{username}' not found"
    with client.session_transaction() as sess:
        sess["user_id"] = row["id"]
        sess["session_version"] = row["session_version"]


# ── DB-layer unit tests ───────────────────────────────────────────────────────


class TestAddMapCatch:
    def test_creates_catch_and_returns_id(self, app):
        with app.app_context():
            uid = _make_user("tester_create")
            catch_id = add_map_catch(uid, 40.7128, -74.0060, "Striped bass")
            assert catch_id > 0

    def test_catch_is_retrievable(self, app):
        with app.app_context():
            uid = _make_user("tester_get")
            catch_id = add_map_catch(uid, 40.0, -73.0, "Bluefish", bait="live bunker",
                                     weight_lb=4.5, length_in=22.0, notes="Good morning bite",
                                     is_public=True)
            row = get_map_catch(catch_id)
            assert row is not None
            assert row["species"] == "Bluefish"
            assert row["bait"] == "live bunker"
            assert abs(row["weight_lb"] - 4.5) < 0.01
            assert row["is_public"] == 1

    def test_private_catch_stored_correctly(self, app):
        with app.app_context():
            uid = _make_user("tester_priv")
            catch_id = add_map_catch(uid, 35.0, -76.0, "Red drum", is_public=False)
            row = get_map_catch(catch_id)
            assert row["is_public"] == 0


class TestGetMapCatchesInBbox:
    def test_returns_public_catches_in_bbox(self, app):
        with app.app_context():
            uid = _make_user("bbox_user")
            add_map_catch(uid, 40.0, -74.0, "Flounder", is_public=True)
            add_map_catch(uid, 41.0, -74.5, "Striper",  is_public=True)
            add_map_catch(uid, 50.0, -74.0, "Cod",      is_public=True)  # outside bbox
            catches = get_map_catches_in_bbox(39.0, -75.0, 42.0, -73.0)
            species = {c["species"] for c in catches}
            assert "Flounder" in species
            assert "Striper" in species
            assert "Cod" not in species

    def test_private_catch_hidden_from_non_owner(self, app):
        with app.app_context():
            uid1 = _make_user("owner")
            uid2 = _make_user("viewer")
            add_map_catch(uid1, 40.0, -74.0, "Tautog", is_public=False)
            # viewer_user_id = uid2 should not see uid1's private catch
            catches = get_map_catches_in_bbox(39.0, -75.0, 42.0, -73.0,
                                              viewer_user_id=uid2)
            assert not any(c["species"] == "Tautog" for c in catches)

    def test_owner_sees_own_private_catch(self, app):
        with app.app_context():
            uid = _make_user("priv_owner")
            add_map_catch(uid, 40.0, -74.0, "Weakfish", is_public=False)
            catches = get_map_catches_in_bbox(39.0, -75.0, 42.0, -73.0,
                                              viewer_user_id=uid)
            assert any(c["species"] == "Weakfish" for c in catches)

    def test_species_filter(self, app):
        with app.app_context():
            uid = _make_user("sf_user")
            add_map_catch(uid, 40.0, -74.0, "Striped bass")
            add_map_catch(uid, 40.0, -74.0, "Bluefish")
            catches = get_map_catches_in_bbox(39.0, -75.0, 42.0, -73.0,
                                              species_filter="striped")
            assert all("striped" in c["species"].lower() for c in catches)


class TestToggleLike:
    def test_like_increments_count(self, app):
        with app.app_context():
            uid1 = _make_user("like_owner")
            uid2 = _make_user("like_user")
            cid = add_map_catch(uid1, 40.0, -74.0, "Porgies")
            liked, count = toggle_map_catch_like(cid, uid2)
            assert liked is True
            assert count == 1

    def test_unlike_decrements_count(self, app):
        with app.app_context():
            uid1 = _make_user("unlike_owner")
            uid2 = _make_user("unlike_user")
            cid = add_map_catch(uid1, 40.0, -74.0, "Sheepshead")
            toggle_map_catch_like(cid, uid2)  # like
            liked, count = toggle_map_catch_like(cid, uid2)  # unlike
            assert liked is False
            assert count == 0

    def test_multiple_users_like(self, app):
        with app.app_context():
            uid0 = _make_user("ml_owner")
            uid1 = _make_user("ml_user1")
            uid2 = _make_user("ml_user2")
            cid = add_map_catch(uid0, 40.0, -74.0, "Black drum")
            toggle_map_catch_like(cid, uid1)
            _, count = toggle_map_catch_like(cid, uid2)
            assert count == 2


class TestMapCatchComments:
    def test_add_and_retrieve_comment(self, app):
        with app.app_context():
            uid1 = _make_user("comm_owner")
            uid2 = _make_user("commenter")
            cid = add_map_catch(uid1, 40.0, -74.0, "Kingfish")
            comment_id = add_map_catch_comment(cid, uid2, "Nice catch!")
            assert comment_id > 0
            comments = get_map_catch_comments(cid)
            assert len(comments) == 1
            assert comments[0]["body"] == "Nice catch!"

    def test_multiple_comments_ordered_asc(self, app):
        with app.app_context():
            uid = _make_user("mc_user")
            cid = add_map_catch(uid, 40.0, -74.0, "Pollock")
            add_map_catch_comment(cid, uid, "First comment")
            add_map_catch_comment(cid, uid, "Second comment")
            comments = get_map_catch_comments(cid)
            assert comments[0]["body"] == "First comment"
            assert comments[1]["body"] == "Second comment"


class TestCommunityHotspots:
    def test_returns_empty_when_no_catches(self, app):
        with app.app_context():
            hotspots = get_community_hotspots(days_back=30)
            assert hotspots == []

    def test_aggregates_nearby_catches(self, app):
        with app.app_context():
            uid = _make_user("hs_user")
            # Three catches within 0.1° of each other
            add_map_catch(uid, 40.00, -74.00, "Striped bass")
            add_map_catch(uid, 40.05, -74.02, "Striped bass")
            add_map_catch(uid, 40.08, -74.03, "Bluefish")
            hotspots = get_community_hotspots(days_back=30)
            assert len(hotspots) >= 1
            assert hotspots[0]["catch_count"] >= 2


class TestRecentPublicCatches:
    def test_only_returns_public(self, app):
        with app.app_context():
            uid = _make_user("rpc_user")
            add_map_catch(uid, 40.0, -74.0, "Cobia", is_public=True)
            add_map_catch(uid, 40.0, -74.0, "Permit", is_public=False)
            catches = get_recent_public_catches(limit=50)
            species = {c["species"] for c in catches}
            assert "Cobia" in species
            assert "Permit" not in species

    def test_species_filter(self, app):
        with app.app_context():
            uid = _make_user("rpc_sf")
            add_map_catch(uid, 40.0, -74.0, "Wahoo")
            add_map_catch(uid, 40.0, -74.0, "Mahi-mahi")
            catches = get_recent_public_catches(species_filter="wahoo")
            assert all("wahoo" in c["species"].lower() for c in catches)


# ── API endpoint tests ────────────────────────────────────────────────────────


class TestMapCatchesAPI:
    """Tests for GET/POST /api/map/catches"""

    def test_get_catches_requires_bbox(self, client):
        rv = client.get("/api/map/catches")
        assert rv.status_code == 400
        data = json.loads(rv.data)
        assert "error" in data

    def test_get_catches_valid_bbox(self, client):
        rv = client.get("/api/map/catches?sw_lat=39&sw_lng=-75&ne_lat=42&ne_lng=-73")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "catches" in data

    def test_get_catches_invalid_bbox_reversed(self, client):
        # sw_lat > ne_lat is invalid
        rv = client.get("/api/map/catches?sw_lat=42&sw_lng=-75&ne_lat=39&ne_lng=-73")
        assert rv.status_code == 400

    def test_post_catch_requires_auth(self, client):
        rv = client.post(
            "/api/map/catches",
            data=json.dumps({"lat": 40.0, "lng": -74.0, "species": "Bass"}),
            content_type="application/json",
        )
        assert rv.status_code == 401

    def test_post_catch_with_auth(self, app, client):
        with app.app_context():
            _make_user("poster")
        _login(client, "poster")
        rv = client.post(
            "/api/map/catches",
            data=json.dumps({"lat": 40.7, "lng": -74.0, "species": "Striped bass",
                             "bait": "bunker", "weight_lb": 12.5}),
            content_type="application/json",
        )
        assert rv.status_code == 201
        data = json.loads(rv.data)
        assert "id" in data
        assert data["id"] > 0

    def test_post_catch_missing_species(self, app, client):
        with app.app_context():
            _make_user("poster2")
        _login(client, "poster2")
        rv = client.post(
            "/api/map/catches",
            data=json.dumps({"lat": 40.0, "lng": -74.0}),
            content_type="application/json",
        )
        assert rv.status_code == 400

    def test_post_catch_invalid_coords(self, app, client):
        with app.app_context():
            _make_user("poster3")
        _login(client, "poster3")
        rv = client.post(
            "/api/map/catches",
            data=json.dumps({"lat": 999.0, "lng": -74.0, "species": "Bass"}),
            content_type="application/json",
        )
        assert rv.status_code == 400


class TestMapCatchDeleteAPI:
    def test_delete_own_catch(self, app, client):
        with app.app_context():
            uid = _make_user("deleter")
            cid = add_map_catch(uid, 40.0, -74.0, "Flounder")
        _login(client, "deleter")
        rv = client.delete(f"/api/map/catches/{cid}", content_type="application/json")
        assert rv.status_code == 200

    def test_delete_other_catch_returns_404(self, app, client):
        with app.app_context():
            uid1 = _make_user("del_owner")
            uid2 = _make_user("del_thief")
            cid = add_map_catch(uid1, 40.0, -74.0, "Eel")
        _login(client, "del_thief")
        rv = client.delete(f"/api/map/catches/{cid}", content_type="application/json")
        assert rv.status_code == 404

    def test_delete_requires_auth(self, client):
        rv = client.delete("/api/map/catches/9999", content_type="application/json")
        assert rv.status_code == 401


class TestMapCatchLikeAPI:
    def test_like_requires_auth(self, client, app):
        with app.app_context():
            uid = _make_user("like_setup")
            cid = add_map_catch(uid, 40.0, -74.0, "Pompano")
        rv = client.post(f"/api/map/catches/{cid}/like",
                         content_type="application/json")
        assert rv.status_code == 401

    def test_like_returns_count(self, app, client):
        with app.app_context():
            uid1 = _make_user("like_owner2")
            cid = add_map_catch(uid1, 40.0, -74.0, "Jack crevalle")
            _make_user("liker")
        _login(client, "liker")
        rv = client.post(f"/api/map/catches/{cid}/like",
                         content_type="application/json")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "liked" in data
        assert "likes_count" in data
        assert data["likes_count"] == 1

    def test_unlike_by_posting_again(self, app, client):
        with app.app_context():
            uid = _make_user("unlike_owner2")
            cid = add_map_catch(uid, 40.0, -74.0, "Kingfish2")
            _make_user("unliker")
        _login(client, "unliker")
        client.post(f"/api/map/catches/{cid}/like",
                    content_type="application/json")  # like
        rv = client.post(f"/api/map/catches/{cid}/like",
                         content_type="application/json")  # unlike
        data = json.loads(rv.data)
        assert data["liked"] is False
        assert data["likes_count"] == 0


class TestMapCatchCommentsAPI:
    def test_get_comments_public_catch(self, app, client):
        with app.app_context():
            uid = _make_user("gc_owner")
            cid = add_map_catch(uid, 40.0, -74.0, "Snook", is_public=True)
            add_map_catch_comment(cid, uid, "Hello world")
        rv = client.get(f"/api/map/catches/{cid}/comments")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert len(data["comments"]) == 1
        assert data["comments"][0]["body"] == "Hello world"

    def test_post_comment_requires_auth(self, app, client):
        with app.app_context():
            uid = _make_user("pc_owner")
            cid = add_map_catch(uid, 40.0, -74.0, "Tarpon")
        rv = client.post(
            f"/api/map/catches/{cid}/comments",
            data=json.dumps({"body": "nice fish!"}),
            content_type="application/json",
        )
        assert rv.status_code == 401

    def test_post_comment_with_auth(self, app, client):
        with app.app_context():
            uid1 = _make_user("pc_owner2")
            cid = add_map_catch(uid1, 40.0, -74.0, "Bonefish")
            _make_user("commenter2")
        _login(client, "commenter2")
        rv = client.post(
            f"/api/map/catches/{cid}/comments",
            data=json.dumps({"body": "Beautiful specimen!"}),
            content_type="application/json",
        )
        assert rv.status_code == 201
        data = json.loads(rv.data)
        assert data["id"] > 0

    def test_post_empty_comment_rejected(self, app, client):
        with app.app_context():
            uid = _make_user("ec_owner")
            cid = add_map_catch(uid, 40.0, -74.0, "Permit2")
            _make_user("ec_commenter")
        _login(client, "ec_commenter")
        rv = client.post(
            f"/api/map/catches/{cid}/comments",
            data=json.dumps({"body": "   "}),
            content_type="application/json",
        )
        assert rv.status_code == 400


class TestMapFeedAPI:
    def test_feed_returns_public_catches(self, app, client):
        with app.app_context():
            uid = _make_user("feed_user")
            add_map_catch(uid, 40.0, -74.0, "Bluefish", is_public=True)
            add_map_catch(uid, 40.0, -74.0, "Secret fish", is_public=False)
        rv = client.get("/api/map/feed")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        species = {c["species"] for c in data["catches"]}
        assert "Bluefish" in species
        assert "Secret fish" not in species

    def test_feed_limit_param(self, app, client):
        with app.app_context():
            uid = _make_user("feed_limit")
            for i in range(10):
                add_map_catch(uid, 40.0 + i * 0.01, -74.0, f"Species {i}")
        rv = client.get("/api/map/feed?limit=5")
        data = json.loads(rv.data)
        assert len(data["catches"]) <= 5

    def test_feed_limit_capped_at_50(self, client):
        rv = client.get("/api/map/feed?limit=9999")
        assert rv.status_code == 200


class TestMapHotspotsAPI:
    def test_hotspots_returns_json(self, client):
        rv = client.get("/api/map/hotspots")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "hotspots" in data
        assert "days_back" in data

    def test_hotspots_with_catches(self, app, client):
        with app.app_context():
            uid = _make_user("hs_api_user")
            add_map_catch(uid, 40.0, -74.0, "Blackfish")
            add_map_catch(uid, 40.05, -74.02, "Blackfish")
        rv = client.get("/api/map/hotspots?days_back=30")
        data = json.loads(rv.data)
        assert isinstance(data["hotspots"], list)

    def test_days_back_capped_at_90(self, client):
        rv = client.get("/api/map/hotspots?days_back=9999")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["days_back"] == 90


class TestFishingMapExtendedFilters:
    """Test that the extended filters on /api/fishing-map work correctly."""

    def test_basic_response_unchanged(self, client):
        rv = client.get("/api/fishing-map")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "locations" in data
        assert "month" in data

    def test_season_filter_spring(self, client):
        rv = client.get("/api/fishing-map?season=spring")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data.get("season") == "spring"
        # Month should be the middle month of spring (April = 4)
        assert data["month"] == 4

    def test_season_filter_summer(self, client):
        rv = client.get("/api/fishing-map?season=summer")
        data = json.loads(rv.data)
        assert data["month"] == 7

    def test_season_filter_fall(self, client):
        rv = client.get("/api/fishing-map?season=fall")
        data = json.loads(rv.data)
        assert data["month"] == 10

    def test_season_filter_winter(self, client):
        rv = client.get("/api/fishing-map?season=winter")
        data = json.loads(rv.data)
        assert data["month"] == 1

    def test_explicit_month_overrides_season(self, client):
        rv = client.get("/api/fishing-map?season=summer&month=3")
        data = json.loads(rv.data)
        # Explicit month=3 should take precedence
        assert data["month"] == 3

    def test_time_of_day_filter(self, client):
        rv = client.get("/api/fishing-map?time_of_day=dawn")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data.get("time_of_day") == "dawn"

    def test_tide_phase_filter(self, client):
        rv = client.get("/api/fishing-map?tide_phase=incoming")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data.get("tide_phase") == "incoming"

    def test_water_temp_filter_narrow_range(self, client):
        """A very narrow temp range should return fewer locations than no filter."""
        rv_all = client.get("/api/fishing-map")
        rv_narrow = client.get("/api/fishing-map?min_water_temp=55&max_water_temp=60")
        data_all = json.loads(rv_all.data)
        data_narrow = json.loads(rv_narrow.data)
        # The narrow range should have <= locations than unfiltered
        assert len(data_narrow["locations"]) <= len(data_all["locations"])

    def test_water_temp_filter_invalid_ignored(self, client):
        rv = client.get("/api/fishing-map?min_water_temp=abc")
        assert rv.status_code == 200  # invalid ignored, not a 400

    def test_community_catches_in_response(self, client):
        rv = client.get("/api/fishing-map")
        data = json.loads(rv.data)
        # Every location should have a community_catches field
        for loc in data["locations"]:
            assert "community_catches" in loc
            assert isinstance(loc["community_catches"], int)

    def test_coast_filter_east(self, client):
        rv = client.get("/api/fishing-map?coast=east")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        for loc in data["locations"]:
            assert loc["coast"] in ("east", "gulf")

    def test_species_filter(self, client):
        rv = client.get("/api/fishing-map?species=striped+bass")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["species_filter"] == "striped bass"

    def test_month_out_of_range_defaults_to_current(self, client):
        rv = client.get("/api/fishing-map?month=99")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        import datetime
        assert data["month"] == datetime.date.today().month

    def test_trending_species_present(self, client):
        rv = client.get("/api/fishing-map")
        data = json.loads(rv.data)
        assert "trending_species" in data
        assert isinstance(data["trending_species"], list)

    def test_species_names_list_present(self, client):
        rv = client.get("/api/fishing-map")
        data = json.loads(rv.data)
        assert "species_names" in data
        assert len(data["species_names"]) > 0

    def test_monthly_summary_has_12_entries(self, client):
        rv = client.get("/api/fishing-map")
        data = json.loads(rv.data)
        assert len(data["monthly_summary"]) == 12


class TestStructureSpotsAPI:
    """Existing structure spots endpoint still works."""

    def test_requires_bbox(self, client):
        rv = client.get("/api/structure-spots")
        assert rv.status_code == 400

    def test_valid_small_bbox(self, client):
        rv = client.get("/api/structure-spots?sw_lat=40.0&sw_lng=-74.2&ne_lat=40.5&ne_lng=-73.7")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "features" in data

    def test_oversized_bbox_returns_zoom_required(self, client):
        rv = client.get("/api/structure-spots?sw_lat=20&sw_lng=-100&ne_lat=45&ne_lng=-60")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data.get("zoom_required") is True


# ── New-field tests: title, image_url, caught_at ──────────────────────────────


class TestMapCatchTitleAndImage:
    """DAL-level tests for title and image_url fields."""

    def test_title_stored_and_retrieved(self, app):
        with app.app_context():
            uid = _make_user("title_user")
            cid = add_map_catch(uid, 40.0, -74.0, "Striped bass",
                                title="Monster striper at dawn")
            row = get_map_catch(cid)
            assert row is not None
            assert row["title"] == "Monster striper at dawn"

    def test_title_truncated_at_120_chars(self, app):
        with app.app_context():
            uid = _make_user("title_trunc")
            long_title = "X" * 200
            cid = add_map_catch(uid, 40.0, -74.0, "Bluefish", title=long_title)
            row = get_map_catch(cid)
            assert len(row["title"]) == 120

    def test_image_url_stored(self, app):
        with app.app_context():
            uid = _make_user("img_user")
            cid = add_map_catch(uid, 40.0, -74.0, "Flounder",
                                image_url="https://example.com/fish.jpg")
            row = get_map_catch(cid)
            assert row["image_url"] == "https://example.com/fish.jpg"

    def test_image_url_defaults_to_none(self, app):
        with app.app_context():
            uid = _make_user("no_img_user")
            cid = add_map_catch(uid, 40.0, -74.0, "Pompano")
            row = get_map_catch(cid)
            assert row["image_url"] is None

    def test_title_defaults_to_none(self, app):
        with app.app_context():
            uid = _make_user("no_title_user")
            cid = add_map_catch(uid, 40.0, -74.0, "Red drum")
            row = get_map_catch(cid)
            assert row["title"] is None

    def test_bbox_query_returns_title_and_image(self, app):
        with app.app_context():
            uid = _make_user("bbox_title")
            add_map_catch(uid, 40.0, -74.0, "Bluefish",
                          title="Nice bluefish", image_url="https://cdn.example.com/a.jpg",
                          is_public=True)
            catches = get_map_catches_in_bbox(39.0, -75.0, 41.0, -73.0)
            assert catches
            c = catches[0]
            assert c["title"] == "Nice bluefish"
            assert c["image_url"] == "https://cdn.example.com/a.jpg"

    def test_feed_returns_title_and_image(self, app):
        with app.app_context():
            uid = _make_user("feed_title")
            add_map_catch(uid, 40.0, -74.0, "Bluefish",
                          title="Bluefish blitz", image_url="https://img.example.com/b.jpg",
                          is_public=True)
            from storage.sqlite import get_recent_public_catches
            catches = get_recent_public_catches()
            assert catches
            c = catches[0]
            assert c["title"] == "Bluefish blitz"
            assert c["image_url"] == "https://img.example.com/b.jpg"


class TestMapCatchCaughtAt:
    """Custom caught_at timestamp tests."""

    def test_custom_caught_at_stored(self, app):
        with app.app_context():
            uid = _make_user("time_user")
            cid = add_map_catch(uid, 40.0, -74.0, "Striper",
                                caught_at="2024-06-15 07:30:00")
            row = get_map_catch(cid)
            assert "2024-06-15" in row["caught_at"]

    def test_default_caught_at_is_recent(self, app):
        import datetime
        with app.app_context():
            uid = _make_user("time_default")
            cid = add_map_catch(uid, 40.0, -74.0, "Flounder")
            row = get_map_catch(cid)
            ts = datetime.datetime.fromisoformat(row["caught_at"])
            diff = datetime.datetime.utcnow() - ts
            assert abs(diff.total_seconds()) < 10


class TestCatchCreateAPINewFields:
    """API-level tests for title / image_url / caught_at in POST /api/map/catches."""

    def test_create_with_title_returns_201(self, client, app):
        with app.app_context():
            _make_user()
        _login(client)
        rv = client.post(
            "/api/map/catches",
            data=json.dumps({
                "lat": 40.0, "lng": -74.0,
                "species": "Striped bass",
                "title": "Awesome striper session",
            }),
            content_type="application/json",
        )
        assert rv.status_code == 201
        data = json.loads(rv.data)
        assert "id" in data

    def test_title_and_image_persisted_via_api(self, client, app):
        with app.app_context():
            _make_user()
        _login(client)
        rv = client.post(
            "/api/map/catches",
            data=json.dumps({
                "lat": 40.0, "lng": -74.0,
                "species": "Bluefish",
                "title": "Bluefish blitz",
                "image_url": "https://photos.example.com/catch1.jpg",
            }),
            content_type="application/json",
        )
        assert rv.status_code == 201
        catch_id = json.loads(rv.data)["id"]

        with app.app_context():
            row = get_map_catch(catch_id)
        assert row["title"] == "Bluefish blitz"
        assert row["image_url"] == "https://photos.example.com/catch1.jpg"

    def test_http_image_url_rejected(self, client, app):
        """Non-https image URLs must be silently discarded for security."""
        with app.app_context():
            _make_user()
        _login(client)
        rv = client.post(
            "/api/map/catches",
            data=json.dumps({
                "lat": 40.0, "lng": -74.0,
                "species": "Flounder",
                "image_url": "http://insecure.example.com/img.jpg",
            }),
            content_type="application/json",
        )
        assert rv.status_code == 201
        catch_id = json.loads(rv.data)["id"]

        with app.app_context():
            row = get_map_catch(catch_id)
        # http:// URL must NOT be stored
        assert not row["image_url"]

    def test_custom_caught_at_via_api(self, client, app):
        with app.app_context():
            _make_user()
        _login(client)
        rv = client.post(
            "/api/map/catches",
            data=json.dumps({
                "lat": 40.0, "lng": -74.0,
                "species": "Pompano",
                "caught_at": "2024-03-10 06:45:00",
            }),
            content_type="application/json",
        )
        assert rv.status_code == 201
        catch_id = json.loads(rv.data)["id"]

        with app.app_context():
            row = get_map_catch(catch_id)
        assert "2024-03-10" in row["caught_at"]

    def test_bbox_api_returns_new_fields(self, client, app):
        with app.app_context():
            uid = _make_user()
            add_map_catch(uid, 40.0, -74.0, "Snook",
                          title="Big snook", image_url="https://example.com/snook.jpg",
                          is_public=True)

        rv = client.get(
            "/api/map/catches?sw_lat=39&sw_lng=-75&ne_lat=41&ne_lng=-73",
        )
        assert rv.status_code == 200
        catches = json.loads(rv.data)["catches"]
        assert catches
        assert catches[0]["title"] == "Big snook"
        assert catches[0]["image_url"] == "https://example.com/snook.jpg"

    def test_feed_api_returns_new_fields(self, client, app):
        with app.app_context():
            uid = _make_user()
            add_map_catch(uid, 40.0, -74.0, "Tarpon",
                          title="Silver king", image_url="https://cdn.example.com/t.jpg",
                          is_public=True)

        rv = client.get("/api/map/feed?lat=40&lng=-74")
        assert rv.status_code == 200
        catches = json.loads(rv.data)["catches"]
        assert catches
        assert catches[0]["title"] == "Silver king"
        assert catches[0]["image_url"] == "https://cdn.example.com/t.jpg"
