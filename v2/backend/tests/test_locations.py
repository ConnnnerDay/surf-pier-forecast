from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.user import BetaAllowlistEntry


def _signup_and_get_headers(client: TestClient, email: str) -> dict[str, str]:
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": "GoodPass1", "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create(client: TestClient, headers: dict[str, str], label: str, **extra: object) -> dict:
    body = {"label": label, "lat": 34.2, "lng": -77.8, **extra}
    resp = client.post("/locations", json=body, headers=headers)
    assert resp.status_code == 201
    return resp.json()


def test_list_locations_requires_auth(client: TestClient) -> None:
    resp = client.get("/locations")
    assert resp.status_code == 401


def test_create_and_list_location(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    created = _create(client, headers, "Home Pier")

    resp = client.get("/locations", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == created["id"]
    assert body[0]["label"] == "Home Pier"
    assert body[0]["is_default"] is False


def test_create_location_caps_at_five(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    for i in range(5):
        _create(client, headers, f"Spot {i}")

    resp = client.post(
        "/locations", json={"label": "One too many", "lat": 1.0, "lng": 2.0}, headers=headers
    )
    assert resp.status_code == 400
    assert "at most 5" in resp.json()["detail"]


def test_update_label(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    created = _create(client, headers, "Old Name")

    resp = client.patch(f"/locations/{created['id']}", json={"label": "New Name"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["label"] == "New Name"


def test_setting_default_unsets_previous_default(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    first = _create(client, headers, "First")
    second = _create(client, headers, "Second")

    resp = client.patch(f"/locations/{first['id']}", json={"is_default": True}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True

    resp = client.patch(f"/locations/{second['id']}", json={"is_default": True}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True

    all_locations = client.get("/locations", headers=headers).json()
    listing = {loc["id"]: loc["is_default"] for loc in all_locations}
    assert listing[first["id"]] is False
    assert listing[second["id"]] is True


def test_update_missing_location_404s(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.patch("/locations/does-not-exist", json={"label": "x"}, headers=headers)
    assert resp.status_code == 404


def test_update_requires_auth(client: TestClient) -> None:
    resp = client.patch("/locations/some-id", json={"label": "x"})
    assert resp.status_code == 401


def test_cannot_update_another_users_location(client: TestClient, db_session: Session) -> None:
    db_session.add(BetaAllowlistEntry(email="owner@example.com"))
    db_session.add(BetaAllowlistEntry(email="intruder@example.com"))
    db_session.commit()

    owner_headers = _signup_and_get_headers(client, "owner@example.com")
    intruder_headers = _signup_and_get_headers(client, "intruder@example.com")
    location = _create(client, owner_headers, "Owner's Spot")

    resp = client.patch(
        f"/locations/{location['id']}", json={"label": "Hijacked"}, headers=intruder_headers
    )
    assert resp.status_code == 404


def test_delete_location(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    created = _create(client, headers, "Temp Spot")

    resp = client.delete(f"/locations/{created['id']}", headers=headers)
    assert resp.status_code == 204

    resp = client.get("/locations", headers=headers)
    assert resp.json() == []


def test_delete_missing_location_404s(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.delete("/locations/does-not-exist", headers=headers)
    assert resp.status_code == 404
