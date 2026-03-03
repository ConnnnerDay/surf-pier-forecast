"""Tests for API schema/validation helpers and OpenAPI generation."""

from web.openapi import build_openapi_spec
from web.schemas import (
    ApiError,
    ForecastQuery,
    LogCreatePayload,
    ProfilePayload,
    error_envelope,
    normalize_log_stats,
    parse_bool,
    success_envelope,
)


def test_parse_bool():
    assert parse_bool(True) is True
    assert parse_bool("true") is True
    assert parse_bool("1") is True
    assert parse_bool("yes") is True
    assert parse_bool("no") is False


def test_envelope_shapes_stable():
    ok = success_envelope({"x": 1})
    assert set(ok.keys()) == {"ok", "data", "error", "meta"}
    assert ok["ok"] is True
    assert ok["error"] is None

    err = error_envelope("bad", "oops")
    assert set(err.keys()) == {"ok", "data", "error", "meta"}
    assert err["ok"] is False
    assert err["data"] is None
    assert err["error"]["code"] == "bad"


def test_forecast_query_fallback():
    q = ForecastQuery.from_request({"force_refresh": "true"}, fallback_location_id="abc")
    assert q.location_id == "abc"
    assert q.force_refresh is True


def test_profile_payload_validation():
    p = ProfilePayload.from_json({"theme": "dark", "units": "F", "favorites": ["a"]})
    assert p.theme == "dark"

    try:
        ProfilePayload.from_json({"theme": "blue"})
        assert False
    except ApiError as e:
        assert e.code == "invalid_theme"


def test_log_payload_validation():
    l = LogCreatePayload.from_json({"species": "Red Drum"}, location_id="loc1")
    assert l.species == "Red Drum"
    assert l.location_id == "loc1"


def test_log_stats_normalization():
    norm = normalize_log_stats({"total": 2})
    assert norm["total"] == 2
    assert "unique_species" in norm


def test_openapi_contains_versioned_routes():
    spec = build_openapi_spec()
    assert spec["openapi"].startswith("3.")
    assert "/api/v1/forecast" in spec["paths"]
    assert "/api/v1/profile" in spec["paths"]
    assert "/api/v1/log" in spec["paths"]
