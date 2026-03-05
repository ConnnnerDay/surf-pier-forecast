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

    monkeypatch.setattr("web.api.load_cached_forecast", lambda loc_id, user_id=None: sample)

    resp = client.get("/api/v1/forecast?location_id=wrightsville-beach-nc")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body.keys()) == {"ok", "data", "error", "meta"}
    assert body["ok"] is True
    assert body["error"] is None
    assert body["meta"]["version"] == "v1"
    assert body["data"]["location_id"] == "wrightsville-beach-nc"
    assert body["data"]["forecast"]["conditions"]["verdict"] == "Good"




def test_v1_forecast_status_endpoint(client, monkeypatch):
    sample = {"generated_at": "2026-03-03T10:00:00"}
    monkeypatch.setattr("web.api.load_cached_forecast", lambda loc_id, user_id=None, include_stale=False: sample)
    monkeypatch.setattr("web.api._forecast_age_minutes", lambda forecast: 15)
    monkeypatch.setattr("web.api.is_refreshing", lambda loc_id, user_id=None: True)

    resp = client.get("/api/v1/forecast/wrightsville-beach-nc/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert data["location_id"] == "wrightsville-beach-nc"
    assert data["last_generated_at"] == "2026-03-03T10:00:00"
    assert data["is_stale"] is False
    assert data["is_refreshing"] is True

def test_legacy_forecast_force_refresh(client, monkeypatch):
    generated = {"generated_at": "2026-03-03T11:00:00", "conditions": {"verdict": "Excellent"}}
    monkeypatch.setattr("web.api.generate_forecast", lambda location: generated)

    saved = {}

    def _save(data, location_id, user_id=None):
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


# ---------------------------------------------------------------------------
# /api/v1/regulations
# ---------------------------------------------------------------------------

def test_v1_regulations_missing_species(client):
    """Omitting the required 'species' param returns 400."""
    resp = client.get("/api/v1/regulations")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "missing_param"


def test_v1_regulations_with_state_returns_envelope(client):
    """Valid species + state returns 200 v1 envelope with regulation dict."""
    resp = client.get("/api/v1/regulations?species=Red+drum+%28puppy+drum%29&state=NC")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert set(body.keys()) == {"ok", "data", "error", "meta"}
    assert body["error"] is None
    assert body["meta"]["version"] == "v1"
    data = body["data"]
    assert data["species"] == "Red drum (puppy drum)"
    assert data["state"] == "NC"
    reg = data["regulation"]
    assert reg is not None
    assert "min_size" in reg
    assert "bag_limit" in reg
    assert "season" in reg
    assert "notes" in reg
    assert "official_source" in reg
    assert "snapshot_source" in reg
    assert "source_file" in reg


def test_v1_regulations_species_lookup_is_case_insensitive(client):
    """Species lookup should work even when species capitalization differs."""
    resp = client.get("/api/v1/regulations?species=red+drum+%28puppy+drum%29&state=NC")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    reg = body["data"]["regulation"]
    assert reg is not None
    assert reg["data_status"] == "snapshot"
    assert reg["min_size"] == "18 in TL"


def test_v1_regulations_unknown_species_returns_null(client):
    """Species without snapshot rows still returns official-source guidance."""
    resp = client.get("/api/v1/regulations?species=Fantasy+Fish&state=NC")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    reg = body["data"]["regulation"]
    assert reg is not None
    assert reg["official_source"].startswith("https://")
    assert reg["data_status"] == "official_reference"
    assert reg["source_file"].endswith("regulations_data.json")


def test_v1_regulations_no_state_returns_null(client):
    """Without state info the regulation is null but the response is still 200."""
    resp = client.get("/api/v1/regulations?species=Red+drum+%28puppy+drum%29")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["state"] is None
    assert body["data"]["regulation"] is None



def test_v1_regulations_falls_back_to_session_location_state(client, monkeypatch):
    """If no state/location params are passed, state is derived from session location."""
    monkeypatch.setattr(
        "web.api.get_session_location",
        lambda: {"id": "session-loc", "state": "NC", "name": "Session Beach"},
    )
    resp = client.get("/api/v1/regulations?species=Red+drum+%28puppy+drum%29")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["state"] == "NC"
    assert body["data"]["regulation"] is not None

def test_v1_regulations_derives_state_from_location_id(client, monkeypatch):
    """Passing location_id causes state to be derived from the location config."""
    monkeypatch.setattr(
        "web.api.get_location",
        lambda loc_id: {"id": loc_id, "state": "NC", "name": "Test Beach"},
    )
    resp = client.get(
        "/api/v1/regulations?species=Red+drum+%28puppy+drum%29&location_id=test-nc"
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["state"] == "NC"
    assert body["data"]["regulation"] is not None




def test_v1_regulations_invalid_location_id_falls_back_to_session_location(client, monkeypatch):
    """If location_id is invalid, API should still try the active session location."""
    monkeypatch.setattr("web.api.get_location", lambda loc_id: None)
    monkeypatch.setattr(
        "web.api.get_session_location",
        lambda: {"id": "session-loc", "state": "NC", "name": "Session Beach"},
    )
    resp = client.get(
        "/api/v1/regulations?species=Red+drum+%28puppy+drum%29&location_id=missing-loc"
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["state"] == "NC"
    assert body["data"]["regulation"] is not None
def test_v1_regulations_state_overrides_location_id(client, monkeypatch):
    """Explicit 'state' query param takes priority over location_id lookup."""
    # location_id would give SC, but state=NC is provided explicitly
    monkeypatch.setattr(
        "web.api.get_location",
        lambda loc_id: {"id": loc_id, "state": "SC", "name": "SC Beach"},
    )
    resp = client.get(
        "/api/v1/regulations?species=Red+drum+%28puppy+drum%29&state=NC&location_id=sc-loc"
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"]["state"] == "NC"


def test_v1_regulations_unknown_state_returns_null(client):
    """Unknown states still return fallback official-source guidance."""
    resp = client.get("/api/v1/regulations?species=Red+drum+%28puppy+drum%29&state=ZZ")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    reg = body["data"]["regulation"]
    assert reg is not None
    assert reg["official_source"].startswith("https://")


def test_v1_forecast_outlook_cached_only(client, monkeypatch):
    sample = {
        "outlook": [{"day": "Mon", "date": "Apr 1", "verdict": "Good", "wind": "10 kt", "waves": "2 ft", "top_species": ["Red Drum"]}],
        "best_day": {"best_day": "Mon", "recommendation": "Fish dawn", "verdict": "Good"},
        "activity_timeline": [{"hour": 0, "label": "12 AM", "level": 35, "tag": "low"}],
    }
    monkeypatch.setattr("web.api.load_cached_forecast", lambda loc_id, user_id=None: sample)
    monkeypatch.setattr("web.api.generate_forecast", lambda location: (_ for _ in ()).throw(AssertionError("should not generate")))

    resp = client.get("/api/v1/forecast/wrightsville-beach-nc/outlook")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["location_id"] == "wrightsville-beach-nc"
    assert body["data"]["outlook"][0]["day"] == "Mon"
    assert body["data"]["activity_timeline"][0]["label"] == "12 AM"


def test_v1_forecast_solunar_cached_only(client, monkeypatch):
    sample = {"solunar": {"rating": "Great", "moon_phase": "Full Moon", "major_periods": [], "minor_periods": []}}
    monkeypatch.setattr("web.api.load_cached_forecast", lambda loc_id, user_id=None: sample)
    monkeypatch.setattr("web.api.generate_forecast", lambda location: (_ for _ in ()).throw(AssertionError("should not generate")))

    resp = client.get("/api/v1/forecast/wrightsville-beach-nc/solunar")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["solunar"]["rating"] == "Great"


def test_v1_forecast_section_endpoints_404_when_missing_cache(client, monkeypatch):
    monkeypatch.setattr("web.api.load_cached_forecast", lambda loc_id, user_id=None: None)

    outlook = client.get("/api/v1/forecast/wrightsville-beach-nc/outlook")
    assert outlook.status_code == 404
    assert outlook.get_json()["error"]["code"] == "forecast_not_cached"

    solunar = client.get("/api/v1/forecast/wrightsville-beach-nc/solunar")
    assert solunar.status_code == 404
    assert solunar.get_json()["error"]["code"] == "forecast_not_cached"
