"""Tests for the Web Push service and push API endpoints."""

import sys
import types

import pytest

import services.push as push
from storage.sqlite import create_user, get_push_subscriptions


def _login_session(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["session_version"] = 0


class TestPushService:
    def test_unconfigured_is_noop(self, monkeypatch):
        monkeypatch.setattr(push, "_VAPID_PRIVATE_KEY", "")
        monkeypatch.setattr(push, "_VAPID_PUBLIC_KEY", "")
        monkeypatch.setattr(push, "_VAPID_SUBJECT", "")
        assert push.is_push_configured() is False
        sub = {"endpoint": "https://p/ep", "p256dh": "x", "auth": "y"}
        assert push.send_push(sub, "t", "b", "/u") is False

    def _configure(self, monkeypatch):
        monkeypatch.setattr(push, "_VAPID_PRIVATE_KEY", "priv")
        monkeypatch.setattr(push, "_VAPID_PUBLIC_KEY", "pub")
        monkeypatch.setattr(push, "_VAPID_SUBJECT", "mailto:a@b.com")

    def _install_fake_pywebpush(self, monkeypatch, raises=None):
        mod = types.ModuleType("pywebpush")

        class WebPushException(Exception):
            def __init__(self, message, response=None):
                super().__init__(message)
                self.response = response

        def webpush(**kwargs):
            if raises is not None:
                raise raises(WebPushException)

        mod.WebPushException = WebPushException
        mod.webpush = webpush
        monkeypatch.setitem(sys.modules, "pywebpush", mod)
        return WebPushException

    def test_configured_success(self, monkeypatch):
        self._configure(monkeypatch)
        self._install_fake_pywebpush(monkeypatch)
        sub = {"endpoint": "https://p/ep", "p256dh": "x", "auth": "y"}
        assert push.send_push(sub, "t", "b", "/u") is True

    def test_410_prunes_subscription(self, monkeypatch):
        self._configure(monkeypatch)

        def raises(exc_cls):
            resp = types.SimpleNamespace(status_code=410)
            raise exc_cls("gone", response=resp)

        self._install_fake_pywebpush(monkeypatch, raises=raises)
        pruned = []
        monkeypatch.setattr(push, "delete_push_subscription", lambda ep: pruned.append(ep))
        sub = {"endpoint": "https://p/dead", "p256dh": "x", "auth": "y"}
        assert push.send_push(sub, "t", "b", "/u") is False
        assert pruned == ["https://p/dead"]

    def test_other_error_does_not_prune(self, monkeypatch):
        self._configure(monkeypatch)

        def raises(exc_cls):
            resp = types.SimpleNamespace(status_code=500)
            raise exc_cls("boom", response=resp)

        self._install_fake_pywebpush(monkeypatch, raises=raises)
        pruned = []
        monkeypatch.setattr(push, "delete_push_subscription", lambda ep: pruned.append(ep))
        sub = {"endpoint": "https://p/ep", "p256dh": "x", "auth": "y"}
        assert push.send_push(sub, "t", "b", "/u") is False
        assert pruned == []


class TestPushEndpoints:
    def test_public_key_endpoint(self, client):
        resp = client.get("/api/v1/push/public-key")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "publicKey" in body["data"]
        assert "configured" in body["data"]

    def test_subscribe_requires_login(self, client):
        resp = client.post("/api/v1/push/subscribe", json={"endpoint": "x"})
        assert resp.status_code == 401

    def test_subscribe_and_unsubscribe(self, client):
        uid = create_user("pushuser", "pass1234")
        _login_session(client, uid)
        sub = {
            "endpoint": "https://push.example/ep1",
            "keys": {"p256dh": "key123", "auth": "auth123"},
        }
        resp = client.post("/api/v1/push/subscribe", json={"subscription": sub})
        assert resp.status_code == 200
        assert get_push_subscriptions(uid)[0]["endpoint"] == "https://push.example/ep1"

        resp = client.post(
            "/api/v1/push/unsubscribe", json={"endpoint": "https://push.example/ep1"}
        )
        assert resp.status_code == 200
        assert get_push_subscriptions(uid) == []

    def test_subscribe_rejects_malformed(self, client):
        uid = create_user("pushbad", "pass1234")
        _login_session(client, uid)
        resp = client.post("/api/v1/push/subscribe", json={"endpoint": "https://x"})
        assert resp.status_code == 400
