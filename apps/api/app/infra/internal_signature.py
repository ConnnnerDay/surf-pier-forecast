"""Internal request signing (sprint 44, partial: verification primitive only).

ADR-004 (`docs/architecture.md`) specifies HMAC-SHA-256 signing between the
Next.js BFF and this FastAPI service: the BFF signs the HTTP method, the
canonical path, a SHA-256 digest of the body, an optional authenticated
internal user identifier, an issued-at time, a short expiration, and a
random request ID, using a key identified by a rotatable key ID. FastAPI
verifies with constant-time comparison, a clock-skew window, expiration,
and replay detection by request ID.

This module is only the pure verification primitive -- signing/verification
logic plus a replay-detection guard, independent of FastAPI.
`app.api.internal_auth` wraps it as a request dependency. **Neither is
wired onto the `/v1` routers yet**: `apps/web` has no signer to pair with
it (Next.js is still the sprint-13 skeleton), and wiring a mandatory
signature check onto routes nothing can currently sign would make
`apps/api` uncallable by its only real client. Wiring is the next step
once `apps/web` grows an internal HTTP client that can sign requests --
plausibly alongside sprint 28's Better Auth work, since ADR-004's
"authenticated internal user identifier" is exactly Better Auth's opaque
user ID.

No legacy precedent: the legacy Flask app is single-process and has no
internal service boundary to sign across.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SigningKey:
    """One named HMAC key. `key_id` travels alongside the signature so the
    verifier knows which secret to check against -- this is what makes key
    rotation possible without a hard cutover (ADR-004: "support an active
    and previous key during rotation").
    """

    key_id: str
    secret: bytes


@dataclass(frozen=True)
class SignedRequestFields:
    """The canonical fields ADR-004 signs, already extracted/computed by
    the caller. `path` is the request target including its query string
    (so query-parameter tampering also invalidates the signature -- e.g.
    `GET /v1/locations/search?q=...`). `body_digest` is always computed by
    the verifier from the bytes it actually received, never trusted from a
    header, so a tampered body produces a signature mismatch rather than
    needing a separate integrity check.
    """

    method: str
    path: str
    body_digest: str
    user_id: str
    issued_at: int
    expires_at: int
    request_id: str
    key_id: str


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_string(fields: SignedRequestFields) -> str:
    """The exact byte sequence that gets HMAC'd. Field order and the `\\n`
    delimiter must match on both the signer and verifier -- this is the
    entire contract, so it's centralized here rather than duplicated.
    """
    return "\n".join(
        [
            fields.method.upper(),
            fields.path,
            fields.body_digest,
            fields.user_id,
            str(fields.issued_at),
            str(fields.expires_at),
            fields.request_id,
            fields.key_id,
        ]
    )


def sign(key: SigningKey, fields: SignedRequestFields) -> str:
    """Compute the hex HMAC-SHA-256 signature a signer would send. Used by
    tests to construct validly signed requests; a real signer (`apps/web`,
    once it has one) calls the equivalent logic on its own side.
    """
    return hmac.new(
        key.secret, canonical_string(fields).encode(), hashlib.sha256
    ).hexdigest()


class SignatureError(Exception):
    """Raised by `verify` for every rejection reason. `reason` is a short,
    stable, machine-readable code (never the secret or key material) that
    `app.api.internal_auth` maps to an HTTP 401 detail.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ReplayGuard:
    """Tracks request IDs seen within their own validity window, so a
    captured-and-resent request is rejected even though its signature is
    still otherwise valid (ADR-004: "records request IDs for replay
    detection within the validity window"). Entries expire at their own
    `expires_at` rather than a fixed TTL -- a request is only worth
    remembering for as long as it would otherwise still be accepted.

    Injectable clock for deterministic tests, matching
    `app.infra.snapshot_cache.SnapshotCache`'s pattern. In-memory and
    per-process, an accepted simplification until this runs as more than
    one process -- the same simplification `SnapshotCache` already makes.
    """

    clock: Callable[[], float] = time.time
    _seen: dict[str, float] = field(default_factory=dict)

    def check_and_record(self, request_id: str, expires_at: int) -> None:
        now = self.clock()
        expired_ids = [rid for rid, exp in self._seen.items() if exp <= now]
        for rid in expired_ids:
            del self._seen[rid]

        if request_id in self._seen:
            raise SignatureError("replay_detected")
        self._seen[request_id] = expires_at


def verify(
    *,
    keys: Sequence[SigningKey],
    fields: SignedRequestFields,
    signature: str,
    replay_guard: ReplayGuard,
    now: float | None = None,
    max_skew_seconds: float = 30.0,
    max_validity_seconds: float = 60.0,
) -> str | None:
    """Raise `SignatureError` if *signature* doesn't authenticate *fields*
    under any key in *keys*, or if the request fails any freshness/replay
    check. Returns `fields.user_id` (or `None` if empty) on success.
    """
    now = time.time() if now is None else now

    if fields.expires_at <= fields.issued_at:
        raise SignatureError("malformed_timestamps")
    if fields.expires_at - fields.issued_at > max_validity_seconds:
        raise SignatureError("validity_window_too_long")
    if abs(now - fields.issued_at) > max_skew_seconds:
        raise SignatureError("clock_skew_too_large")
    if now > fields.expires_at:
        raise SignatureError("expired")

    matched_key = next((k for k in keys if k.key_id == fields.key_id), None)
    if matched_key is None:
        raise SignatureError("unknown_key_id")

    expected = sign(matched_key, fields)
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("signature_mismatch")

    replay_guard.check_and_record(fields.request_id, fields.expires_at)
    return fields.user_id or None
