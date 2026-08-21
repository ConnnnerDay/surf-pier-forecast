"""Tests for app.api.internal_auth -- the FastAPI dependency wrapping
app.infra.internal_signature. Uses a small throwaway FastAPI app rather
than the real routers, since require_internal_signature isn't wired onto
them yet (see both modules' docstrings for why).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.internal_auth import InternalAuthDependency
from app.infra.internal_signature import (
    SignedRequestFields,
    SigningKey,
    sha256_hex,
    sign,
)

_KEY = SigningKey(key_id="k1", secret=b"active-secret")
_NOW = 1_000_000.0


def _headers_for(
    *,
    method: str = "POST",
    path: str = "/protected",
    body: bytes = b"",
    user_id: str = "",
    issued_at: int = int(_NOW) - 5,
    expires_at: int = int(_NOW) + 25,
    request_id: str = "req-1",
    key: SigningKey = _KEY,
) -> dict[str, str]:
    fields = SignedRequestFields(
        method=method,
        path=path,
        body_digest=sha256_hex(body),
        user_id=user_id,
        issued_at=issued_at,
        expires_at=expires_at,
        request_id=request_id,
        key_id=key.key_id,
    )
    signature = sign(key, fields)
    headers = {
        "X-Internal-Key-Id": key.key_id,
        "X-Internal-Request-Id": request_id,
        "X-Internal-Issued-At": str(issued_at),
        "X-Internal-Expires-At": str(expires_at),
        "X-Internal-Signature": signature,
    }
    if user_id:
        headers["X-Internal-User-Id"] = user_id
    return headers


@pytest.fixture
def client() -> Iterator[TestClient]:
    verifier = InternalAuthDependency(keys=lambda: (_KEY,), clock=lambda: _NOW)
    app = FastAPI()

    @app.post("/protected")
    async def protected(
        user_id: str | None = Depends(verifier),
    ) -> dict[str, str | None]:
        return {"user_id": user_id}

    with TestClient(app) as test_client:
        yield test_client


def test_valid_signature_reaches_handler_with_user_id(client: TestClient) -> None:
    response = client.post(
        "/protected", headers=_headers_for(user_id="user-42"), content=b""
    )

    assert response.status_code == 200
    assert response.json() == {"user_id": "user-42"}


def test_missing_headers_rejected(client: TestClient) -> None:
    response = client.post("/protected")

    assert response.status_code == 401
    assert "missing internal signature headers" in response.json()["detail"]


def test_invalid_signature_rejected(client: TestClient) -> None:
    headers = _headers_for()
    headers["X-Internal-Signature"] = "0" * 64

    response = client.post("/protected", headers=headers, content=b"")

    assert response.status_code == 401
    assert "signature_mismatch" in response.json()["detail"]


def test_body_tampered_after_signing_rejected(client: TestClient) -> None:
    headers = _headers_for(body=b"original")

    response = client.post("/protected", headers=headers, content=b"tampered")

    assert response.status_code == 401
    assert "signature_mismatch" in response.json()["detail"]


def test_expired_request_rejected(client: TestClient) -> None:
    # issued_at stays within the default clock-skew tolerance so only the
    # expiry check trips.
    headers = _headers_for(issued_at=int(_NOW) - 20, expires_at=int(_NOW) - 1)

    response = client.post("/protected", headers=headers, content=b"")

    assert response.status_code == 401
    assert "expired" in response.json()["detail"]


def test_malformed_timestamp_header_rejected(client: TestClient) -> None:
    headers = _headers_for()
    headers["X-Internal-Issued-At"] = "not-a-number"

    response = client.post("/protected", headers=headers, content=b"")

    assert response.status_code == 401
    assert "malformed internal signature timestamps" in response.json()["detail"]


def test_replay_of_same_request_id_rejected(client: TestClient) -> None:
    headers = _headers_for(request_id="req-replay")

    first = client.post("/protected", headers=headers, content=b"")
    second = client.post("/protected", headers=headers, content=b"")

    assert first.status_code == 200
    assert second.status_code == 401
    assert "replay_detected" in second.json()["detail"]


def test_no_configured_keys_fails_closed_with_500() -> None:
    verifier = InternalAuthDependency(keys=lambda: (), clock=lambda: _NOW)
    app = FastAPI()

    @app.post("/protected")
    async def protected(
        user_id: str | None = Depends(verifier),
    ) -> dict[str, str | None]:
        return {"user_id": user_id}

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post("/protected", headers=_headers_for(), content=b"")

    assert response.status_code == 500
