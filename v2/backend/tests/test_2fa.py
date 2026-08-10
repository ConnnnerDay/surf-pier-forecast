import pyotp
from fastapi.testclient import TestClient


def _signup(client: TestClient, email: str) -> dict[str, str]:
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": "GoodPass1", "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_enroll_requires_auth(client: TestClient) -> None:
    resp = client.post("/auth/2fa/enroll")
    assert resp.status_code == 401


def test_totp_not_required_until_confirmed(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup(client, allowlisted_email)
    resp = client.post("/auth/2fa/enroll", headers=headers)
    assert resp.status_code == 200
    assert "secret" in resp.json()
    assert resp.json()["provisioning_uri"].startswith("otpauth://totp/")

    # not confirmed yet — plain login (no code) still works
    login_resp = client.post(
        "/auth/login", json={"email": allowlisted_email, "password": "GoodPass1"}
    )
    assert login_resp.status_code == 200


def test_confirm_rejects_wrong_code(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup(client, allowlisted_email)
    client.post("/auth/2fa/enroll", headers=headers)
    resp = client.post("/auth/2fa/confirm", json={"code": "000000"}, headers=headers)
    assert resp.status_code == 400


def test_full_2fa_enroll_confirm_login_disable_flow(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup(client, allowlisted_email)

    enroll_resp = client.post("/auth/2fa/enroll", headers=headers)
    secret = enroll_resp.json()["secret"]
    totp = pyotp.TOTP(secret)

    confirm_resp = client.post("/auth/2fa/confirm", json={"code": totp.now()}, headers=headers)
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["totp_enabled"] is True

    # login without a code now fails, requiring one
    no_code_resp = client.post(
        "/auth/login", json={"email": allowlisted_email, "password": "GoodPass1"}
    )
    assert no_code_resp.status_code == 401
    assert no_code_resp.json()["detail"] == "TOTP code required"

    # wrong code fails
    wrong_code_resp = client.post(
        "/auth/login",
        json={"email": allowlisted_email, "password": "GoodPass1", "totp_code": "000000"},
    )
    assert wrong_code_resp.status_code == 401

    # right code succeeds
    right_code_resp = client.post(
        "/auth/login",
        json={"email": allowlisted_email, "password": "GoodPass1", "totp_code": totp.now()},
    )
    assert right_code_resp.status_code == 200

    # disabling requires the correct password
    bad_disable = client.post("/auth/2fa/disable", json={"password": "WrongPass1"}, headers=headers)
    assert bad_disable.status_code == 401

    disable_resp = client.post("/auth/2fa/disable", json={"password": "GoodPass1"}, headers=headers)
    assert disable_resp.status_code == 200
    assert disable_resp.json()["totp_enabled"] is False

    # login without a code works again
    final_login = client.post(
        "/auth/login", json={"email": allowlisted_email, "password": "GoodPass1"}
    )
    assert final_login.status_code == 200
