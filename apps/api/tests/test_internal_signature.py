"""Tests for app.infra.internal_signature -- the pure ADR-004 signature
verification primitive, no FastAPI involved (see test_internal_auth.py for
the dependency layer).
"""

from __future__ import annotations

import pytest

from app.infra.internal_signature import (
    ReplayGuard,
    SignatureError,
    SignedRequestFields,
    SigningKey,
    sha256_hex,
    sign,
    verify,
)

_KEY = SigningKey(key_id="k1", secret=b"active-secret")
_PREVIOUS_KEY = SigningKey(key_id="k0", secret=b"previous-secret")


def _fields(**overrides: object) -> SignedRequestFields:
    base = {
        "method": "GET",
        "path": "/v1/forecasts/wrightsville-beach-nc",
        "body_digest": sha256_hex(b""),
        "user_id": "",
        "issued_at": 1000,
        "expires_at": 1030,
        "request_id": "req-1",
        "key_id": "k1",
    }
    base.update(overrides)
    return SignedRequestFields(**base)  # type: ignore[arg-type]


def test_valid_signature_verifies_and_returns_user_id() -> None:
    fields = _fields(user_id="user-42")
    signature = sign(_KEY, fields)

    result = verify(
        keys=[_KEY],
        fields=fields,
        signature=signature,
        replay_guard=ReplayGuard(clock=lambda: 1010.0),
        now=1010.0,
    )

    assert result == "user-42"


def test_valid_signature_with_no_user_id_returns_none() -> None:
    fields = _fields()
    signature = sign(_KEY, fields)

    result = verify(
        keys=[_KEY],
        fields=fields,
        signature=signature,
        replay_guard=ReplayGuard(clock=lambda: 1010.0),
        now=1010.0,
    )

    assert result is None


def test_tampered_body_digest_fails_verification() -> None:
    fields = _fields()
    signature = sign(_KEY, fields)
    tampered = _fields(body_digest=sha256_hex(b"different body"))

    with pytest.raises(SignatureError) as exc_info:
        verify(
            keys=[_KEY],
            fields=tampered,
            signature=signature,
            replay_guard=ReplayGuard(clock=lambda: 1010.0),
            now=1010.0,
        )

    assert exc_info.value.reason == "signature_mismatch"


def test_tampered_method_fails_verification() -> None:
    fields = _fields(method="GET")
    signature = sign(_KEY, fields)
    tampered = _fields(method="DELETE")

    with pytest.raises(SignatureError) as exc_info:
        verify(
            keys=[_KEY],
            fields=tampered,
            signature=signature,
            replay_guard=ReplayGuard(clock=lambda: 1010.0),
            now=1010.0,
        )

    assert exc_info.value.reason == "signature_mismatch"


def test_tampered_path_fails_verification() -> None:
    fields = _fields(path="/v1/forecasts/a")
    signature = sign(_KEY, fields)
    tampered = _fields(path="/v1/forecasts/b")

    with pytest.raises(SignatureError) as exc_info:
        verify(
            keys=[_KEY],
            fields=tampered,
            signature=signature,
            replay_guard=ReplayGuard(clock=lambda: 1010.0),
            now=1010.0,
        )

    assert exc_info.value.reason == "signature_mismatch"


def test_expired_request_rejected() -> None:
    # A generous max_skew_seconds isolates the expiry check from the
    # clock-skew check below -- issued_at is only 31s in the past, which
    # would otherwise also trip the default 30s skew tolerance.
    fields = _fields(issued_at=1000, expires_at=1030)
    signature = sign(_KEY, fields)

    with pytest.raises(SignatureError) as exc_info:
        verify(
            keys=[_KEY],
            fields=fields,
            signature=signature,
            replay_guard=ReplayGuard(clock=lambda: 1031.0),
            now=1031.0,
            max_skew_seconds=1000.0,
        )

    assert exc_info.value.reason == "expired"


def test_clock_skew_too_large_rejected() -> None:
    # issued_at is 1000, "now" is 500 seconds earlier -- clearly outside
    # any reasonable clock-skew tolerance, even though expires_at hasn't
    # been reached yet.
    fields = _fields(issued_at=1000, expires_at=1030)
    signature = sign(_KEY, fields)

    with pytest.raises(SignatureError) as exc_info:
        verify(
            keys=[_KEY],
            fields=fields,
            signature=signature,
            replay_guard=ReplayGuard(clock=lambda: 500.0),
            now=500.0,
            max_skew_seconds=30.0,
        )

    assert exc_info.value.reason == "clock_skew_too_large"


def test_validity_window_too_long_rejected() -> None:
    fields = _fields(issued_at=1000, expires_at=1000 + 3600)
    signature = sign(_KEY, fields)

    with pytest.raises(SignatureError) as exc_info:
        verify(
            keys=[_KEY],
            fields=fields,
            signature=signature,
            replay_guard=ReplayGuard(clock=lambda: 1010.0),
            now=1010.0,
            max_validity_seconds=60.0,
        )

    assert exc_info.value.reason == "validity_window_too_long"


def test_malformed_timestamps_rejected() -> None:
    fields = _fields(issued_at=1000, expires_at=900)
    signature = sign(_KEY, fields)

    with pytest.raises(SignatureError) as exc_info:
        verify(
            keys=[_KEY],
            fields=fields,
            signature=signature,
            replay_guard=ReplayGuard(clock=lambda: 1010.0),
            now=1010.0,
        )

    assert exc_info.value.reason == "malformed_timestamps"


def test_unknown_key_id_rejected() -> None:
    fields = _fields(key_id="not-configured")
    signature = sign(_KEY, fields)

    with pytest.raises(SignatureError) as exc_info:
        verify(
            keys=[_KEY],
            fields=fields,
            signature=signature,
            replay_guard=ReplayGuard(clock=lambda: 1010.0),
            now=1010.0,
        )

    assert exc_info.value.reason == "unknown_key_id"


def test_previous_key_accepted_during_rotation() -> None:
    fields = _fields(key_id="k0")
    signature = sign(_PREVIOUS_KEY, fields)

    result = verify(
        keys=[_KEY, _PREVIOUS_KEY],
        fields=fields,
        signature=signature,
        replay_guard=ReplayGuard(clock=lambda: 1010.0),
        now=1010.0,
    )

    assert result is None


def test_replay_of_same_request_id_rejected() -> None:
    fields = _fields()
    signature = sign(_KEY, fields)
    guard = ReplayGuard(clock=lambda: 1010.0)

    verify(
        keys=[_KEY], fields=fields, signature=signature, replay_guard=guard, now=1010.0
    )

    with pytest.raises(SignatureError) as exc_info:
        verify(
            keys=[_KEY],
            fields=fields,
            signature=signature,
            replay_guard=guard,
            now=1015.0,
        )

    assert exc_info.value.reason == "replay_detected"


def test_replay_guard_prunes_entries_past_their_own_expiry() -> None:
    """A request ID is only remembered for as long as it would otherwise
    still be accepted (module docstring). Once the clock passes an
    entry's own `expires_at`, a fresh `check_and_record` call prunes it
    from internal state -- proven directly rather than by re-deriving it
    from `verify`'s behavior.
    """
    guard = ReplayGuard(clock=lambda: 2010.0)
    guard.check_and_record("req-a", expires_at=1030)  # already-expired entry
    assert "req-a" in guard._seen

    guard.check_and_record("req-b", expires_at=2040)

    assert "req-a" not in guard._seen
    assert "req-b" in guard._seen
