"""Tests for API schema/validation helpers and OpenAPI generation."""

from web.openapi import build_openapi_spec
from web.schemas import (
    ApiError,
    ForecastQuery,
    LogCreatePayload,
    ProfilePayload,
    _MAX_FAVORITES,
    _MAX_FAVORITES_ENTRY_LEN,
    _MAX_NOTES_LEN,
    _MAX_SPECIES_LEN,
    _MAX_SIZE_LEN,
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
    # Endpoints added for catch-pattern learning and web-push notifications.
    assert "/api/v1/log/patterns" in spec["paths"]
    assert "/api/v1/push/subscribe" in spec["paths"]
    assert "/api/v1/push/public-key" in spec["paths"]
    assert "/api/v1/community/activity" in spec["paths"]


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


def test_notification_prefs_rejects_bad_min_rating():
    _raises(
        {"notification_prefs": {"min_rating": "Amazing"}},
        "invalid_min_rating",
    )


def test_notification_prefs_rejects_out_of_range_lead_hours():
    _raises(
        {"notification_prefs": {"lead_hours": 99}},
        "invalid_lead_hours",
    )


def test_notification_prefs_rejects_non_bool_enabled():
    _raises(
        {"notification_prefs": {"enabled": "yes"}},
        "invalid_notification_enabled",
    )


def test_notification_prefs_accepted_and_normalized():
    p = ProfilePayload.from_json(
        {
            "notification_prefs": {
                "enabled": True,
                "email": True,
                "push": False,
                "min_rating": "Excellent",
                "lead_hours": 4,
                "bogus": "dropped",
            }
        }
    )
    np = p.notification_prefs
    assert np["enabled"] is True
    assert np["min_rating"] == "Excellent"
    assert np["lead_hours"] == 4
    assert "bogus" not in np  # unknown keys are stripped
    assert "notification_prefs" in p.as_updates()


def test_notification_prefs_preserves_weekly_email():
    # weekly_email is owned by the display-settings form; the API must keep it
    # so saving alert settings doesn't wipe it.
    p = ProfilePayload.from_json(
        {"notification_prefs": {"enabled": True, "weekly_email": True}}
    )
    assert p.notification_prefs["weekly_email"] is True


def test_profile_payload_rejects_out_of_range_max_wind():
    _raises(
        {"fishing_profile": {"max_wind_kt": 999}},
        "invalid_max_wind_kt",
    )


def test_profile_payload_rejects_non_numeric_max_wave():
    _raises(
        {"fishing_profile": {"max_wave_ft": "lots"}},
        "invalid_max_wave_ft",
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
                "max_wind_kt": 15,
                "max_wave_ft": 4,
            }
        }
    )
    assert p.fishing_profile["experience"] == "intermediate"
    assert p.fishing_profile["max_wind_kt"] == 15


def test_profile_payload_rejects_non_bool_share_catches():
    _raises({"fishing_profile": {"share_catches": "yes"}}, "invalid_share_catches")


def test_profile_payload_accepts_share_catches():
    p = ProfilePayload.from_json({"fishing_profile": {"share_catches": True}})
    assert p.fishing_profile["share_catches"] is True


def test_success_envelope_meta_merge():
    result = success_envelope({"x": 1}, meta={"total": 5})
    assert result["meta"]["total"] == 5
    assert result["meta"]["version"] == "v1"


# ---------------------------------------------------------------------------
# Boundary condition tests for _MAX_* validation constants
# ---------------------------------------------------------------------------


def test_favorites_at_max_accepted():
    p = ProfilePayload.from_json({"favorites": ["x"] * _MAX_FAVORITES})
    assert len(p.favorites) == _MAX_FAVORITES


def test_favorites_over_max_rejected():
    _raises({"favorites": ["x"] * (_MAX_FAVORITES + 1)}, "invalid_favorites")


def test_favorite_entry_at_max_len_accepted():
    entry = "a" * _MAX_FAVORITES_ENTRY_LEN
    p = ProfilePayload.from_json({"favorites": [entry]})
    assert p.favorites[0] == entry


def test_favorite_entry_over_max_len_rejected():
    _raises({"favorites": ["a" * (_MAX_FAVORITES_ENTRY_LEN + 1)]}, "invalid_favorites")


def test_log_notes_at_max_len_accepted():
    payload = LogCreatePayload.from_json(
        {"species": "Bass", "notes": "x" * _MAX_NOTES_LEN}, location_id="loc1"
    )
    assert len(payload.notes) == _MAX_NOTES_LEN


def test_log_notes_over_max_len_rejected():
    try:
        LogCreatePayload.from_json(
            {"species": "Bass", "notes": "x" * (_MAX_NOTES_LEN + 1)}, location_id="loc1"
        )
        raise AssertionError("Expected ApiError for oversized notes")
    except ApiError as e:
        assert e.code == "invalid_notes"


def test_log_species_at_max_len_accepted():
    payload = LogCreatePayload.from_json(
        {"species": "B" * _MAX_SPECIES_LEN}, location_id="loc1"
    )
    assert len(payload.species) == _MAX_SPECIES_LEN


def test_log_species_over_max_len_rejected():
    try:
        LogCreatePayload.from_json(
            {"species": "B" * (_MAX_SPECIES_LEN + 1)}, location_id="loc1"
        )
        raise AssertionError("Expected ApiError for oversized species")
    except ApiError as e:
        assert e.code == "invalid_species"


def test_log_size_at_max_len_accepted():
    payload = LogCreatePayload.from_json(
        {"species": "Bass", "size": "x" * _MAX_SIZE_LEN}, location_id="loc1"
    )
    assert len(payload.size) == _MAX_SIZE_LEN


def test_log_size_over_max_len_rejected():
    try:
        LogCreatePayload.from_json(
            {"species": "Bass", "size": "x" * (_MAX_SIZE_LEN + 1)}, location_id="loc1"
        )
        raise AssertionError("Expected ApiError for oversized size")
    except ApiError as e:
        assert e.code == "invalid_size"
