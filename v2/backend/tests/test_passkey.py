import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse

from app.models.passkey import WebAuthnChallenge


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _fake_credential(challenge_b64url: str, cred_type: str, origin: str, rp_id: str) -> dict:
    """A syntactically real WebAuthn response shape — real clientDataJSON
    (no signature needed to construct that part), fake attestation/auth
    data since producing those for real requires an actual authenticator.
    The signature verification itself is mocked in these tests; this gets
    everything *around* it (challenge round-trip, JSON parsing) exercised
    for real."""
    client_data = json.dumps(
        {"type": cred_type, "challenge": challenge_b64url, "origin": origin, "crossOrigin": False}
    ).encode()
    cred_id = _b64url(f"cred-{rp_id}-{challenge_b64url[:8]}".encode())
    return {
        "id": cred_id,
        "rawId": cred_id,
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64url(client_data),
            "attestationObject": _b64url(b"fake-attestation"),
            "authenticatorData": _b64url(b"fake-authenticator-data"),
            "signature": _b64url(b"fake-signature"),
        },
    }


def _signup_and_get_headers(client: TestClient, email: str) -> dict[str, str]:
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": "GoodPass1", "date_of_birth": "2000-01-01"},
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_register_options_requires_auth(client: TestClient) -> None:
    resp = client.post("/auth/passkey/register/options")
    assert resp.status_code == 401


def test_register_options_returns_valid_shape(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.post("/auth/passkey/register/options", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["rp"]["id"] == "localhost"
    assert "challenge" in body
    assert body["user"]["name"] == allowlisted_email
    assert body["authenticatorSelection"]["residentKey"] == "required"


def test_login_options_no_auth_required(client: TestClient) -> None:
    resp = client.post("/auth/passkey/login/options")
    assert resp.status_code == 200
    body = resp.json()
    assert "challenge" in body
    # discoverable/usernameless — no allowCredentials restriction
    assert not body.get("allowCredentials")


class _FakeVerifiedRegistration:
    def __init__(self, credential_id: bytes) -> None:
        self.credential_id = credential_id
        self.credential_public_key = b"fake-public-key-bytes"
        self.sign_count = 0


class _FakeVerifiedAuthentication:
    def __init__(self, new_sign_count: int = 1) -> None:
        self.new_sign_count = new_sign_count


def test_full_register_then_list_then_delete_flow(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)

    options_resp = client.post("/auth/passkey/register/options", headers=headers)
    challenge = options_resp.json()["challenge"]
    origin = "http://localhost:5173"
    credential = _fake_credential(challenge, "webauthn.create", origin, "localhost")

    with patch(
        "app.api.routes.passkey.webauthn.verify_registration_response",
        return_value=_FakeVerifiedRegistration(base64url_to_bytes(credential["id"])),
    ) as mock_verify:
        resp = client.post(
            "/auth/passkey/register/verify",
            json={"credential": credential, "nickname": "MacBook Touch ID"},
            headers=headers,
        )

    assert resp.status_code == 201
    assert resp.json()["device_label"] == "MacBook Touch ID"
    mock_verify.assert_called_once()

    list_resp = client.get("/auth/passkey/list", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
    passkey_id = list_resp.json()[0]["id"]

    delete_resp = client.delete(f"/auth/passkey/{passkey_id}", headers=headers)
    assert delete_resp.status_code == 204

    list_resp2 = client.get("/auth/passkey/list", headers=headers)
    assert list_resp2.json() == []


def test_register_verify_rejects_reused_challenge(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    options_resp = client.post("/auth/passkey/register/options", headers=headers)
    challenge = options_resp.json()["challenge"]
    credential = _fake_credential(
        challenge, "webauthn.create", "http://localhost:5173", "localhost"
    )

    with patch(
        "app.api.routes.passkey.webauthn.verify_registration_response",
        return_value=_FakeVerifiedRegistration(base64url_to_bytes(credential["id"])),
    ):
        first = client.post(
            "/auth/passkey/register/verify", json={"credential": credential}, headers=headers
        )
        assert first.status_code == 201

        replay = client.post(
            "/auth/passkey/register/verify", json={"credential": credential}, headers=headers
        )
    assert replay.status_code == 400


def test_register_verify_rejects_invalid_response(
    client: TestClient, allowlisted_email: str
) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    options_resp = client.post("/auth/passkey/register/options", headers=headers)
    challenge = options_resp.json()["challenge"]
    credential = _fake_credential(
        challenge, "webauthn.create", "http://localhost:5173", "localhost"
    )

    with patch(
        "app.api.routes.passkey.webauthn.verify_registration_response",
        side_effect=InvalidRegistrationResponse("bad signature"),
    ):
        resp = client.post(
            "/auth/passkey/register/verify", json={"credential": credential}, headers=headers
        )
    assert resp.status_code == 400


def test_full_login_flow(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)

    # register a passkey first
    reg_options = client.post("/auth/passkey/register/options", headers=headers).json()
    reg_credential = _fake_credential(
        reg_options["challenge"], "webauthn.create", "http://localhost:5173", "localhost"
    )
    with patch(
        "app.api.routes.passkey.webauthn.verify_registration_response",
        return_value=_FakeVerifiedRegistration(base64url_to_bytes(reg_credential["id"])),
    ):
        client.post(
            "/auth/passkey/register/verify", json={"credential": reg_credential}, headers=headers
        )

    # now log in with it (unauthenticated flow)
    login_options = client.post("/auth/passkey/login/options").json()
    login_credential = dict(reg_credential)
    login_credential["response"] = {
        **reg_credential["response"],
        "clientDataJSON": _b64url(
            json.dumps(
                {
                    "type": "webauthn.get",
                    "challenge": login_options["challenge"],
                    "origin": "http://localhost:5173",
                    "crossOrigin": False,
                }
            ).encode()
        ),
    }

    with patch(
        "app.api.routes.passkey.webauthn.verify_authentication_response",
        return_value=_FakeVerifiedAuthentication(new_sign_count=1),
    ) as mock_verify:
        resp = client.post(
            "/auth/passkey/login/verify",
            json={"credential": login_credential, "device_label": "iPhone"},
        )

    assert resp.status_code == 200
    assert resp.json()["access_token"]
    mock_verify.assert_called_once()


def test_login_verify_rejects_unknown_credential(client: TestClient) -> None:
    login_options = client.post("/auth/passkey/login/options").json()
    credential = _fake_credential(
        login_options["challenge"], "webauthn.get", "http://localhost:5173", "localhost"
    )
    resp = client.post("/auth/passkey/login/verify", json={"credential": credential})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Unknown passkey"


def test_login_verify_rejects_invalid_signature(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    reg_options = client.post("/auth/passkey/register/options", headers=headers).json()
    reg_credential = _fake_credential(
        reg_options["challenge"], "webauthn.create", "http://localhost:5173", "localhost"
    )
    with patch(
        "app.api.routes.passkey.webauthn.verify_registration_response",
        return_value=_FakeVerifiedRegistration(base64url_to_bytes(reg_credential["id"])),
    ):
        client.post(
            "/auth/passkey/register/verify", json={"credential": reg_credential}, headers=headers
        )

    login_options = client.post("/auth/passkey/login/options").json()
    login_credential = dict(reg_credential)
    login_credential["response"] = {
        **reg_credential["response"],
        "clientDataJSON": _b64url(
            json.dumps(
                {
                    "type": "webauthn.get",
                    "challenge": login_options["challenge"],
                    "origin": "http://localhost:5173",
                    "crossOrigin": False,
                }
            ).encode()
        ),
    }

    with patch(
        "app.api.routes.passkey.webauthn.verify_authentication_response",
        side_effect=InvalidAuthenticationResponse("bad signature"),
    ):
        resp = client.post("/auth/passkey/login/verify", json={"credential": login_credential})
    assert resp.status_code == 401


def test_login_verify_rejects_expired_challenge(client: TestClient, db_session: Session) -> None:
    login_options = client.post("/auth/passkey/login/options").json()
    credential = _fake_credential(
        login_options["challenge"], "webauthn.get", "http://localhost:5173", "localhost"
    )

    row = db_session.query(WebAuthnChallenge).filter_by(challenge=login_options["challenge"]).one()
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    resp = client.post("/auth/passkey/login/verify", json={"credential": credential})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Passkey challenge expired"


def test_delete_passkey_requires_ownership(client: TestClient, allowlisted_email: str) -> None:
    headers = _signup_and_get_headers(client, allowlisted_email)
    resp = client.delete("/auth/passkey/does-not-exist", headers=headers)
    assert resp.status_code == 404
