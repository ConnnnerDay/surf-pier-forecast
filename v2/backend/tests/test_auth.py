from fastapi.testclient import TestClient


def test_signup_requires_allowlist(client: TestClient) -> None:
    resp = client.post(
        "/auth/signup",
        json={
            "email": "not-on-list@example.com",
            "password": "GoodPass1",
            "date_of_birth": "2000-01-01",
        },
    )
    assert resp.status_code == 403


def test_signup_requires_min_age(client: TestClient, allowlisted_email: str) -> None:
    resp = client.post(
        "/auth/signup",
        json={
            "email": allowlisted_email,
            "password": "GoodPass1",
            "date_of_birth": "2020-01-01",
        },
    )
    assert resp.status_code == 403
    assert "13" in resp.json()["detail"]


def test_signup_login_and_refresh_flow(client: TestClient, allowlisted_email: str) -> None:
    signup_resp = client.post(
        "/auth/signup",
        json={
            "email": allowlisted_email,
            "password": "GoodPass1",
            "date_of_birth": "2000-01-01",
        },
    )
    assert signup_resp.status_code == 201
    tokens = signup_resp.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    login_resp = client.post(
        "/auth/login", json={"email": allowlisted_email, "password": "GoodPass1"}
    )
    assert login_resp.status_code == 200

    refresh_resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_resp.status_code == 200

    # a used refresh token cannot be replayed
    replay_resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay_resp.status_code == 401


def test_locations_require_auth(client: TestClient) -> None:
    resp = client.get("/locations")
    assert resp.status_code == 401


def test_saved_locations_cap(client: TestClient, allowlisted_email: str) -> None:
    signup_resp = client.post(
        "/auth/signup",
        json={
            "email": allowlisted_email,
            "password": "GoodPass1",
            "date_of_birth": "2000-01-01",
        },
    )
    access_token = signup_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    for i in range(5):
        resp = client.post(
            "/locations",
            json={"label": f"Spot {i}", "lat": 34.0 + i, "lng": -77.0},
            headers=headers,
        )
        assert resp.status_code == 201

    resp = client.post(
        "/locations",
        json={"label": "One too many", "lat": 40.0, "lng": -70.0},
        headers=headers,
    )
    assert resp.status_code == 400
