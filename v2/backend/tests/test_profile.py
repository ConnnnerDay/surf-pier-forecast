from fastapi.testclient import TestClient


def _signup_and_get_headers(client: TestClient, email: str) -> dict[str, str]:
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": "GoodPass1", "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_profile_requires_auth(client: TestClient) -> None:
    resp = client.get("/profile")
    assert resp.status_code == 401


def test_get_profile_defaults(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.get("/profile", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["experience_level"] == "beginner"
    assert body["units"] == "imperial"
    assert body["theme"] == "system"
    assert body["onboarding_completed"] is False
    assert body["fishing_styles"] == []
    assert body["target_species"] == []


def test_update_profile_partial(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)

    resp = client.patch(
        "/profile",
        json={"max_wind_mph": 20, "target_species": ["Redfish", "Flounder"]},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_wind_mph"] == 20
    assert body["target_species"] == ["Redfish", "Flounder"]
    # untouched fields keep their existing values
    assert body["experience_level"] == "beginner"
    assert body["units"] == "imperial"

    # second partial update doesn't clobber the first
    resp2 = client.patch("/profile", json={"experience_level": "advanced"}, headers=headers)
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["experience_level"] == "advanced"
    assert body2["max_wind_mph"] == 20
    assert body2["target_species"] == ["Redfish", "Flounder"]


def test_update_profile_rejects_invalid_experience_level(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.patch("/profile", json={"experience_level": "wizard"}, headers=headers)
    assert resp.status_code == 422


def test_update_profile_rejects_invalid_units(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.patch("/profile", json={"units": "furlongs"}, headers=headers)
    assert resp.status_code == 422


def test_update_profile_rejects_negative_thresholds(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.patch("/profile", json={"max_wind_mph": -5}, headers=headers)
    assert resp.status_code == 422
