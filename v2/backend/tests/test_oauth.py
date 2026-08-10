from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient


def _configured_settings(**overrides):
    base = {
        "google_client_id": "test-google-client-id",
        "google_client_secret": "test-google-secret",
        "google_redirect_uri": "https://app.example.com/oauth/google/callback",
        "apple_client_id": "test-apple-client-id",
        "apple_redirect_uri": "https://app.example.com/oauth/apple/callback",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_google_login_url_not_configured(client: TestClient) -> None:
    resp = client.get("/oauth/google/login")
    assert resp.status_code == 501


def test_apple_login_url_not_configured(client: TestClient) -> None:
    resp = client.get("/oauth/apple/login")
    assert resp.status_code == 501


def test_google_login_url_when_configured(client: TestClient) -> None:
    with patch("app.api.routes.oauth.get_settings", return_value=_configured_settings()):
        resp = client.get("/oauth/google/login")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorize_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "state" in body


def test_google_callback_not_configured(client: TestClient) -> None:
    resp = client.post("/oauth/google/callback", json={"code": "abc"})
    assert resp.status_code == 501


def test_apple_callback_not_configured(client: TestClient) -> None:
    resp = client.post("/oauth/apple/callback", json={"id_token": "abc"})
    assert resp.status_code == 501


def test_google_first_time_signin_requires_signup_info(
    client: TestClient, allowlisted_email: str
) -> None:
    fake_claims = {
        "sub": "google-sub-1",
        "email": allowlisted_email,
        "iss": "https://accounts.google.com",
    }
    fake_token_resp = SimpleNamespace(status_code=200, json=lambda: {"id_token": "fake.jwt.token"})

    with (
        patch("app.api.routes.oauth.get_settings", return_value=_configured_settings()),
        patch("app.api.routes.oauth.httpx.post", return_value=fake_token_resp),
        patch("app.api.routes.oauth._verify_id_token", return_value=fake_claims),
    ):
        resp = client.post("/oauth/google/callback", json={"code": "abc"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "needs_signup_info"
    assert body["pending_token"]
    assert body["tokens"] is None


def test_google_signup_then_login_full_flow(client: TestClient, allowlisted_email: str) -> None:
    fake_claims = {
        "sub": "google-sub-2",
        "email": allowlisted_email,
        "iss": "https://accounts.google.com",
    }
    fake_token_resp = SimpleNamespace(status_code=200, json=lambda: {"id_token": "fake.jwt.token"})

    with (
        patch("app.api.routes.oauth.get_settings", return_value=_configured_settings()),
        patch("app.api.routes.oauth.httpx.post", return_value=fake_token_resp),
        patch("app.api.routes.oauth._verify_id_token", return_value=fake_claims),
    ):
        first = client.post("/oauth/google/callback", json={"code": "abc"})
        pending_token = first.json()["pending_token"]

        complete = client.post(
            "/oauth/complete-signup",
            json={"pending_token": pending_token, "date_of_birth": "2000-01-01"},
        )
        assert complete.status_code == 200
        assert complete.json()["access_token"]

        second = client.post("/oauth/google/callback", json={"code": "def"})

    assert second.status_code == 200
    assert second.json()["status"] == "logged_in"
    assert second.json()["tokens"]["access_token"]


def test_complete_signup_enforces_age_gate(client: TestClient, allowlisted_email: str) -> None:
    fake_claims = {
        "sub": "google-sub-3",
        "email": allowlisted_email,
        "iss": "https://accounts.google.com",
    }
    fake_token_resp = SimpleNamespace(status_code=200, json=lambda: {"id_token": "fake.jwt.token"})

    with (
        patch("app.api.routes.oauth.get_settings", return_value=_configured_settings()),
        patch("app.api.routes.oauth.httpx.post", return_value=fake_token_resp),
        patch("app.api.routes.oauth._verify_id_token", return_value=fake_claims),
    ):
        first = client.post("/oauth/google/callback", json={"code": "abc"})
        pending_token = first.json()["pending_token"]

    resp = client.post(
        "/oauth/complete-signup",
        json={"pending_token": pending_token, "date_of_birth": "2020-01-01"},
    )
    assert resp.status_code == 403


def test_complete_signup_enforces_allowlist(client: TestClient) -> None:
    fake_claims = {
        "sub": "google-sub-4",
        "email": "not-on-list@example.com",
        "iss": "https://accounts.google.com",
    }
    fake_token_resp = SimpleNamespace(status_code=200, json=lambda: {"id_token": "fake.jwt.token"})

    with (
        patch("app.api.routes.oauth.get_settings", return_value=_configured_settings()),
        patch("app.api.routes.oauth.httpx.post", return_value=fake_token_resp),
        patch("app.api.routes.oauth._verify_id_token", return_value=fake_claims),
    ):
        first = client.post("/oauth/google/callback", json={"code": "abc"})
        pending_token = first.json()["pending_token"]

    resp = client.post(
        "/oauth/complete-signup",
        json={"pending_token": pending_token, "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 403


def test_google_signin_links_to_existing_password_account(
    client: TestClient, allowlisted_email: str
) -> None:
    signup_resp = client.post(
        "/auth/signup",
        json={"email": allowlisted_email, "password": "GoodPass1", "date_of_birth": "2000-01-01"},
    )
    assert signup_resp.status_code == 201

    fake_claims = {
        "sub": "google-sub-5",
        "email": allowlisted_email,
        "iss": "https://accounts.google.com",
    }
    fake_token_resp = SimpleNamespace(status_code=200, json=lambda: {"id_token": "fake.jwt.token"})

    with (
        patch("app.api.routes.oauth.get_settings", return_value=_configured_settings()),
        patch("app.api.routes.oauth.httpx.post", return_value=fake_token_resp),
        patch("app.api.routes.oauth._verify_id_token", return_value=fake_claims),
    ):
        resp = client.post("/oauth/google/callback", json={"code": "abc"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "logged_in"
    assert resp.json()["tokens"]["access_token"]


def test_apple_signin_flow(client: TestClient, allowlisted_email: str) -> None:
    fake_claims = {
        "sub": "apple-sub-1",
        "email": allowlisted_email,
        "iss": "https://appleid.apple.com",
    }

    with (
        patch("app.api.routes.oauth.get_settings", return_value=_configured_settings()),
        patch("app.api.routes.oauth._verify_id_token", return_value=fake_claims),
    ):
        first = client.post("/oauth/apple/callback", json={"id_token": "fake.jwt.token"})
        assert first.status_code == 200
        assert first.json()["status"] == "needs_signup_info"
        pending_token = first.json()["pending_token"]

        complete = client.post(
            "/oauth/complete-signup",
            json={"pending_token": pending_token, "date_of_birth": "2000-01-01"},
        )
        assert complete.status_code == 200

        second = client.post("/oauth/apple/callback", json={"id_token": "fake.jwt.token"})

    assert second.status_code == 200
    assert second.json()["status"] == "logged_in"


def test_complete_signup_rejects_garbage_token(client: TestClient) -> None:
    resp = client.post(
        "/oauth/complete-signup",
        json={"pending_token": "not-a-real-token", "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 401


def test_complete_signup_rejects_a_real_access_token(
    client: TestClient, allowlisted_email: str
) -> None:
    # an access token is a validly-signed JWT, just not an oauth_pending one —
    # make sure the type check actually gates this, not just signature validity
    signup_resp = client.post(
        "/auth/signup",
        json={"email": allowlisted_email, "password": "GoodPass1", "date_of_birth": "2000-01-01"},
    )
    access_token = signup_resp.json()["access_token"]

    resp = client.post(
        "/oauth/complete-signup",
        json={"pending_token": access_token, "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 401
