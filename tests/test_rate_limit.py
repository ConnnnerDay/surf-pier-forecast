"""Tests for web.rate_limit covering previously missed branches.

Missing lines from the full-suite report:
  29-31  client_ip() X-Forwarded-For path (TRUSTED_PROXY=1)
  40-43  prune_store() expired-entry loop
  59     is_rate_limited() prune branch (_prune_counter % _PRUNE_EVERY == 0)
  62-63  is_rate_limited() expired-ip reset path
  78     record_attempt() expired-ip reset path
"""
from __future__ import annotations

import threading
import time

import pytest


# ---------------------------------------------------------------------------
# Fixture: a minimal Flask app to provide request contexts
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _app():
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


# ---------------------------------------------------------------------------
# Lines 29-31: client_ip() honours X-Forwarded-For when TRUSTED_PROXY=1
# ---------------------------------------------------------------------------


class TestClientIp:
    def test_forwarded_for_used_when_trusted(self, _app, monkeypatch):
        """Lines 29-31: when _TRUST_PROXY is True, the leftmost X-Forwarded-For
        address is returned."""
        import web.rate_limit as rl

        monkeypatch.setattr(rl, "_TRUST_PROXY", True)
        with _app.test_request_context(
            "/", headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
        ):
            assert rl.client_ip() == "1.2.3.4"

    def test_forwarded_for_empty_falls_back_to_remote_addr(self, _app, monkeypatch):
        """Lines 29-31: when the X-Forwarded-For header is empty even with
        _TRUST_PROXY=True, falls back to remote_addr."""
        import web.rate_limit as rl

        monkeypatch.setattr(rl, "_TRUST_PROXY", True)
        with _app.test_request_context("/"):
            ip = rl.client_ip()
        assert ip in ("127.0.0.1", "unknown", "localhost")

    def test_forwarded_for_ignored_when_not_trusted(self, _app, monkeypatch):
        """Default (no TRUSTED_PROXY): X-Forwarded-For header is ignored."""
        import web.rate_limit as rl

        monkeypatch.setattr(rl, "_TRUST_PROXY", False)
        with _app.test_request_context(
            "/", headers={"X-Forwarded-For": "9.9.9.9"}
        ):
            ip = rl.client_ip()
        assert ip != "9.9.9.9"


# ---------------------------------------------------------------------------
# Lines 40-43: prune_store() removes entries whose window has expired
# ---------------------------------------------------------------------------


class TestPruneStore:
    def test_expired_entries_removed(self):
        """Lines 40-43: entries older than window_s are deleted."""
        from web.rate_limit import prune_store

        now = time.time()
        store = {
            "old.ip": (now - 1000, 5),   # expired
            "new.ip": (now - 10, 2),     # still within window
        }
        prune_store(store, window_s=60)
        assert "old.ip" not in store
        assert "new.ip" in store

    def test_no_expired_entries_leaves_store_intact(self):
        """prune_store with no stale entries leaves the store unchanged."""
        from web.rate_limit import prune_store

        now = time.time()
        store = {"live.ip": (now, 1)}
        prune_store(store, window_s=60)
        assert "live.ip" in store


# ---------------------------------------------------------------------------
# Line 59: is_rate_limited() triggers prune when counter hits _PRUNE_EVERY
# ---------------------------------------------------------------------------


class TestIsRateLimitedPrune:
    def test_prune_called_at_interval(self, _app, monkeypatch):
        """Line 59: when _prune_counter % _PRUNE_EVERY == 0 prune_store is called."""
        import web.rate_limit as rl

        prune_calls = []
        original_prune = rl.prune_store

        def counting_prune(store, window_s):
            prune_calls.append(1)
            original_prune(store, window_s)

        monkeypatch.setattr(rl, "prune_store", counting_prune)
        monkeypatch.setattr(rl, "_PRUNE_EVERY", 1)
        monkeypatch.setattr(rl, "_prune_counter", 0)

        store: dict = {}
        lock = threading.Lock()
        with _app.test_request_context("/"):
            rl.is_rate_limited(store, lock, max_attempts=10, window_s=60)

        assert len(prune_calls) >= 1


# ---------------------------------------------------------------------------
# Lines 62-63: is_rate_limited() resets counter when existing entry is expired
# ---------------------------------------------------------------------------


class TestIsRateLimitedExpiredReset:
    def test_expired_ip_entry_is_reset_and_not_limited(self, _app):
        """Lines 62-63: if the stored entry for the IP is past window_s the counter
        is reset to 0 and the function returns False (not rate limited)."""
        import web.rate_limit as rl

        store: dict = {}
        lock = threading.Lock()
        with _app.test_request_context("/"):
            # Discover the actual IP used in this context.
            ip = rl.client_ip()
            store[ip] = (time.time() - 1000, 99)  # expired entry
            result = rl.is_rate_limited(store, lock, max_attempts=5, window_s=60)
        assert result is False
        assert store[ip][1] == 0  # counter reset to zero


# ---------------------------------------------------------------------------
# Line 78: record_attempt() resets counter when existing entry is expired
# ---------------------------------------------------------------------------


class TestRecordAttemptExpiredReset:
    def test_expired_ip_entry_starts_fresh_counter(self, _app):
        """Line 78: when the stored window for the IP has passed, the counter
        starts at 1 (not incremented from the stale value)."""
        import web.rate_limit as rl

        store: dict = {}
        lock = threading.Lock()
        with _app.test_request_context("/"):
            ip = rl.client_ip()
            store[ip] = (time.time() - 1000, 50)  # expired entry
            rl.record_attempt(store, lock, window_s=60)
        assert store[ip][1] == 1
