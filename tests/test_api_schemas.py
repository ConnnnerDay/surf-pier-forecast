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
    q = ForecastQuery.from_request(
        {"force_refresh": "true"}, fallback_location_id="abc"
    )
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
    payload = LogCreatePayload.from_json({"species": "Red Drum"}, location_id="loc1")
    assert payload.species == "Red Drum"
    assert payload.location_id == "loc1"


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


# ---------------------------------------------------------------------------
# ProfilePayload.from_json — fishing_profile sub-field validation
# ---------------------------------------------------------------------------


def _raises(data, expected_code):
    """Assert that ProfilePayload.from_json raises ApiError with expected code."""
    try:
        ProfilePayload.from_json(data)
        raise AssertionError(
            f"Expected ApiError({expected_code!r}) but no error raised"
        )
    except ApiError as e:
        assert e.code == expected_code, f"expected {expected_code!r}, got {e.code!r}"


def test_profile_payload_rejects_non_dict():
    _raises("a string", "invalid_payload")


def test_profile_payload_rejects_invalid_units():
    _raises({"units": "K"}, "invalid_units")


def test_profile_payload_rejects_non_list_favorites():
    _raises({"favorites": "not-a-list"}, "invalid_favorites")


def test_profile_payload_rejects_too_many_favorites():
    _raises({"favorites": [str(i) for i in range(21)]}, "invalid_favorites")


def test_profile_payload_rejects_non_string_favorites():
    _raises({"favorites": [1, 2, 3]}, "invalid_favorites")


def test_profile_payload_rejects_fishing_profile_non_dict():
    _raises({"fishing_profile": "bad"}, "invalid_profile")


def test_profile_payload_rejects_invalid_fishing_types():
    _raises({"fishing_profile": {"fishing_types": ["scuba"]}}, "invalid_fishing_types")


def test_profile_payload_rejects_invalid_targets():
    _raises({"fishing_profile": {"targets": ["unicorn"]}}, "invalid_targets")


def test_profile_payload_rejects_invalid_experience():
    _raises({"fishing_profile": {"experience": "master"}}, "invalid_experience")


def test_profile_payload_rejects_invalid_bait_pref():
    _raises({"fishing_profile": {"live_bait": "maybe"}}, "invalid_live_bait")


def test_profile_payload_rejects_invalid_preferred_times():
    _raises(
        {"fishing_profile": {"preferred_times": ["midnight"]}},
        "invalid_preferred_times",
    )


def test_profile_payload_rejects_invalid_primary_goal():
    _raises({"fishing_profile": {"primary_goal": "money"}}, "invalid_primary_goal")


def test_profile_payload_rejects_invalid_condition_tolerance():
    _raises(
        {"fishing_profile": {"condition_tolerance": "hurricane"}},
        "invalid_condition_tolerance",
    )


def test_profile_payload_accepts_valid_fishing_profile():
    p = ProfilePayload.from_json(
        {
            "fishing_profile": {
                "fishing_types": ["surf", "pier"],
                "targets": ["bottom"],
                "experience": "intermediate",
                "live_bait": "yes",
                "preferred_times": ["dawn", "morning"],
                "primary_goal": "action",
                "condition_tolerance": "moderate",
            }
        }
    )
    assert p.fishing_profile["experience"] == "intermediate"


def test_success_envelope_meta_merge():
    result = success_envelope({"x": 1}, meta={"total": 5})
    assert result["meta"]["total"] == 5
    assert result["meta"]["version"] == "v1"
