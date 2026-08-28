"""Tests for app.infra.request_logging.log_requests, the sprint-41
structured per-request trace middleware wired onto app.main.app.

Uses the real app via TestClient (not a bare ASGI app built just for
this test) since the point is proving the middleware is actually wired
onto every request the real app serves, including ones that never reach
a route handler (an unauthenticated /v1 request, which
require_internal_signature rejects before any route body runs) --
exactly the case the middleware exists to still produce a trace line
for.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_echoes_caller_supplied_request_id(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.request"):
        response = client.get(
            "/health/live", headers={"X-Internal-Request-Id": "abc-123"}
        )

    assert response.status_code == 200
    assert response.headers["X-Internal-Request-Id"] == "abc-123"

    record = json.loads(caplog.records[-1].message)
    assert record["request_id"] == "abc-123"
    assert record["method"] == "GET"
    assert record["path"] == "/health/live"
    assert record["status_code"] == 200
    assert isinstance(record["duration_ms"], (int, float))
    assert record["duration_ms"] >= 0


def test_generates_a_request_id_when_caller_omits_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.request"):
        response = client.get("/health/live")

    generated_id = response.headers["X-Internal-Request-Id"]
    assert generated_id

    record = json.loads(caplog.records[-1].message)
    assert record["request_id"] == generated_id


def test_traces_a_request_that_never_reaches_a_route_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unsigned /v1 request is rejected by `require_internal_signature`
    before any route body runs -- the middleware still produces a trace
    line for it, at whatever status code the rejection used, since it
    wraps the whole ASGI call rather than sitting behind that dependency.
    """
    with caplog.at_level(logging.INFO, logger="app.request"):
        response = client.get(
            "/v1/locations/search",
            params={"q": "wrightsville"},
            headers={"X-Internal-Request-Id": "unsigned-request"},
        )

    assert response.status_code >= 400
    record = json.loads(caplog.records[-1].message)
    assert record["request_id"] == "unsigned-request"
    assert record["path"] == "/v1/locations/search"
    assert record["status_code"] == response.status_code


def test_log_line_never_carries_query_string_or_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ "Safe context" (sprint 41's own wording): the trace line is
    exactly request_id/method/path/status_code/duration_ms -- no query
    string, no header values, no body.
    """
    with caplog.at_level(logging.INFO, logger="app.request"):
        client.get("/v1/locations/search", params={"q": "secret-ish-query"})

    record = json.loads(caplog.records[-1].message)
    assert set(record.keys()) == {
        "request_id",
        "method",
        "path",
        "status_code",
        "duration_ms",
    }
    assert "secret-ish-query" not in json.dumps(record)
