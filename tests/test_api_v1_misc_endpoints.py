"""Tests for web/api.py push-subscription endpoints and the multi-fetch
aggregation endpoints (env-context, map stat-cards, combined-forecast) —
previously untested.
"""

from __future__ import annotations

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
        sess["session_version"] = 0
        sess["location_id"] = location_id


class TestPushPublicKey:
    def test_returns_public_key_and_configured_flag(self, client, monkeypatch):
        monkeypatch.setattr("services.push.get_public_key", lambda: "pub-key-123")
        monkeypatch.setattr("services.push.is_push_configured", lambda: True)
        resp = client.get("/api/v1/push/public-key")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["publicKey"] == "pub-key-123"
        assert body["data"]["configured"] is True


class TestPushSubscribe:
    def test_requires_login(self, client):
        resp = client.post("/api/v1/push/subscribe", json={})
        assert resp.status_code == 401

    def test_rejects_missing_endpoint(self, client):
        uid = create_user("push_user1", "pass1234")
        _login_session(client, uid)
        resp = client.post(
            "/api/v1/push/subscribe",
            json={"subscription": {"keys": {"p256dh": "a", "auth": "b"}}},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_subscription"

    def test_rejects_endpoint_too_long(self, client):
        uid = create_user("push_user2", "pass1234")
        _login_session(client, uid)
        resp = client.post(
            "/api/v1/push/subscribe",
            json={
                "subscription": {
                    "endpoint": "x" * 1025,
                    "keys": {"p256dh": "a", "auth": "b"},
                }
            },
        )
        assert resp.status_code == 400

    def test_rejects_missing_keys(self, client):
        uid = create_user("push_user3", "pass1234")
        _login_session(client, uid)
        resp = client.post(
            "/api/v1/push/subscribe",
            json={"subscription": {"endpoint": "https://push.example/ep1"}},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_subscription"

    def test_rejects_incomplete_keys(self, client):
        uid = create_user("push_user4", "pass1234")
        _login_session(client, uid)
        resp = client.post(
            "/api/v1/push/subscribe",
            json={
                "subscription": {
                    "endpoint": "https://push.example/ep2",
                    "keys": {"p256dh": "a"},
                }
            },
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_subscription"

    def test_stores_subscription_when_valid(self, client):
        from storage.sqlite import get_push_subscriptions

        uid = create_user("push_user5", "pass1234")
        _login_session(client, uid)
        resp = client.post(
            "/api/v1/push/subscribe",
            json={
                "subscription": {
                    "endpoint": "https://push.example/ep3",
                    "keys": {"p256dh": "key-p", "auth": "key-a"},
                }
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["ok"] is True
        subs = get_push_subscriptions(uid)
        assert any(s["endpoint"] == "https://push.example/ep3" for s in subs)

    def test_accepts_flat_subscription_payload(self, client):
        uid = create_user("push_user6", "pass1234")
        _login_session(client, uid)
        resp = client.post(
            "/api/v1/push/subscribe",
            json={
                "endpoint": "https://push.example/ep4",
                "keys": {"p256dh": "key-p", "auth": "key-a"},
            },
        )
        assert resp.status_code == 200


class TestPushUnsubscribe:
    def test_requires_login(self, client):
        resp = client.post("/api/v1/push/unsubscribe", json={"endpoint": "x"})
        assert resp.status_code == 401

    def test_rejects_missing_endpoint(self, client):
        uid = create_user("push_user7", "pass1234")
        _login_session(client, uid)
        resp = client.post("/api/v1/push/unsubscribe", json={})
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_param"

    def test_removes_subscription(self, client):
        from storage.sqlite import add_push_subscription, get_push_subscriptions

        uid = create_user("push_user8", "pass1234")
        add_push_subscription(uid, "https://push.example/ep5", "p", "a")
        _login_session(client, uid)
        resp = client.post(
            "/api/v1/push/unsubscribe", json={"endpoint": "https://push.example/ep5"}
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["ok"] is True
        subs = get_push_subscriptions(uid)
        assert not any(s["endpoint"] == "https://push.example/ep5" for s in subs)


class TestWeatherEnvContext:
    def test_requires_lat_lng(self, client):
        resp = client.get("/api/weather/env-context")
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_params"

    def test_returns_aqi_and_drought(self, client, monkeypatch):
        monkeypatch.setattr("web.api.fetch_air_quality", lambda lat, lng: {"aqi": 42})
        monkeypatch.setattr("web.api.fetch_drought", lambda lat, lng: {"level": "D1"})
        resp = client.get("/api/weather/env-context?lat=34.2&lng=-77.8")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["aqi"] == {"aqi": 42}
        assert body["drought"] == {"level": "D1"}

    def test_handles_fetch_exceptions_gracefully(self, client, monkeypatch):
        def _boom(lat, lng):
            raise RuntimeError("network down")

        monkeypatch.setattr("web.api.fetch_air_quality", _boom)
        monkeypatch.setattr("web.api.fetch_drought", _boom)
        resp = client.get("/api/weather/env-context?lat=34.2&lng=-77.8")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["aqi"] is None
        assert body["drought"] is None


class TestMapStatCards:
    def test_requires_lat_lng(self, client):
        resp = client.get("/api/map/stat-cards")
        assert resp.status_code == 400

    def test_aggregates_all_sources(self, client, monkeypatch):
        monkeypatch.setattr(
            "web.api.fetch_ndbc_buoys", lambda s, w, n, e: [{"id": "b1"}]
        )
        monkeypatch.setattr(
            "web.api.fetch_metar_stations", lambda s, w, n, e: [{"id": "m1"}]
        )
        monkeypatch.setattr(
            "web.api.fetch_wildfire_incidents", lambda s, w, n, e: []
        )
        monkeypatch.setattr(
            "web.api.fetch_stream_gauges", lambda s, w, n, e: [{"id": "g1"}]
        )
        monkeypatch.setattr("web.api.fetch_tropical_outlook", lambda: [])
        resp = client.get("/api/map/stat-cards?lat=34.2&lng=-77.8")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["buoys"]["count"] == 1
        assert body["metar"]["count"] == 1
        assert body["gauges"]["count"] == 1
        assert body["fires"]["count"] == 0
        assert body["tropical"]["count"] == 0

    def test_individual_source_failure_does_not_break_response(self, client, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("down")

        monkeypatch.setattr("web.api.fetch_ndbc_buoys", _boom)
        monkeypatch.setattr("web.api.fetch_metar_stations", lambda s, w, n, e: [])
        monkeypatch.setattr("web.api.fetch_wildfire_incidents", lambda s, w, n, e: [])
        monkeypatch.setattr("web.api.fetch_stream_gauges", lambda s, w, n, e: [])
        monkeypatch.setattr("web.api.fetch_tropical_outlook", lambda: [])
        resp = client.get("/api/map/stat-cards?lat=34.2&lng=-77.8")
        assert resp.status_code == 200
        assert resp.get_json()["buoys"]["count"] == 0


class TestWeatherCombinedForecast:
    def test_requires_lat_lng(self, client):
        resp = client.get("/api/weather/combined-forecast")
        assert resp.status_code == 400

    def test_returns_all_three_forecasts(self, client, monkeypatch):
        monkeypatch.setattr(
            "web.api.fetch_wind_forecast", lambda lat, lng: [{"speed": 10}]
        )
        monkeypatch.setattr(
            "web.api.fetch_precip_forecast", lambda lat, lng: [{"chance": 20}]
        )
        monkeypatch.setattr(
            "web.api.fetch_temp_forecast", lambda lat, lng: [{"high": 80}]
        )
        resp = client.get("/api/weather/combined-forecast?lat=34.2&lng=-77.8")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["wind"]["count"] == 1
        assert body["precip"]["count"] == 1
        assert body["temp"]["days"] == [{"high": 80}]

    def test_null_results_when_fetch_raises(self, client, monkeypatch):
        def _boom(lat, lng):
            raise RuntimeError("down")

        monkeypatch.setattr("web.api.fetch_wind_forecast", _boom)
        monkeypatch.setattr("web.api.fetch_precip_forecast", _boom)
        monkeypatch.setattr("web.api.fetch_temp_forecast", _boom)
        resp = client.get("/api/weather/combined-forecast?lat=34.2&lng=-77.8")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["wind"] is None
        assert body["precip"] is None
        assert body["temp"] is None
