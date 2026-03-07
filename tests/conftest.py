"""Shared pytest fixtures and helpers for the surf-pier-forecast test suite."""

from __future__ import annotations

import re

import pytest

from app import create_app
from storage.sqlite import init_db


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
