"""Integration tests for versioned API routes and stable response envelopes."""

import pytest

from app import create_app
from storage.sqlite import create_user, init_db


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


def _login_session(client, user_id, location_id="wrightsville-beach-nc"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["location_id"] = location_id


def test_v1_forecast_envelope(client, monkeypatch):
    sample = {"generated_at": "2026-03-03T10:00:00", "conditions": {"verdict": "Good"}}

    monkeypatch.setattr("web.api.load_cached_forecast", lambda loc_id: sample)

    resp = client.get("/api/v1/forecast?location_id=wrightsville-beach-nc")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body.keys()) == {"ok", "data", "error", "meta"}
    assert body["ok"] is True
    assert body["error"] is None
    assert body["meta"]["version"] == "v1"
    assert body["data"]["location_id"] == "wrightsville-beach-nc"
    assert body["data"]["forecast"]["conditions"]["verdict"] == "Good"


def test_legacy_forecast_force_refresh(client, monkeypatch):
    generated = {"generated_at": "2026-03-03T11:00:00", "conditions": {"verdict": "Excellent"}}
    monkeypatch.setattr("web.api.generate_forecast", lambda location: generated)

    saved = {}

    def _save(data, location_id):
        saved["location_id"] = location_id
        saved["data"] = data

    monkeypatch.setattr("web.api.save_forecast", _save)

    resp = client.get("/api/forecast?location_id=wrightsville-beach-nc&force_refresh=true")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["conditions"]["verdict"] == "Excellent"
    assert saved["location_id"] == "wrightsville-beach-nc"


def test_v1_profile_requires_login(client):
    resp = client.get("/api/v1/profile")
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"


def test_v1_profile_get_and_post(client):
    uid = create_user("apiv1_user", "pass1234")
    assert uid is not None
    _login_session(client, uid)

    post = client.post("/api/v1/profile", json={"theme": "dark", "units": "F", "favorites": ["wrightsville-beach-nc"]})
    assert post.status_code == 200
    pbody = post.get_json()
    assert pbody["ok"] is True
    assert pbody["data"]["profile"]["theme"] == "dark"

    get = client.get("/api/v1/profile")
    assert get.status_code == 200
    gbody = get.get_json()
    assert gbody["ok"] is True
    assert set(gbody.keys()) == {"ok", "data", "error", "meta"}


def test_v1_log_crud(client):
    uid = create_user("apiv1_log", "pass1234")
    assert uid is not None
    _login_session(client, uid)

    create = client.post("/api/v1/log", json={"species": "Red Drum", "size": "22 in", "notes": "Slot fish"})
    assert create.status_code == 201
    cbody = create.get_json()
    assert cbody["ok"] is True
    entry_id = cbody["data"]["entry"]["id"]

    listing = client.get("/api/v1/log?location_id=wrightsville-beach-nc")
    assert listing.status_code == 200
    lbody = listing.get_json()
    assert lbody["ok"] is True
    assert isinstance(lbody["data"]["entries"], list)
    assert "stats" in lbody["data"]

    delete = client.delete(f"/api/v1/log/{entry_id}")
    assert delete.status_code == 200
    dbody = delete.get_json()
    assert dbody["ok"] is True
    assert dbody["data"]["deleted"] is True
