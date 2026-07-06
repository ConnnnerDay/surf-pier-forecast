"""Tests for catch-log pattern analysis and condition snapshotting."""

from domain.catch_insights import analyze_catch_patterns
from storage.sqlite import (
    add_log_entry,
    create_user,
    get_catch_conditions,
)


def _catch(species="Red drum", tide="Rising", wind="NE", temp=66.0, moon="Full Moon",
           bait="Live shrimp", rig="Hi-lo", hab_risk=None, river_discharge_cfs=None):
    return {
        "species": species,
        "tide_state": tide,
        "wind_dir": wind,
        "water_temp_f": temp,
        "moon_phase": moon,
        "bait": bait,
        "rig": rig,
        "hab_risk": hab_risk,
        "river_discharge_cfs": river_discharge_cfs,
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

    def test_top_bait(self):
        catches = [_catch(bait="Live shrimp") for _ in range(4)] + [
            _catch(bait="Cut mullet")
        ]
        out = analyze_catch_patterns(catches)
        assert out["factors"]["top_bait"][0]["bait"] == "Live shrimp"
        assert any("Live shrimp" in i and "productive bait" in i for i in out["insights"])

    def test_top_rig(self):
        catches = [_catch(rig="Fish-finder") for _ in range(4)] + [_catch(rig="Hi-lo")]
        out = analyze_catch_patterns(catches)
        assert out["factors"]["top_rig"][0]["rig"] == "Fish-finder"
        assert any("Fish-finder" in i and "productive rig" in i for i in out["insights"])

    def test_no_bait_no_insight(self):
        catches = [_catch(bait="") for _ in range(5)]
        out = analyze_catch_patterns(catches)
        assert "top_bait" not in out["factors"]

    def test_current_conditions_match_surfaced(self):
        catches = [_catch(tide="Rising", wind="NE") for _ in range(5)]
        out = analyze_catch_patterns(
            catches, current={"tide_state": "Rising", "wind_dir": "SW"}
        )
        assert any("rising tide matches" in m.lower() for m in out["matches"])
        # Wind doesn't match (NE pattern vs SW now), so no wind match.
        assert not any("wind matches" in m.lower() for m in out["matches"])
        # Matches are surfaced at the top of insights.
        assert out["insights"][0] in out["matches"]

    def test_no_match_when_conditions_differ(self):
        catches = [_catch(tide="Rising") for _ in range(5)]
        out = analyze_catch_patterns(catches, current={"tide_state": "Falling"})
        assert out["matches"] == []

    def test_matches_empty_without_current(self):
        out = analyze_catch_patterns([_catch() for _ in range(5)])
        assert out["matches"] == []

    def test_with_conditions_count(self):
        catches = [_catch()] + [
            {"species": "X", "tide_state": None, "wind_dir": None, "moon_phase": None}
        ]
        out = analyze_catch_patterns(catches)
        assert out["with_conditions"] == 1

    def test_hab_events_flagged(self):
        catches = (
            [_catch(hab_risk="danger") for _ in range(2)]
            + [_catch(hab_risk="watch")]
            + [_catch(hab_risk=None) for _ in range(2)]
        )
        out = analyze_catch_patterns(catches)
        assert out["factors"]["hab_events"]["count"] == 3
        assert any("algal bloom advisory" in i.lower() for i in out["insights"])

    def test_no_hab_events_no_flag(self):
        catches = [_catch(hab_risk="low") for _ in range(5)]
        out = analyze_catch_patterns(catches)
        assert "hab_events" not in out["factors"]
        assert not any("algal bloom" in i.lower() for i in out["insights"])

    def test_river_discharge_band(self):
        catches = [_catch(river_discharge_cfs=cfs) for cfs in (80, 100, 120, 140, 160, 180)]
        out = analyze_catch_patterns(catches)
        assert "river_discharge_cfs" in out["factors"]
        band = out["factors"]["river_discharge_cfs"]
        assert band["low"] <= band["high"]
        assert any("cfs" in i for i in out["insights"])

    def test_no_river_discharge_no_band(self):
        catches = [_catch() for _ in range(5)]
        out = analyze_catch_patterns(catches)
        assert "river_discharge_cfs" not in out["factors"]


class TestConditionSnapshotStorage:
    def test_add_and_read_back_conditions(self, app):
        # app fixture initializes an isolated DB.
        uid = create_user("catchuser", "pass1234")
        add_log_entry(
            uid,
            "montauk-ny",
            "Striped bass",
            size="28 in",
            bait="Live eel",
            rig="Fish-finder",
            conditions={
                "tide_state": "Falling",
                "wind_dir": "NW",
                "water_temp_f": 58.5,
                "moon_phase": "Waning Crescent",
                "hab_risk": "watch",
                "river_discharge_cfs": 210.5,
            },
        )
        rows = get_catch_conditions(uid, "montauk-ny")
        assert len(rows) == 1
        assert rows[0]["tide_state"] == "Falling"
        assert rows[0]["wind_dir"] == "NW"
        assert rows[0]["water_temp_f"] == 58.5
        assert rows[0]["moon_phase"] == "Waning Crescent"
        assert rows[0]["bait"] == "Live eel"
        assert rows[0]["rig"] == "Fish-finder"
        assert rows[0]["hab_risk"] == "watch"
        assert rows[0]["river_discharge_cfs"] == 210.5

    def test_missing_conditions_are_null(self, app):
        uid = create_user("catchuser2", "pass1234")
        add_log_entry(uid, "montauk-ny", "Bluefish")
        rows = get_catch_conditions(uid, "montauk-ny")
        assert rows[0]["tide_state"] is None
        assert rows[0]["water_temp_f"] is None
        assert rows[0]["hab_risk"] is None
        assert rows[0]["river_discharge_cfs"] is None


class TestCommunityActivity:
    def _user_with_share(self, idx, share):
        from storage.sqlite import create_user, save_preferences
        uid = create_user(f"comm{idx}", "pass1234")
        save_preferences(uid, fishing_profile={"share_catches": share, "completed": True})
        return uid

    def test_below_threshold_returns_none(self, app):
        from storage.sqlite import add_log_entry, get_recent_catch_activity
        # Two opted-in contributors — below the default min of 3.
        for i in range(2):
            uid = self._user_with_share(i, True)
            add_log_entry(uid, "loc-a", "Red drum")
        assert get_recent_catch_activity("loc-a") is None

    def test_threshold_met_aggregates(self, app):
        from storage.sqlite import add_log_entry, get_recent_catch_activity
        for i in range(3):
            uid = self._user_with_share(i, True)
            add_log_entry(uid, "loc-b", "Red drum")
            add_log_entry(uid, "loc-b", "Bluefish")
        act = get_recent_catch_activity("loc-b")
        assert act is not None
        assert act["contributors"] == 3
        assert act["count"] == 6
        assert act["top_species"][0]["species"] in ("Red drum", "Bluefish")

    def test_opt_out_users_excluded(self, app):
        from storage.sqlite import add_log_entry, get_recent_catch_activity
        # 3 contributors but only 2 opted in → below threshold.
        for i in range(2):
            uid = self._user_with_share(i, True)
            add_log_entry(uid, "loc-c", "Red drum")
        uid_out = self._user_with_share(99, False)
        add_log_entry(uid_out, "loc-c", "Red drum")
        assert get_recent_catch_activity("loc-c") is None
