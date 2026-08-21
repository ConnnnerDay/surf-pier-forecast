"""Proves ADR-004's signature requirement is actually attached to the
real `/v1` routers (`app.api.v1.locations`/`forecasts`), not just built
and unit-tested in isolation (test_internal_signature.py,
test_internal_auth.py) — every other router test in this suite
overrides `require_internal_signature` to a no-op specifically so it can
test router/domain behavior without also having to sign requests; this
file is the one place that deliberately does *not* override it, using
monkeypatched env vars plus a cleared `lru_cache` to exercise the real
module-level singleton the routers actually depend on.

Only `/v1/locations/search` is used here (no network, no `AppState`
needed) — the point is proving the dependency is wired, not
re-exercising router behavior already covered elsewhere.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.internal_auth import _keys_from_env
from app.infra.internal_signature import (
    SignedRequestFields,
    SigningKey,
    sha256_hex,
    sign,
)
from app.main import app

_KEY_ID = "test-key"
_KEY_SECRET = "test-secret"


@pytest.fixture
def keyed_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("INTERNAL_SIGNING_KEY_ID", _KEY_ID)
    monkeypatch.setenv("INTERNAL_SIGNING_KEY_SECRET", _KEY_SECRET)
    _keys_from_env.cache_clear()
    try:
        yield TestClient(app)
    finally:
        _keys_from_env.cache_clear()


def _signed_headers(method: str, path: str, body: bytes = b"") -> dict[str, str]:
    key = SigningKey(key_id=_KEY_ID, secret=_KEY_SECRET.encode())
    now = int(time.time())
    fields = SignedRequestFields(
        method=method,
        path=path,
        body_digest=sha256_hex(body),
        user_id="",
        issued_at=now - 1,
        expires_at=now + 20,
        request_id=str(uuid.uuid4()),
        key_id=_KEY_ID,
    )
    return {
        "X-Internal-Key-Id": _KEY_ID,
        "X-Internal-Request-Id": fields.request_id,
        "X-Internal-Issued-At": str(fields.issued_at),
        "X-Internal-Expires-At": str(fields.expires_at),
        "X-Internal-Signature": sign(key, fields),
    }


def test_unsigned_request_to_locations_router_is_rejected(
    keyed_client: TestClient,
) -> None:
    resp = keyed_client.get("/v1/locations/search", params={"q": "wrightsville"})
    assert resp.status_code == 401


def test_correctly_signed_request_to_locations_router_is_accepted(
    keyed_client: TestClient,
) -> None:
    headers = _signed_headers("GET", "/v1/locations/search?q=wrightsville")
    resp = keyed_client.get(
        "/v1/locations/search", params={"q": "wrightsville"}, headers=headers
    )
    assert resp.status_code == 200


def test_no_signing_keys_configured_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INTERNAL_SIGNING_KEY_ID", raising=False)
    monkeypatch.delenv("INTERNAL_SIGNING_KEY_SECRET", raising=False)
    _keys_from_env.cache_clear()
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/v1/locations/search", params={"q": "wrightsville"})
        assert resp.status_code == 500
    finally:
        _keys_from_env.cache_clear()
