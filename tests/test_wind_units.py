from web.views import _apply_wind_unit_preference, _convert_wind_text_units


def test_convert_wind_text_units_knots_to_mph_single_and_range():
    assert _convert_wind_text_units("10 kt", "mph") == "12 mph"
    assert _convert_wind_text_units("SW 8-12 kt", "mph") == "SW 9-14 mph"


def test_apply_wind_unit_preference_updates_conditions_and_outlook():
    forecast = {
        "conditions": {"wind": "NE 10-14 kt"},
        "outlook": [
            {"wind": "S 7 kt"},
            {"wind": "W 11-16 kt"},
        ],
    }

    _apply_wind_unit_preference(forecast, "mph")

    assert forecast["conditions"]["wind"] == "NE 12-16 mph"
    assert forecast["outlook"][0]["wind"] == "S 8 mph"
    assert forecast["outlook"][1]["wind"] == "W 13-18 mph"


def test_apply_wind_unit_preference_noop_for_knots():
    forecast = {"conditions": {"wind": "10 kt"}, "outlook": [{"wind": "12 kt"}]}
    _apply_wind_unit_preference(forecast, "knots")
    assert forecast["conditions"]["wind"] == "10 kt"
    assert forecast["outlook"][0]["wind"] == "12 kt"
