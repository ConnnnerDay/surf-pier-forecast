"""Tests for catch-log pattern analysis and condition snapshotting."""

from domain.catch_insights import analyze_catch_patterns
from storage.sqlite import (
    add_log_entry,
    create_user,
    get_catch_conditions,
)


def _catch(species="Red drum", tide="Rising", wind="NE", temp=66.0, moon="Full Moon"):
    return {
        "species": species,
        "tide_state": tide,
        "wind_dir": wind,
        "water_temp_f": temp,
        "moon_phase": moon,
    }


class TestAnalyzeCatchPatterns:
    def test_empty(self):
        out = analyze_catch_patterns([])
        assert out["total"] == 0
        assert out["insights"] == []

    def test_dominant_tide_surfaced(self):
        catches = [_catch(tide="Rising") for _ in range(5)] + [_catch(tide="Falling")]
        out = analyze_catch_patterns(catches)
        assert out["factors"]["tide_state"]["value"] == "Rising"
        assert any("rising tide" in i.lower() for i in out["insights"])

    def test_below_min_samples_no_pattern(self):
        # Only 3 catches → below the noise threshold.
        out = analyze_catch_patterns([_catch() for _ in range(3)])
        assert "tide_state" not in out["factors"]

    def test_no_dominance_no_claim(self):
        # Evenly split tides → nothing dominates.
        catches = [_catch(tide="Rising") for _ in range(3)] + [
            _catch(tide="Falling") for _ in range(3)
        ]
        out = analyze_catch_patterns(catches)
        assert "tide_state" not in out["factors"]

    def test_water_temp_band(self):
        catches = [_catch(temp=t) for t in (60, 62, 64, 66, 68, 70)]
        out = analyze_catch_patterns(catches)
        assert "water_temp_f" in out["factors"]
        band = out["factors"]["water_temp_f"]
        assert band["low"] <= band["high"]

    def test_top_species(self):
        catches = [_catch(species="Red drum") for _ in range(4)] + [
            _catch(species="Bluefish")
        ]
        out = analyze_catch_patterns(catches)
        assert out["factors"]["top_species"][0]["species"] == "Red drum"
        assert any("Red drum" in i for i in out["insights"])

    def test_with_conditions_count(self):
        catches = [_catch()] + [
            {"species": "X", "tide_state": None, "wind_dir": None, "moon_phase": None}
        ]
        out = analyze_catch_patterns(catches)
        assert out["with_conditions"] == 1


class TestConditionSnapshotStorage:
    def test_add_and_read_back_conditions(self, app):
        # app fixture initializes an isolated DB.
        uid = create_user("catchuser", "pass1234")
        add_log_entry(
            uid,
            "montauk-ny",
            "Striped bass",
            size="28 in",
            conditions={
                "tide_state": "Falling",
                "wind_dir": "NW",
                "water_temp_f": 58.5,
                "moon_phase": "Waning Crescent",
            },
        )
        rows = get_catch_conditions(uid, "montauk-ny")
        assert len(rows) == 1
        assert rows[0]["tide_state"] == "Falling"
        assert rows[0]["wind_dir"] == "NW"
        assert rows[0]["water_temp_f"] == 58.5
        assert rows[0]["moon_phase"] == "Waning Crescent"

    def test_missing_conditions_are_null(self, app):
        uid = create_user("catchuser2", "pass1234")
        add_log_entry(uid, "montauk-ny", "Bluefish")
        rows = get_catch_conditions(uid, "montauk-ny")
        assert rows[0]["tide_state"] is None
        assert rows[0]["water_temp_f"] is None
