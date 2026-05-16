"""Tests for custom habitat admin API and map_habitats_v1 merge.

Covers:
- Auth guards on admin routes (non-admin → 403, unauthenticated → 403)
- Create / GET / DELETE lifecycle for custom habitats
- Habitat override create / list / delete
- map_habitats_v1 merges custom habitats into bbox results
- Invalid habitat_type → 400 on admin create route
- geo_habitats bbox validation (400 on bad params)
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

from app import create_app
from storage.sqlite import create_user, get_db, init_db
import storage.sqlite as _sqlite


# ─── Fixtures ────────────────────────────────────────────────────────────────

_CSRF = "test-csrf-token"


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    init_db()
    _app = create_app()
    _app.config["TESTING"] = True
    return _app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_admin(user_id: int) -> None:
    """Promote a user to admin directly in the DB."""
    conn = get_db()
    try:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    _sqlite._USER_CACHE.clear()


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["session_version"] = 0
        sess["csrf_token"] = _CSRF


def _headers() -> dict:
    return {"Content-Type": "application/json", "X-CSRFToken": _CSRF}


def _valid_polygon_geometry() -> dict:
    """A small valid GeoJSON Polygon near Wrightsville Beach, NC."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [-77.80, 34.22],
                [-77.79, 34.22],
                [-77.79, 34.23],
                [-77.80, 34.23],
                [-77.80, 34.22],
            ]
        ],
    }


def _valid_point_geometry() -> dict:
    return {"type": "Point", "coordinates": [-77.795, 34.225]}


# ─── Auth guard tests ─────────────────────────────────────────────────────────


class TestAdminHabitatAuthGuards:
    def test_anon_get_habitats_returns_403(self, client):
        resp = client.get("/api/v1/admin/habitats")
        assert resp.status_code == 403

    def test_anon_post_habitat_returns_403(self, client):
        resp = client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({"habitat_type": "reef", "geometry": _valid_polygon_geometry()}),
            headers=_headers(),
        )
        assert resp.status_code == 403

    def test_anon_delete_habitat_returns_403(self, client):
        resp = client.delete("/api/v1/admin/habitats/some-id", headers=_headers())
        assert resp.status_code == 403

    def test_non_admin_get_habitats_returns_403(self, client):
        uid = create_user("regular1", "pw123456")
        _login(client, uid)
        resp = client.get("/api/v1/admin/habitats")
        assert resp.status_code == 403

    def test_non_admin_post_habitat_returns_403(self, client):
        uid = create_user("regular2", "pw123456")
        _login(client, uid)
        resp = client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({"habitat_type": "reef", "geometry": _valid_polygon_geometry()}),
            headers=_headers(),
        )
        assert resp.status_code == 403

    def test_non_admin_delete_habitat_returns_403(self, client):
        uid = create_user("regular3", "pw123456")
        _login(client, uid)
        resp = client.delete("/api/v1/admin/habitats/some-id", headers=_headers())
        assert resp.status_code == 403

    def test_anon_override_post_returns_403(self, client):
        resp = client.post(
            "/api/v1/admin/habitat-overrides",
            data=json.dumps({"feature_key": "abc123"}),
            headers=_headers(),
        )
        assert resp.status_code == 403

    def test_non_admin_override_post_returns_403(self, client):
        uid = create_user("regular4", "pw123456")
        _login(client, uid)
        resp = client.post(
            "/api/v1/admin/habitat-overrides",
            data=json.dumps({"feature_key": "abc123"}),
            headers=_headers(),
        )
        assert resp.status_code == 403


# ─── Custom habitat CRUD ──────────────────────────────────────────────────────


class TestCustomHabitatCRUD:
    def test_admin_can_create_polygon_habitat(self, client):
        uid = create_user("conner1", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        payload = {
            "habitat_type": "reef",
            "name": "North Reef",
            "description": "Rocky reef structure",
            "fill_color": "#f59e0b",
            "geometry": _valid_polygon_geometry(),
        }
        resp = client.post(
            "/api/v1/admin/habitats",
            data=json.dumps(payload),
            headers=_headers(),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["custom"] is True
        assert data["habitat_type"] == "reef"
        assert data["name"] == "North Reef"
        assert data["fill_color"] == "#f59e0b"
        assert data["geometry"]["type"] == "Polygon"
        assert "id" in data

    def test_admin_can_create_point_habitat(self, client):
        uid = create_user("conner2", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        payload = {
            "habitat_type": "grassflat",
            "name": "Seagrass Meadow",
            "geometry": _valid_point_geometry(),
        }
        resp = client.post(
            "/api/v1/admin/habitats",
            data=json.dumps(payload),
            headers=_headers(),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["habitat_type"] == "grassflat"
        # Centroid should match the point coordinates
        assert abs(data["lat"] - 34.225) < 0.001
        assert abs(data["lng"] - (-77.795)) < 0.001

    def test_admin_can_list_habitats(self, client):
        uid = create_user("conner3", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        # Create two habitats
        for name in ["Habitat A", "Habitat B"]:
            client.post(
                "/api/v1/admin/habitats",
                data=json.dumps({"habitat_type": "reef", "name": name, "geometry": _valid_polygon_geometry()}),
                headers=_headers(),
            )

        resp = client.get("/api/v1/admin/habitats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2
        names = {h["name"] for h in data["habitats"]}
        assert "Habitat A" in names
        assert "Habitat B" in names

    def test_create_with_explicit_id_creates_that_id(self, client):
        uid = create_user("conner4", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        explicit_id = str(uuid.uuid4())
        payload = {
            "id": explicit_id,
            "habitat_type": "mangrove",
            "name": "Test Mangrove",
            "geometry": _valid_polygon_geometry(),
        }
        resp = client.post(
            "/api/v1/admin/habitats",
            data=json.dumps(payload),
            headers=_headers(),
        )
        assert resp.status_code == 201
        assert resp.get_json()["id"] == explicit_id

    def test_post_with_existing_id_updates_habitat(self, client):
        uid = create_user("conner5", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        # Create
        explicit_id = str(uuid.uuid4())
        client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({
                "id": explicit_id, "habitat_type": "reef",
                "name": "Original", "geometry": _valid_polygon_geometry(),
            }),
            headers=_headers(),
        )

        # Update via POST with same id
        resp = client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({
                "id": explicit_id, "habitat_type": "mangrove",
                "name": "Updated", "geometry": _valid_polygon_geometry(),
            }),
            headers=_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Updated"
        assert data["habitat_type"] == "mangrove"

    def test_delete_removes_habitat_from_list(self, client):
        uid = create_user("conner6", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        explicit_id = str(uuid.uuid4())
        client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({
                "id": explicit_id, "habitat_type": "surf",
                "name": "Surf Zone", "geometry": _valid_polygon_geometry(),
            }),
            headers=_headers(),
        )

        # Confirm it exists
        resp = client.get("/api/v1/admin/habitats")
        assert any(h["id"] == explicit_id for h in resp.get_json()["habitats"])

        # Delete
        resp = client.delete(f"/api/v1/admin/habitats/{explicit_id}", headers=_headers())
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] == explicit_id

        # Should no longer appear in list
        _sqlite._CUSTOM_HABITATS_TS = 0.0  # bust cache
        resp = client.get("/api/v1/admin/habitats")
        assert not any(h["id"] == explicit_id for h in resp.get_json()["habitats"])

    def test_delete_nonexistent_returns_404(self, client):
        uid = create_user("conner7", "pw123456")
        _make_admin(uid)
        _login(client, uid)
        resp = client.delete("/api/v1/admin/habitats/no-such-id", headers=_headers())
        assert resp.status_code == 404

    def test_invalid_habitat_type_returns_400(self, client):
        uid = create_user("conner8", "pw123456")
        _make_admin(uid)
        _login(client, uid)
        resp = client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({
                "habitat_type": "INVALID_TYPE",
                "geometry": _valid_polygon_geometry(),
            }),
            headers=_headers(),
        )
        assert resp.status_code == 400
        assert "habitat_type" in resp.get_json()["error"].lower()

    def test_missing_geometry_returns_400(self, client):
        uid = create_user("conner9", "pw123456")
        _make_admin(uid)
        _login(client, uid)
        resp = client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({"habitat_type": "reef"}),
            headers=_headers(),
        )
        assert resp.status_code == 400

    def test_invalid_geometry_type_returns_400(self, client):
        uid = create_user("conner10", "pw123456")
        _make_admin(uid)
        _login(client, uid)
        resp = client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({
                "habitat_type": "reef",
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            }),
            headers=_headers(),
        )
        assert resp.status_code == 400


# ─── Habitat overrides CRUD ───────────────────────────────────────────────────


class TestHabitatOverrides:
    def test_admin_can_create_override(self, client):
        uid = create_user("conner_ov1", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        resp = client.post(
            "/api/v1/admin/habitat-overrides",
            data=json.dumps({
                "feature_key": "34.22,-77.80,reef",
                "name": "Admin Name",
                "description": "Custom tip",
                "fill_color": "#ff0000",
            }),
            headers=_headers(),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["feature_key"] == "34.22,-77.80,reef"
        assert data["name"] == "Admin Name"
        assert data["fill_color"] == "#ff0000"

    def test_override_upsert_updates_existing(self, client):
        uid = create_user("conner_ov2", "pw123456")
        _make_admin(uid)
        _login(client, uid)
        fkey = "34.22,-77.80,mangrove"

        client.post(
            "/api/v1/admin/habitat-overrides",
            data=json.dumps({"feature_key": fkey, "name": "First"}),
            headers=_headers(),
        )
        resp = client.post(
            "/api/v1/admin/habitat-overrides",
            data=json.dumps({"feature_key": fkey, "name": "Second"}),
            headers=_headers(),
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Second"

    def test_override_missing_feature_key_returns_400(self, client):
        uid = create_user("conner_ov3", "pw123456")
        _make_admin(uid)
        _login(client, uid)
        resp = client.post(
            "/api/v1/admin/habitat-overrides",
            data=json.dumps({"name": "No key"}),
            headers=_headers(),
        )
        assert resp.status_code == 400

    def test_admin_can_list_overrides(self, client):
        uid = create_user("conner_ov4", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        client.post(
            "/api/v1/admin/habitat-overrides",
            data=json.dumps({"feature_key": "key1", "name": "OV1"}),
            headers=_headers(),
        )
        client.post(
            "/api/v1/admin/habitat-overrides",
            data=json.dumps({"feature_key": "key2", "name": "OV2"}),
            headers=_headers(),
        )
        resp = client.get("/api/v1/admin/habitat-overrides")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2

    def test_admin_can_delete_override(self, client):
        uid = create_user("conner_ov5", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        create_resp = client.post(
            "/api/v1/admin/habitat-overrides",
            data=json.dumps({"feature_key": "key_del", "name": "ToDelete"}),
            headers=_headers(),
        )
        ov_id = create_resp.get_json()["id"]

        resp = client.delete(f"/api/v1/admin/habitat-overrides/{ov_id}", headers=_headers())
        assert resp.status_code == 200

        _sqlite._HABITAT_OVERRIDES_TS = 0.0  # bust cache
        list_resp = client.get("/api/v1/admin/habitat-overrides")
        overrides = list_resp.get_json()["overrides"]
        assert not any(o["id"] == ov_id for o in overrides)


# ─── map_habitats_v1 merge ────────────────────────────────────────────────────


class TestMapHabitatsV1Merge:
    """GET /api/v1/map/habitats should include custom habitats in the bbox."""

    def _bbox_params(self):
        return "south=34.20&west=-77.85&north=34.25&east=-77.75"

    def test_custom_habitat_appears_in_merged_response(self, client):
        uid = create_user("conner_merge1", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        # Create a custom habitat inside the bbox
        explicit_id = str(uuid.uuid4())
        client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({
                "id": explicit_id,
                "habitat_type": "reef",
                "name": "Merged Reef",
                "geometry": _valid_polygon_geometry(),  # centroid within bbox
            }),
            headers=_headers(),
        )
        _sqlite._CUSTOM_HABITATS_TS = 0.0  # bust cache

        # Patch fetch_ai_habitats to return empty so only custom features appear
        with patch("web.api.fetch_ai_habitats", return_value=[]):
            resp = client.get(f"/api/v1/map/habitats?{self._bbox_params()}")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        features = data["data"]["features"]
        custom = [f for f in features if f.get("custom")]
        assert len(custom) == 1
        assert custom[0]["id"] == explicit_id
        assert custom[0]["name"] == "Merged Reef"
        assert custom[0]["habitat_type"] == "reef"

    def test_deleted_habitat_not_in_merged_response(self, client):
        uid = create_user("conner_merge2", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        explicit_id = str(uuid.uuid4())
        client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({
                "id": explicit_id, "habitat_type": "surf",
                "name": "Temp Surf", "geometry": _valid_polygon_geometry(),
            }),
            headers=_headers(),
        )
        client.delete(f"/api/v1/admin/habitats/{explicit_id}", headers=_headers())
        _sqlite._CUSTOM_HABITATS_TS = 0.0

        with patch("web.api.fetch_ai_habitats", return_value=[]):
            resp = client.get(f"/api/v1/map/habitats?{self._bbox_params()}")

        features = resp.get_json()["data"]["features"]
        assert not any(f.get("id") == explicit_id for f in features)

    def test_habitat_outside_bbox_not_merged(self, client):
        uid = create_user("conner_merge3", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        # Habitat far outside the test bbox (which is around 34.20-34.25N, 77.75-77.85W)
        far_geometry = {
            "type": "Point",
            "coordinates": [-80.0, 25.0],  # Miami area
        }
        explicit_id = str(uuid.uuid4())
        client.post(
            "/api/v1/admin/habitats",
            data=json.dumps({
                "id": explicit_id, "habitat_type": "reef",
                "name": "Far Away", "geometry": far_geometry,
            }),
            headers=_headers(),
        )
        _sqlite._CUSTOM_HABITATS_TS = 0.0

        with patch("web.api.fetch_ai_habitats", return_value=[]):
            resp = client.get(f"/api/v1/map/habitats?{self._bbox_params()}")

        features = resp.get_json()["data"]["features"]
        assert not any(f.get("id") == explicit_id for f in features)

    def test_override_applied_to_ai_feature(self, client):
        uid = create_user("conner_merge4", "pw123456")
        _make_admin(uid)
        _login(client, uid)

        ai_feature = {
            "id": "osm:way:12345",
            "lat": 34.225, "lng": -77.795,
            "name": "AI Name", "osm_type": "reef", "score": 5,
        }
        client.post(
            "/api/v1/admin/habitat-overrides",
            data=json.dumps({
                "feature_key": "osm:way:12345",
                "name": "Admin Override Name",
                "description": "Better tip",
            }),
            headers=_headers(),
        )
        _sqlite._HABITAT_OVERRIDES_TS = 0.0

        with patch("web.api.fetch_ai_habitats", return_value=[ai_feature]):
            resp = client.get(f"/api/v1/map/habitats?{self._bbox_params()}")

        features = resp.get_json()["data"]["features"]
        matched = next((f for f in features if f.get("id") == "osm:way:12345"), None)
        assert matched is not None
        assert matched.get("override_name") == "Admin Override Name"
        assert matched.get("override_description") == "Better tip"

    def test_map_habitats_requires_bbox(self, client):
        resp = client.get("/api/v1/map/habitats")
        assert resp.status_code == 400

    def test_map_habitats_rejects_oversized_bbox(self, client):
        resp = client.get("/api/v1/map/habitats?south=20&west=-100&north=45&east=-60")
        assert resp.status_code == 400


# ─── geo_habitats route (public GET) bbox validation ─────────────────────────


class TestGeoHabitatsBboxValidation:
    def test_missing_bbox_returns_400(self, client):
        resp = client.get("/api/v1/geo/habitats")
        assert resp.status_code == 400

    def test_invalid_habitat_type_returns_400(self, client):
        resp = client.get(
            "/api/v1/geo/habitats?south=34&west=-78&north=35&east=-77&habitat_type=NOPE"
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert not data["ok"]

    def test_valid_habitat_type_accepted(self, client):
        with patch("web.geo_api.fetch_ai_habitats", return_value=[]):
            for htype in ("surf", "reef", "mangrove", "grassflat", "estuary",
                          "kelp", "bottom", "general", "pelagic", "tidalflat"):
                resp = client.get(
                    f"/api/v1/geo/habitats?south=34&west=-78&north=35&east=-77&habitat_type={htype}"
                )
                assert resp.status_code == 200, f"Expected 200 for habitat_type={htype}"

    def test_oversized_bbox_returns_400(self, client):
        resp = client.get(
            "/api/v1/geo/habitats?south=20&west=-100&north=45&east=-60"
        )
        assert resp.status_code == 400


# ─── Routes existence ─────────────────────────────────────────────────────────


class TestHabitatRoutesExist:
    def test_admin_habitat_routes_registered(self, app):
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/v1/admin/habitats" in rules
        assert "/api/v1/admin/habitats/<string:feature_id>" in rules
        assert "/api/v1/admin/habitat-overrides" in rules
        assert "/api/v1/admin/habitat-overrides/<int:override_id>" in rules
        assert "/api/v1/map/habitats" in rules
        assert "/api/v1/geo/habitats" in rules
