"""FastAPI dependency wrapping `app.infra.internal_signature`'s
verification primitive (sprint 44, partial -- see that module's docstring).

`InternalAuthDependency` takes its keys and clock via constructor
injection, matching `SnapshotCache`/`StationCatalogCache`'s
constructor-injection pattern, so tests never need to monkeypatch
`os.environ` or clear an `lru_cache`. `require_internal_signature` is the
default instance `app.api.v1.locations`/`forecasts` depend on, reading
keys from the environment (`INTERNAL_SIGNING_KEY_ID`/
`INTERNAL_SIGNING_KEY_SECRET` for the active key,
`INTERNAL_SIGNING_KEY_ID_PREVIOUS`/`INTERNAL_SIGNING_KEY_SECRET_PREVIOUS`
for ADR-004's rotation) -- now a real deployment requirement, documented in
`apps/api/README.md`, since every `/v1` route depends on this instance and
fails closed (500) without them configured. Local dev needs the same
`INTERNAL_SIGNING_KEY_ID`/`INTERNAL_SIGNING_KEY_SECRET` values set on both
`apps/api` and `apps/web` (`lib/internal-api-client.ts` reads the matching
names) -- see both apps' READMEs.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from functools import lru_cache

from fastapi import HTTPException, Request

from app.infra.internal_signature import (
    ReplayGuard,
    SignatureError,
    SignedRequestFields,
    SigningKey,
    sha256_hex,
    verify,
)

_HEADER_KEY_ID = "X-Internal-Key-Id"
_HEADER_REQUEST_ID = "X-Internal-Request-Id"
_HEADER_USER_ID = "X-Internal-User-Id"
_HEADER_ISSUED_AT = "X-Internal-Issued-At"
_HEADER_EXPIRES_AT = "X-Internal-Expires-At"
_HEADER_SIGNATURE = "X-Internal-Signature"

_REQUIRED_HEADERS = (
    _HEADER_KEY_ID,
    _HEADER_REQUEST_ID,
    _HEADER_ISSUED_AT,
    _HEADER_EXPIRES_AT,
    _HEADER_SIGNATURE,
)


@lru_cache(maxsize=1)
def _keys_from_env() -> tuple[SigningKey, ...]:
    keys: list[SigningKey] = []
    active_id = os.environ.get("INTERNAL_SIGNING_KEY_ID")
    active_secret = os.environ.get("INTERNAL_SIGNING_KEY_SECRET")
    if active_id and active_secret:
        keys.append(SigningKey(key_id=active_id, secret=active_secret.encode()))
    previous_id = os.environ.get("INTERNAL_SIGNING_KEY_ID_PREVIOUS")
    previous_secret = os.environ.get("INTERNAL_SIGNING_KEY_SECRET_PREVIOUS")
    if previous_id and previous_secret:
        keys.append(SigningKey(key_id=previous_id, secret=previous_secret.encode()))
    return tuple(keys)


def _canonical_path(request: Request) -> str:
    query = request.url.query
    return f"{request.url.path}?{query}" if query else request.url.path


class InternalAuthDependency:
    """Callable FastAPI dependency verifying ADR-004's internal request
    signature. Fails closed: no configured keys is a 500 (misconfiguration),
    never a silent pass-through.
    """

    def __init__(
        self,
        *,
        keys: Callable[[], Sequence[SigningKey]] = _keys_from_env,
        replay_guard: ReplayGuard | None = None,
        max_skew_seconds: float = 30.0,
        max_validity_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._keys = keys
        self._replay_guard = (
            replay_guard if replay_guard is not None else ReplayGuard(clock=clock)
        )
        self._max_skew_seconds = max_skew_seconds
        self._max_validity_seconds = max_validity_seconds
        self._clock = clock

    async def __call__(self, request: Request) -> str | None:
        keys = self._keys()
        if not keys:
            raise HTTPException(
                status_code=500, detail="internal signing keys not configured"
            )

        missing = [h for h in _REQUIRED_HEADERS if h not in request.headers]
        if missing:
            raise HTTPException(
                status_code=401, detail="missing internal signature headers"
            )

        try:
            issued_at = int(request.headers[_HEADER_ISSUED_AT])
            expires_at = int(request.headers[_HEADER_EXPIRES_AT])
        except ValueError as exc:
            raise HTTPException(
                status_code=401,
                detail="malformed internal signature timestamps",
            ) from exc

        body = await request.body()
        fields = SignedRequestFields(
            method=request.method,
            path=_canonical_path(request),
            body_digest=sha256_hex(body),
            user_id=request.headers.get(_HEADER_USER_ID, ""),
            issued_at=issued_at,
            expires_at=expires_at,
            request_id=request.headers[_HEADER_REQUEST_ID],
            key_id=request.headers[_HEADER_KEY_ID],
        )

        try:
            return verify(
                keys=keys,
                fields=fields,
                signature=request.headers[_HEADER_SIGNATURE],
                replay_guard=self._replay_guard,
                now=self._clock(),
                max_skew_seconds=self._max_skew_seconds,
                max_validity_seconds=self._max_validity_seconds,
            )
        except SignatureError as exc:
            raise HTTPException(
                status_code=401,
                detail=f"internal signature rejected: {exc.reason}",
            ) from exc


require_internal_signature = InternalAuthDependency()
