from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _signup_and_get_headers(client: TestClient, email: str) -> dict[str, str]:
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": "GoodPass1", "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_export_requires_auth(client: TestClient) -> None:
    resp = client.get("/account/export")
    assert resp.status_code == 401


def test_export_returns_account_profile_and_locations(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    client.post(
        "/locations", json={"label": "Home Pier", "lat": 34.2, "lng": -77.8}, headers=headers
    )
    client.patch("/profile", json={"units": "metric"}, headers=headers)

    resp = client.get("/account/export", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == allowlisted_email
    assert body["has_password"] is True
    assert body["google_linked"] is False
    assert body["profile"]["units"] == "metric"
    assert len(body["locations"]) == 1
    assert body["locations"][0]["label"] == "Home Pier"
    # security material never leaves this endpoint
    assert "password_hash" not in body
    assert "totp_secret" not in body


def test_delete_requires_auth(client: TestClient) -> None:
    resp = client.request("DELETE", "/account", json={})
    assert resp.status_code == 401


def test_delete_requires_correct_password(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.request("DELETE", "/account", json={"password": "WrongPass1"}, headers=headers)
    assert resp.status_code == 401

    # account must still be usable
    resp = client.get("/auth/me", headers=headers)
    assert resp.status_code == 200


def test_delete_rejects_missing_password_when_one_is_set(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.request("DELETE", "/account", json={}, headers=headers)
    assert resp.status_code == 401


def test_delete_wipes_account_and_data(
    client: TestClient, allowlisted_email: str, db_session: Session
) -> None:
    from app.models.forecast_cache import ForecastCache
    from app.models.location import SavedLocation
    from app.models.profile import Profile
    from app.models.user import RefreshToken, User

    headers = _signup_and_get_headers(client, allowlisted_email)
    location = client.post(
        "/locations", json={"label": "Home Pier", "lat": 34.2, "lng": -77.8}, headers=headers
    ).json()
    db_session.add(
        ForecastCache(location_id=location["id"], forecast_json={"conditions": {"verdict": "go"}})
    )
    db_session.commit()

    resp = client.request("DELETE", "/account", json={"password": "GoodPass1"}, headers=headers)
    assert resp.status_code == 204

    assert db_session.query(User).filter_by(email=allowlisted_email).one_or_none() is None
    assert db_session.query(SavedLocation).filter_by(id=location["id"]).one_or_none() is None
    assert db_session.query(Profile).count() == 0
    assert (
        db_session.query(ForecastCache).filter_by(location_id=location["id"]).one_or_none() is None
    )
    assert db_session.query(RefreshToken).count() == 0

    # the access token that just deleted the account no longer works
    resp = client.get("/auth/me", headers=headers)
    assert resp.status_code == 401


def test_delete_without_password_succeeds_for_oauth_only_account(
    client: TestClient, db_session: Session
) -> None:
    from app.models.profile import Profile
    from app.models.user import User

    user = User(email="oauth-only@example.com", google_sub="google-sub-123")
    db_session.add(user)
    db_session.flush()
    db_session.add(Profile(user_id=user.id))
    db_session.commit()

    from app.core.security import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    resp = client.request("DELETE", "/account", json={}, headers=headers)
    assert resp.status_code == 204
    assert db_session.query(User).filter_by(id=user.id).one_or_none() is None
