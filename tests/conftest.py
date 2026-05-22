"""Shared pytest fixtures and helpers for the surf-pier-forecast test suite."""

from __future__ import annotations

import re

import pytest

from app import create_app
from storage.sqlite import init_db
import storage.sqlite as _sqlite
import storage.cache as _cache
import domain.forecast as _forecast
import web.api as _api


@pytest.fixture(autouse=True)
def _clear_in_process_caches():
    """Clear all module-level in-process caches before each test.

    Several modules maintain singleton caches (prefs, forecast memory layer,
    personalize results) that are keyed by database IDs.  Because each test
    uses an isolated SQLite DB (different tmp_path), user/location IDs can
    collide across tests, causing stale cache hits.  Clearing before each test
    ensures isolation without requiring all tests to know about the caches.
    """
    _sqlite._PREFS_CACHE.clear()
    _sqlite._USER_CACHE.clear()
    _sqlite._LOG_STATS_CACHE.clear()
    _cache._MEM_CACHE.clear()
    _forecast._PERSONALIZE_CACHE.clear()
    # Custom marker / habitat caches — module-level singletons that must be
    # reset when each test gets a fresh isolated SQLite DB so cached data
    # from a previous test's DB doesn't bleed into the next test.
    _sqlite._CUSTOM_MARKERS_CACHE = None
    _sqlite._CUSTOM_MARKERS_TS = 0.0
    _sqlite._CUSTOM_HABITATS_CACHE = None
    _sqlite._CUSTOM_HABITATS_TS = 0.0
    _sqlite._HABITAT_OVERRIDES_CACHE = None
    _sqlite._HABITAT_OVERRIDES_TS = 0.0
    _sqlite._SUPPRESSED_SPOTS_CACHE = None
    _sqlite._SUPPRESSED_SPOTS_TS = 0.0
    _sqlite._CUSTOM_HABITAT_TYPES_CACHE = None
    _sqlite._CUSTOM_HABITAT_TYPES_TS = 0.0
    _api._HABITATS_CACHE.clear()
    yield
    # Also clear after in case a test leaves behind entries that bleed forward.
    _sqlite._PREFS_CACHE.clear()
    _sqlite._USER_CACHE.clear()
    _sqlite._LOG_STATS_CACHE.clear()
    _cache._MEM_CACHE.clear()
    _forecast._PERSONALIZE_CACHE.clear()
    _sqlite._CUSTOM_MARKERS_CACHE = None
    _sqlite._CUSTOM_MARKERS_TS = 0.0
    _sqlite._CUSTOM_HABITATS_CACHE = None
    _sqlite._CUSTOM_HABITATS_TS = 0.0
    _sqlite._HABITAT_OVERRIDES_CACHE = None
    _sqlite._HABITAT_OVERRIDES_TS = 0.0
    _sqlite._SUPPRESSED_SPOTS_CACHE = None
    _sqlite._SUPPRESSED_SPOTS_TS = 0.0
    _sqlite._CUSTOM_HABITAT_TYPES_CACHE = None
    _sqlite._CUSTOM_HABITAT_TYPES_TS = 0.0
    _api._HABITATS_CACHE.clear()


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Flask app configured for testing with an isolated SQLite database.

    Test files that need a different app setup can define their own ``app``
    fixture locally — pytest will use the most-local definition.
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    init_db()
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Test client for the Flask app."""
    return app.test_client()


def csrf_token_from_html(html: bytes) -> str:
    """Extract the CSRF token from an HTML response body."""
    m = re.search(r'name="csrf_token" value="([^"]+)"', html.decode("utf-8"))
    assert m is not None, "No CSRF token found in HTML"
    return m.group(1)


def set_session(client, **kwargs):
    """Convenience helper to set session values before a request."""
    with client.session_transaction() as sess:
        for key, value in kwargs.items():
            sess[key] = value


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Flask app configured for testing with an isolated SQLite database.

    Test files that need a different app setup can define their own ``app``
    fixture locally — pytest will use the most-local definition.
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("storage.sqlite.DB_PATH", db_path)
    init_db()
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Test client for the Flask app."""
    return app.test_client()


def csrf_token_from_html(html: bytes) -> str:
    """Extract the CSRF token from an HTML response body."""
    m = re.search(r'name="csrf_token" value="([^"]+)"', html.decode("utf-8"))
    assert m is not None, "No CSRF token found in HTML"
    return m.group(1)


def set_session(client, **kwargs):
    """Convenience helper to set session values before a request."""
    with client.session_transaction() as sess:
        for key, value in kwargs.items():
            sess[key] = value
