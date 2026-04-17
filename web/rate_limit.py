"""Shared IP-keyed sliding-window rate limiting utilities.

Imported by web.auth and web.api to avoid duplicating the same ~50 lines
of locking/pruning logic in both modules.
"""

from __future__ import annotations

import os
import threading
import time

from flask import request

_TRUST_PROXY = os.environ.get("TRUSTED_PROXY", "").strip() == "1"

_PRUNE_EVERY = 200
_prune_counter = 0


def client_ip() -> str:
    """Return the best-effort client IP for rate limiting.

    X-Forwarded-For is only honoured when TRUSTED_PROXY=1 is set.
    Without that flag the header is ignored to prevent clients from spoofing
    arbitrary IPs and bypassing IP-based rate limiting.
    """
    if _TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def prune_store(store: dict[str, tuple[float, int]], window_s: float) -> None:
    """Remove entries whose rate-limit window has expired.

    Must be called while holding the relevant lock.
    """
    now = time.time()
    expired = [ip for ip, (start, _) in store.items() if now - start > window_s]
    for ip in expired:
        del store[ip]


def is_rate_limited(
    store: dict[str, tuple[float, int]],
    lock: threading.Lock,
    max_attempts: int,
    window_s: float,
) -> bool:
    """Return True if the current client IP has exceeded the given rate limit."""
    global _prune_counter
    ip = client_ip()
    now = time.time()
    with lock:
        _prune_counter += 1
        if _prune_counter % _PRUNE_EVERY == 0:
            prune_store(store, window_s)
        start, attempts = store.get(ip, (now, 0))
        if now - start > window_s:
            store[ip] = (now, 0)
            return False
        return attempts >= max_attempts


def record_attempt(
    store: dict[str, tuple[float, int]],
    lock: threading.Lock,
    window_s: float,
) -> None:
    """Increment the attempt counter for the current client IP."""
    ip = client_ip()
    now = time.time()
    with lock:
        start, attempts = store.get(ip, (now, 0))
        if now - start > window_s:
            store[ip] = (now, 1)
        else:
            store[ip] = (start, attempts + 1)


def clear_attempts(
    store: dict[str, tuple[float, int]],
    lock: threading.Lock,
) -> None:
    """Clear the attempt counter for the current client IP."""
    ip = client_ip()
    with lock:
        store.pop(ip, None)
