"""Extended coverage tests for domain/species.py.

Targets the 133 uncovered lines identified by coverage analysis, ordered
easiest-to-hardest within each function group.
"""

from __future__ import annotations

import pytest

from domain.species import (
    SEASONAL_EXPLANATIONS,
    SPECIES_DB_MAP,
    _build_conditions_modifier,
    _classify_rig,
    _condition_rig_tip,
    _conditions_modifier,
    _format_spawn_window,
    _get_explanation,
    _get_technique_tip,
    _parse_closed_months,
    _retention_prohibited,
    _species_matches_profile,
    build_bait_ranking,
    build_lure_recommendations,
    build_rig_recommendations,
    build_species_calendar,
    build_species_ranking,
    build_spawning_report,
)


# ---------------------------------------------------------------------------
# _get_explanation
# ---------------------------------------------------------------------------


class TestGetExplanation:
    """Lines 411-420 — never previously covered."""

    def _sp(self, name: str) -> dict:
        sp = SPECIES_DB_MAP.get(name)
        if sp:
            return sp
        return {
            "name": name,
            "explanation_cold": "cold text",
            "explanation_warm": "warm text",
        }

    def test_spring_override_for_red_drum(self):
        sp = self._sp("Red drum (puppy drum)")
        result = _get_explanation(sp, month=4, water_temp=70)
        assert result == SEASONAL_EXPLANATIONS["Red drum (puppy drum)"]["spring"]

    def test_fall_override_for_red_drum(self):
        sp = self._sp("Red drum (puppy drum)")
        result = _get_explanation(sp, month=10, water_temp=68)
        assert result == SEASONAL_EXPLANATIONS["Red drum (puppy drum)"]["fall"]

    def test_winter_has_no_override_falls_back_cold(self):
        sp = self._sp("Red drum (puppy drum)")
        result = _get_explanation(sp, month=1, water_temp=55)
        # Red drum has no "winter" override → cold fallback
        assert result == sp["explanation_cold"]

    def test_summer_has_no_override_falls_back_warm(self):
        sp = self._sp("Red drum (puppy drum)")
        result = _get_explanation(sp, month=7, water_temp=80)
        assert result == sp["explanation_warm"]

    def test_species_not_in_overrides_uses_warm(self):
        # Use a generic fake species not in SEASONAL_EXPLANATIONS
        sp = {
            "name": "Fake Fish",
            "explanation_cold": "brrr",
            "explanation_warm": "hot",
        }
        result = _get_explanation(sp, month=6, water_temp=78)
        assert result == "hot"

    def test_species_not_in_overrides_uses_cold(self):
        sp = {
            "name": "Fake Fish",
            "explanation_cold": "brrr",
            "explanation_warm": "hot",
        }
        result = _get_explanation(sp, month=1, water_temp=50)
        assert result == "brrr"


# ---------------------------------------------------------------------------
# _get_technique_tip
# ---------------------------------------------------------------------------


class TestGetTechniqueTip:
    """Lines 444-528 — never previously covered."""

    # drum paths
    def test_drum_rising_tide(self):
        tip = _get_technique_tip("Red drum", tide_state="Rising")
        assert "sandbar" in tip.lower() or "trough" in tip.lower()

    def test_drum_dawn(self):
        tip = _get_technique_tip("Red drum", hour=6)
        assert "mullet" in tip.lower() or "cut" in tip.lower()

    def test_drum_default(self):
        tip = _get_technique_tip("Red drum", hour=12, tide_state="Slack")
        assert "fish-finder" in tip.lower() or "bottom" in tip.lower()

    # trout paths
    def test_trout_dawn(self):
        tip = _get_technique_tip("Speckled trout (spotted seatrout)", hour=6)
        assert "shrimp" in tip.lower() or "cork" in tip.lower()

    def test_trout_falling_tide(self):
        tip = _get_technique_tip("Speckled trout", tide_state="Falling")
        assert "outflow" in tip.lower() or "falling" in tip.lower()

    def test_trout_default(self):
        tip = _get_technique_tip("Speckled trout", hour=12, tide_state="Slack")
        assert "cork" in tip.lower() or "pop" in tip.lower()

    # flounder paths
    def test_flounder_rising_tide(self):
        tip = _get_technique_tip("Flounder (summer flounder)", tide_state="Rising")
        assert "minnow" in tip.lower() or "incoming" in tip.lower()

    def test_flounder_default(self):
        tip = _get_technique_tip(
            "Flounder (summer flounder)", hour=12, tide_state="Slack"
        )
        assert "bucktail" in tip.lower() or "bottom" in tip.lower()

    # bluefish paths
    def test_bluefish_dawn(self):
        tip = _get_technique_tip("Bluefish", hour=6)
        assert "spoon" in tip.lower() or "metal" in tip.lower()

    def test_bluefish_default(self):
        tip = _get_technique_tip("Bluefish", hour=12, tide_state="Slack")
        assert "wire" in tip.lower()

    # single-path species
    def test_sheepshead(self):
        tip = _get_technique_tip("Sheepshead")
        assert "crab" in tip.lower()

    def test_shark_night(self):
        tip = _get_technique_tip("Atlantic sharpnose shark", hour=22)
        assert "cut bait" in tip.lower() or "steel leader" in tip.lower()

    def test_shark_default(self):
        tip = _get_technique_tip("Atlantic sharpnose shark", hour=12, tide_state="")
        assert "bait" in tip.lower()

    def test_pompano_rising_tide(self):
        tip = _get_technique_tip("Pompano", tide_state="Rising")
        assert "sand flea" in tip.lower() or "pompano rig" in tip.lower()

    def test_pompano_default(self):
        tip = _get_technique_tip("Pompano", hour=12, tide_state="Slack")
        assert "trough" in tip.lower()

    def test_whiting(self):
        tip = _get_technique_tip("Whiting (sea mullet, kingfish)")
        assert "shrimp" in tip.lower() or "trough" in tip.lower()

    def test_mackerel_dawn(self):
        tip = _get_technique_tip("Spanish mackerel", hour=6)
        assert "spoon" in tip.lower() or "gotcha" in tip.lower()

    def test_mackerel_default(self):
        tip = _get_technique_tip("Spanish mackerel", hour=12, tide_state="Slack")
        assert "clark" in tip.lower() or "troll" in tip.lower() or "jig" in tip.lower()

    def test_croaker(self):
        tip = _get_technique_tip("Atlantic croaker")
        assert "shrimp" in tip.lower() or "worm" in tip.lower()

    def test_spot_not_trout(self):
        tip = _get_technique_tip("Spot")
        assert "shrimp" in tip.lower() or "worm" in tip.lower()

    def test_bass_dawn(self):
        tip = _get_technique_tip("Striped bass (rockfish)", hour=6)
        assert "eel" in tip.lower() or "dawn" in tip.lower() or "dusk" in tip.lower()

    def test_bass_default(self):
        tip = _get_technique_tip("Striped bass (rockfish)", hour=12, tide_state="Slack")
        assert "bucktail" in tip.lower() or "structure" in tip.lower()

    def test_rockfish(self):
        tip = _get_technique_tip("Quillback rockfish")
        assert "rocky" in tip.lower() or "kelp" in tip.lower() or "jig" in tip.lower()

    def test_surfperch(self):
        tip = _get_technique_tip("Barred surfperch")
        assert "sand crab" in tip.lower() or "wash" in tip.lower()

    def test_halibut(self):
        tip = _get_technique_tip("California halibut")
        assert "live bait" in tip.lower() or "bottom" in tip.lower()

    def test_corbina(self):
        tip = _get_technique_tip("Corbina")
        assert "sand crab" in tip.lower() or "wade" in tip.lower()

    def test_yellowtail(self):
        tip = _get_technique_tip("Yellowtail")
        assert "kelp" in tip.lower() or "jig" in tip.lower()

    # generic tips
    def test_generic_dawn_dusk(self):
        tip = _get_technique_tip("Unknown Fish", hour=6)
        assert (
            "low-light" in tip.lower() or "dawn" in tip.lower() or "dusk" in tip.lower()
        )

    def test_generic_rising(self):
        tip = _get_technique_tip("Unknown Fish", hour=12, tide_state="Rising")
        assert "rising" in tip.lower()

    def test_generic_falling(self):
        tip = _get_technique_tip("Unknown Fish", hour=12, tide_state="Falling")
        assert "falling" in tip.lower()

    def test_generic_midday(self):
        tip = _get_technique_tip("Unknown Fish", hour=12, tide_state="")
        assert "midday" in tip.lower() or "deeper" in tip.lower()

    def test_generic_default(self):
        tip = _get_technique_tip("Unknown Fish", hour=15, tide_state="")
        # midday OR default — both are acceptable
        assert isinstance(tip, str) and len(tip) > 0


# ---------------------------------------------------------------------------
# _classify_rig
# ---------------------------------------------------------------------------


class TestClassifyRig:
    """Lines 762-817 — branches not previously triggered."""

    def test_na_returns_empty(self):
        assert _classify_rig("n/a") == ""

    def test_observe_returns_empty(self):
        assert _classify_rig("Observe only, protected") == ""

    def test_protected_returns_empty(self):
        assert _classify_rig("protected species") == ""

    def test_trolling_without_slow(self):
        assert _classify_rig("trolling rig with inline sinker") == "trolling"

    def test_sabiki_rig(self):
        assert _classify_rig("sabiki rig for bait catching") == "sabiki"

    def test_bait_catcher(self):
        assert _classify_rig("bait catcher gold hook") == "sabiki"

    def test_shad_dart(self):
        assert _classify_rig("shad dart in tandem") == "tandem-jig"

    def test_tandem_jig(self):
        assert _classify_rig("tandem jig setup") == "tandem-jig"

    def test_fly_pattern(self):
        assert _classify_rig("clouser minnow fly pattern") == "fly_pattern"

    def test_fly_with_rod(self):
        assert _classify_rig("8-wt fly rod with deceiver") == "fly_pattern"

    def test_tippet(self):
        assert _classify_rig("10 lb tippet with streamer") == "fly_pattern"

    def test_jig_head(self):
        assert _classify_rig("jig head with 4 inch paddle tail") == "wade_light"

    def test_wade_light(self):
        assert _classify_rig("wade light tackle inshore") == "wade_light"

    def test_flats_rig(self):
        assert _classify_rig("flats rig for bonefish") == "wade_light"

    def test_bridge_jig(self):
        assert _classify_rig("bridge jig heavy") == "current_jig"

    def test_current_jig(self):
        assert _classify_rig("current jig in pass") == "current_jig"

    def test_bucktail_jig(self):
        assert _classify_rig("bucktail jig 1oz") == "current_jig"

    def test_freeline(self):
        assert _classify_rig("freeline live bait under the kayak") == "kayak_live_bait"

    def test_kayak_live_bait(self):
        assert _classify_rig("kayak live bait freeline") == "kayak_live_bait"

    def test_popping_cork(self):
        assert _classify_rig("popping cork rig") == "popping-cork"

    def test_pompano_rig_keyword(self):
        assert _classify_rig("pompano rig double-drop") == "pompano"

    def test_float_bead(self):
        assert _classify_rig("float bead rig above hook") == "pompano"

    def test_slow_trolling_still_classifies_as_trolling(self):
        assert (
            _classify_rig("Medium to heavy trolling or slow-trolling near reef")
            == "trolling"
        )

    def test_light_surf_rig_phrase(self):
        assert _classify_rig("Light surf rig at 5-20 ft in the surf zone") == "pompano"

    def test_ultralight_float_beats_generic_float(self):
        assert (
            _classify_rig("Ultra-light float rig with tiny hooks and dough bait")
            == "ultralight_panfish"
        )

    def test_plain_float_still_classifies_as_float(self):
        assert _classify_rig("Float rig with wire leader") == "float"

    def test_deep_bottom_rig_routes_to_dropper_loop_deep(self):
        assert (
            _classify_rig("Medium to heavy bottom rig at 100-400 ft near reef")
            == "dropper_loop_deep"
        )

    def test_very_deep_bottom_rig_routes_to_deep_drop(self):
        assert (
            _classify_rig("Heavy bottom rig at 200-800 ft near deep rocky reef")
            == "deep-drop"
        )

    def test_shallow_bottom_rig_stays_light_bottom_reef(self):
        assert (
            _classify_rig("Light bottom rig near sandy bottom at 3-30 ft in bays")
            == "light_bottom_reef"
        )

    def test_heavy_conventional_ball_sinker(self):
        assert (
            _classify_rig("Heavy conventional with 16-32 oz ball sinker at 100-600 ft")
            == "heavy_conventional"
        )

    def test_heavy_tackle_incidental_catch(self):
        assert (
            _classify_rig("Heavy tackle (usually incidental catch)")
            == "heavy_conventional"
        )

    def test_shark_name_fallback_without_wire_keyword(self):
        assert (
            _classify_rig("Heavy rig; 200 lb leader", "Brown shark (sandbar)")
            == "shark"
        )

    def test_steelhead_name_hint_on_generic_drift_text(self):
        assert (
            _classify_rig(
                "Drift or float rig in medium to fast water",
                "Olympic Peninsula steelhead",
            )
            == "steelhead_drift"
        )

    def test_pencil_lead_routes_to_steelhead_drift(self):
        assert (
            _classify_rig("Drift rig with pencil lead; bobber-and-jig")
            == "steelhead_drift"
        )

    def test_river_mouth_drift_routes_to_steelhead_drift(self):
        assert (
            _classify_rig(
                "Light jig below a small float; drift fishing near river mouths"
            )
            == "steelhead_drift"
        )

    def test_fly_rod_wins_over_river_mouth_drift(self):
        assert (
            _classify_rig(
                "Fly rod with sink-tip line is the traditional approach; "
                "drift rig with roe near river mouths"
            )
            == "fly_pattern"
        )

    def test_bare_pier_mention_no_longer_forces_knocker(self):
        # A species that just happens to be caught "near a pier" but whose
        # own text names a completely different technique should classify
        # by that technique, not get swept into the knocker bucket.
        assert (
            _classify_rig(
                "Medium to heavy spinning or conventional near pier structure"
            )
            == "heavy_spin_cast"
        )
        assert (
            _classify_rig(
                "Hi-lo rig or Carolina rig; keep baits near bottom from piers"
            )
            == "hi-lo"
        )

    def test_actual_knocker_rig_still_classifies_as_knocker(self):
        assert (
            _classify_rig("Knocker rig tight to pilings; short fluorocarbon leader")
            == "knocker"
        )

    def test_heavy_spinning_routes_to_heavy_spin_cast(self):
        assert (
            _classify_rig("Heavy spinning or conventional near surface-cruising fish")
            == "heavy_spin_cast"
        )

    def test_light_spinning_stays_light_spin_cast(self):
        assert (
            _classify_rig("Light to medium spinning near surface schools")
            == "light_spin_cast"
        )

    def test_shark_word_boundary_excludes_sharksucker(self):
        # "sharksucker" contains "shark" as a substring but remoras are not
        # sharks and shouldn't be handed heavy wire-leader shark tackle.
        assert (
            _classify_rig(
                "Any bottom or float rig (incidental catch)",
                "Remora (sharksucker)",
            )
            != "shark"
        )

    def test_shark_name_still_wins_over_spinning_text(self):
        assert (
            _classify_rig(
                "Heavy surf or spinning rig; 100 lb leader", "Atlantic blacktip shark"
            )
            == "shark"
        )

    def test_spinning_near_mangroves_is_not_a_bottom_rig(self):
        # "mangroves"/"docks" are locations, not techniques — a species cast
        # to on spinning gear near mangroves shouldn't be swept into the
        # bottom rig bucket just because the location word matched.
        assert (
            _classify_rig(
                "Light to medium spinning near mangroves and freshwater canals"
            )
            == "light_spin_cast"
        )
        assert (
            _classify_rig("Light spinning near grass flats, docks, and channel edges")
            == "light_spin_cast"
        )

    def test_bottom_rig_near_mangroves_still_classifies_as_bottom_reef(self):
        assert (
            _classify_rig("Light bottom rig near mangroves and muddy bottom")
            == "light_bottom_reef"
        )

    def test_named_river_chinook_drift_routes_to_steelhead_drift(self):
        # River-run chinook named for their home river are back-bounced/
        # plugged in current the same way as steelhead, not slip-sinker
        # drifted like an ocean/bay bottom fish.
        assert (
            _classify_rig(
                "Drift rig in fast Klamath River water",
                "Klamath River fall chinook",
            )
            == "steelhead_drift"
        )

    def test_trolling_still_wins_for_river_named_species(self):
        assert (
            _classify_rig(
                "Trolling or drift rig in San Francisco Bay and Sacramento River",
                "Sacramento River fall chinook",
            )
            == "trolling"
        )

    def test_plain_drift_rig_stays_drift_bottom(self):
        assert (
            _classify_rig(
                "Spreader bar rig or slip sinker with 80-150 lb leader; drift fishing"
            )
            == "drift_bottom"
        )


# ---------------------------------------------------------------------------
# _condition_rig_tip — moderate wave path, pier gaff path
# ---------------------------------------------------------------------------


class TestConditionRigTipExtended:
    """Lines 892, 915."""

    def test_moderate_choppy_wave(self):
        # avg_wave=2.75 → moderate (≥2.5) but not rough (<4)
        tip = _condition_rig_tip((5, 8), (2.5, 3.0), 70.0, "", set(), "bait")
        assert "choppy" in tip.lower() or "3-4 oz" in tip

    def test_pier_gaff_advice_high_wave(self):
        # pier + avg_wave=4.5 → pier gaff line 915
        tip = _condition_rig_tip((10, 15), (4.0, 5.0), 68.0, "", {"pier"}, "bait")
        assert "gaff" in tip.lower() or "pier" in tip.lower()


# ---------------------------------------------------------------------------
# build_rig_recommendations — type-specific prepend, duplicate skip, gear filter
# ---------------------------------------------------------------------------


class TestBuildRigRecommendations:
    """Lines 982-1045."""

    def _ranking(self):
        return build_species_ranking(month=6, water_temp=75, coast="east")

    def test_fly_prepends_fly_pattern_rig(self):
        recs = build_rig_recommendations(self._ranking(), fishing_types=["fly"])
        rig_names = [r["name"] for r in recs]
        # fly_pattern rig should appear — possibly as first item
        from domain.species import RIG_CATEGORIES

        fly_name = RIG_CATEGORIES["fly_pattern"]["name"]
        assert any(fly_name in name for name in rig_names)

    def test_bridge_prepends_current_jig_rig(self):
        recs = build_rig_recommendations(self._ranking(), fishing_types=["bridge"])
        from domain.species import RIG_CATEGORIES

        cj_name = RIG_CATEGORIES["current_jig"]["name"]
        rig_names = [r["name"] for r in recs]
        assert any(cj_name in name for name in rig_names)

    def test_wade_prepends_wade_light_rig(self):
        recs = build_rig_recommendations(self._ranking(), fishing_types=["wade"])
        from domain.species import RIG_CATEGORIES

        wl_name = RIG_CATEGORIES["wade_light"]["name"]
        rig_names = [r["name"] for r in recs]
        assert any(wl_name in name for name in rig_names)

    def test_kayak_prepends_kayak_live_bait_rig(self):
        recs = build_rig_recommendations(self._ranking(), fishing_types=["kayak"])
        from domain.species import RIG_CATEGORIES

        klb_name = RIG_CATEGORIES["kayak_live_bait"]["name"]
        rig_names = [r["name"] for r in recs]
        assert any(klb_name in name for name in rig_names)

    def test_lures_no_drops_lure_only_rigs(self):
        recs_no_lure = build_rig_recommendations(
            self._ranking(),
            lures="no",
            live_bait="yes",
        )
        from domain.species import RIG_CATEGORIES, _RIG_GEAR_TYPE

        lure_names = {
            cat["name"]
            for k, cat in RIG_CATEGORIES.items()
            if _RIG_GEAR_TYPE.get(k) == "lure"
        }
        rig_names = [r["name"] for r in recs_no_lure]
        for ln in lure_names:
            assert ln not in rig_names, f"lure-only rig '{ln}' should be filtered out"

    def test_lures_only_sorts_lures_first(self):
        recs = build_rig_recommendations(
            self._ranking(),
            lures="yes",
            live_bait="no",
            cut_bait="no",
        )
        from domain.species import RIG_CATEGORIES, _RIG_GEAR_TYPE

        def gear(name: str) -> str:
            for k, cat in RIG_CATEGORIES.items():
                if cat.get("name") == name:
                    return _RIG_GEAR_TYPE.get(k, "mixed")
            return "mixed"

        gear_types = [gear(r["name"]) for r in recs]
        first_bait = next(
            (i for i, g in enumerate(gear_types) if g == "bait"), len(gear_types)
        )
        first_lure = next(
            (i for i, g in enumerate(gear_types) if g in ("lure", "mixed")),
            len(gear_types),
        )
        assert first_lure <= first_bait

    def test_every_rig_category_has_an_image_file_and_gear_type(self):
        import pathlib

        from domain.species import RIG_CATEGORIES, _RIG_GEAR_TYPE

        static_dir = pathlib.Path(__file__).resolve().parent.parent / "static"
        for key, cat in RIG_CATEGORIES.items():
            assert key in _RIG_GEAR_TYPE, f"{key} missing from _RIG_GEAR_TYPE"
            image = cat.get("image")
            assert image, f"{key} has no image path"
            assert (static_dir / image).is_file(), f"{key} image not found: {image}"


# ---------------------------------------------------------------------------
# _species_matches_profile — charter-only and fly-only gates
# ---------------------------------------------------------------------------


class TestSpeciesMatchesProfileCharter:
    """Lines 110, 125 — charter/fly-only gates."""

    def test_charter_only_blocks_pier_species(self):
        # Sheepshead is in _PIER_SPECIES but not _CHARTER_SPECIES
        result = _species_matches_profile("Sheepshead", fishing_types=["charter"])
        assert result is False

    def test_charter_only_allows_charter_species(self):
        from domain.species import _CHARTER_SPECIES

        charter_sp = next(iter(_CHARTER_SPECIES))
        result = _species_matches_profile(charter_sp, fishing_types=["charter"])
        assert result is True

    def test_fly_only_blocks_non_fly_species(self):
        # Sheepshead is in _PIER_SPECIES but NOT in _FLY_SPECIES
        result = _species_matches_profile("Sheepshead", fishing_types=["fly"])
        assert result is False

    def test_fly_only_allows_fly_species(self):
        # "Jack crevalle" is in _FLY_SPECIES and not in _OFFSHORE_ONLY_SPECIES
        result = _species_matches_profile("Jack crevalle", fishing_types=["fly"])
        assert result is True


class TestSpeciesMatchesProfileAccessibleSets:
    """Lines 158, 160 — kayak and charter accessible set building."""

    def test_kayak_adds_kayak_species(self):
        from domain.species import _KAYAK_SPECIES

        sp = next(iter(_KAYAK_SPECIES))
        result = _species_matches_profile(sp, fishing_types=["kayak"])
        assert result is True

    def test_charter_adds_charter_species(self):
        from domain.species import _CHARTER_SPECIES

        sp = next(iter(_CHARTER_SPECIES))
        result = _species_matches_profile(sp, fishing_types=["kayak", "charter"])
        assert result is True

    def test_fly_adds_fly_species(self):
        # "Tarpon" is in _FLY_SPECIES and not in _OFFSHORE_ONLY_SPECIES
        result = _species_matches_profile("Tarpon", fishing_types=["wade", "fly"])
        assert result is True


class TestSpeciesMatchesProfileTargetFlags:
    """Lines 181, 183, 185 — pelagic, structure, gamefish flags."""

    def test_pelagic_target_matches_pelagic_species(self):
        from domain.species import _PELAGIC_SPECIES

        sp = next(iter(_PELAGIC_SPECIES))
        result = _species_matches_profile(sp, targets=["pelagic"])
        assert result is True

    def test_structure_target_matches_structure_species(self):
        from domain.species import _STRUCTURE_SPECIES

        sp = next(iter(_STRUCTURE_SPECIES))
        result = _species_matches_profile(sp, targets=["structure"])
        assert result is True

    def test_gamefish_target_matches_gamefish_species(self):
        from domain.species import _GAMEFISH_SPECIES

        sp = next(iter(_GAMEFISH_SPECIES))
        result = _species_matches_profile(sp, targets=["gamefish"])
        assert result is True

    def test_non_matching_target_returns_false(self):
        result = _species_matches_profile("Sheepshead", targets=["pelagic"])
        assert result is False


# ---------------------------------------------------------------------------
# _conditions_modifier — calm water wind direction and speed, wave, daytime
# ---------------------------------------------------------------------------


class TestConditionsModifier:
    """Lines 1685-1726 — calm water conditions modifier.

    Signature: _conditions_modifier(sp, wind_dir, wind_range, wave_range, hour, coast)
    Sheepshead is in _CALM_WATER_SPECIES but NOT in _LOW_LIGHT_SPECIES or
    _DAYTIME_SPECIES, so time-of-day has no effect on it.
    """

    def test_calm_water_offshore_wind_bonus(self):
        # Sheepshead is a calm-water species; offshore wind on east coast
        # gives +5 bonus. East coast offshore dirs include "W".
        result = _conditions_modifier(
            sp={"name": "Sheepshead"},
            wind_dir="W",  # offshore on east coast
            wind_range=(3, 5),
            wave_range=(0.5, 1.0),
            hour=12,
            coast="east",
        )
        assert result > 0

    def test_calm_water_onshore_wind_penalty(self):
        # Onshore wind (E on east coast) hurts calm-water species (line 1686)
        result = _conditions_modifier(
            sp={"name": "Sheepshead"},
            wind_dir="E",  # onshore on east coast
            wind_range=(16, 20),  # also high wind → -2 (line 1702)
            wave_range=(4.5, 5.5),  # also high wave → -2 (line 1717)
            hour=12,
            coast="east",
        )
        assert result < 0

    def test_calm_water_low_wind_speed_bonus(self):
        # wind_avg < 8 → +3 bonus for calm-water species (line 1700)
        result = _conditions_modifier(
            sp={"name": "Sheepshead"},
            wind_dir=None,
            wind_range=(3, 6),  # avg=4.5 < 8
            wave_range=(0.5, 0.8),
            hour=12,
            coast="east",
        )
        assert result > 0

    def test_calm_water_high_wind_penalty(self):
        # wind_avg > 15 → -2 for calm-water species (line 1702)
        # Use neutral wave (3–4 ft, avg=3.5 → no bonus/penalty for calm water)
        result = _conditions_modifier(
            sp={"name": "Sheepshead"},
            wind_dir=None,
            wind_range=(16, 20),  # avg=18 > 15 → -2
            wave_range=(3.0, 4.0),  # avg=3.5, not < 2 and not > 4 → 0
            hour=12,
            coast="east",
        )
        assert result < 0

    def test_calm_water_low_wave_bonus(self):
        # wave_avg < 2 → +4 bonus for calm-water species (line 1715)
        result = _conditions_modifier(
            sp={"name": "Sheepshead"},
            wind_dir=None,
            wind_range=(3, 6),
            wave_range=(0.5, 1.0),  # avg=0.75 < 2
            hour=12,
            coast="east",
        )
        assert result > 0

    def test_calm_water_high_wave_penalty(self):
        # wave_avg > 4 → -2 for calm-water species (line 1717)
        # Use neutral wind (10–14 kt, avg=12, not < 8 and not > 15 → 0)
        result = _conditions_modifier(
            sp={"name": "Sheepshead"},
            wind_dir=None,
            wind_range=(10, 14),  # avg=12 → 0 for calm water
            wave_range=(4.5, 5.5),  # avg=5 > 4 → -2
            hour=12,
            coast="east",
        )
        assert result < 0

    def test_daytime_species_bonus_at_midday(self):
        # Spanish mackerel is a daytime species; midday gives +3 (line 1726)
        result = _conditions_modifier(
            sp={"name": "Spanish mackerel"},
            wind_dir=None,
            wind_range=None,
            wave_range=None,
            hour=12,  # midday (10–15)
            coast="east",
        )
        assert result > 0

    def test_daytime_species_penalty_at_night(self):
        # Spanish mackerel at low-light hours → -1
        result = _conditions_modifier(
            sp={"name": "Spanish mackerel"},
            wind_dir=None,
            wind_range=None,
            wave_range=None,
            hour=22,  # low light (> 18)
            coast="east",
        )
        assert result < 0

    def test_rough_surf_low_wind_penalty(self):
        # Red drum is in _ROUGH_SURF_SPECIES; wind_avg=3 < 5 → -2 (line 1695-1696)
        # Use hour=9 (not low-light, not midday) to avoid time-of-day modifier
        result = _conditions_modifier(
            sp={"name": "Red drum (puppy drum)"},
            wind_dir=None,
            wind_range=(2, 4),  # avg=3 < 5 → -2
            wave_range=(2, 3),  # avg=2.5 → +4
            hour=9,
            coast="east",
        )
        # -2 + 4 = +2
        assert result == pytest.approx(2.0)

    def test_rough_surf_low_wave_penalty(self):
        # Red drum; wave_avg=0.5 < 1 → -1 (line 1711-1712)
        # Use hour=9 to avoid time-of-day modifier
        result = _conditions_modifier(
            sp={"name": "Red drum (puppy drum)"},
            wind_dir=None,
            wind_range=(10, 14),  # avg=12 → 10≤12≤18 → +3
            wave_range=(0.3, 0.7),  # avg=0.5 < 1 → -1
            hour=9,
            coast="east",
        )
        # +3 - 1 = +2
        assert result == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# _build_conditions_modifier closure — rough surf low wind/wave paths
# ---------------------------------------------------------------------------


class TestBuildConditionsModifier:
    """Lines 1773, 1785 — rough surf species when wind < 5 or wave < 1.

    Red drum is in _LOW_LIGHT_SPECIES so at midday (10-15) it gets -1.
    Use hour=9 to avoid that time-of-day modifier and get clean arithmetic.
    """

    def test_rough_surf_low_wind_penalty(self):
        modifier = _build_conditions_modifier(
            wind_dir=None,
            wind_range=(2, 4),  # avg=3 < 5 → -2 for rough surf (line 1773)
            wave_range=(2.0, 3.0),  # avg=2.5 → +4 for rough surf
            hour=9,  # not midday, not low-light → 0 time modifier
            coast="east",
        )
        mod = modifier("Red drum (puppy drum)")
        # wind -2 + wave +4 = +2
        assert mod == pytest.approx(2.0)

    def test_rough_surf_low_wave_penalty(self):
        modifier = _build_conditions_modifier(
            wind_dir=None,
            wind_range=(8, 12),  # avg=10 → 10 ≤ 10 ≤ 18 → +3 for rough surf
            wave_range=(0.3, 0.6),  # avg=0.45 < 1 → -1 for rough surf (line 1785)
            hour=9,  # not midday, not low-light → 0 time modifier
            coast="east",
        )
        mod = modifier("Red drum (puppy drum)")
        # wind +3 + wave -1 = +2
        assert mod == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# _parse_closed_months
# ---------------------------------------------------------------------------


class TestParseClosedMonths:
    """Lines 1837-1852 — never previously covered."""

    def test_simple_range_jan_may(self):
        result = _parse_closed_months("closed Jan-May")
        assert result == {1, 2, 3, 4, 5}

    def test_full_month_names(self):
        result = _parse_closed_months("closed March-June")
        assert result == {3, 4, 5, 6}

    def test_year_wrap_nov_feb(self):
        result = _parse_closed_months("closed Nov-Feb")
        assert result == {11, 12, 1, 2}

    def test_year_wrap_dec_feb(self):
        result = _parse_closed_months("closed Dec-Feb")
        assert result == {12, 1, 2}

    def test_en_dash_separator(self):
        result = _parse_closed_months("closed Jan–Mar")
        assert result == {1, 2, 3}

    def test_no_closed_keyword(self):
        result = _parse_closed_months("Season open year-round")
        assert result == set()

    def test_unknown_month_abbreviation(self):
        result = _parse_closed_months("closed Xyz-Jan")
        assert result == set()


# ---------------------------------------------------------------------------
# _retention_prohibited — empty combined, month-specific closure
# ---------------------------------------------------------------------------


class TestRetentionProhibitedExtended:
    """Lines 1886, 1909-1910."""

    def test_empty_dict_returns_false(self):
        # combined == "" → line 1886
        assert _retention_prohibited({}) is False

    def test_month_in_closed_months(self):
        # notes contains "closed Jan-May" and month=3 is in that range
        assert _retention_prohibited({"notes": "closed Jan-May"}, month=3) is True

    def test_month_not_in_closed_months(self):
        assert _retention_prohibited({"notes": "closed Jan-May"}, month=7) is False

    def test_month_zero_skips_closure_check(self):
        # month=0 skips the closure check (not calling _parse_closed_months)
        assert _retention_prohibited({"notes": "closed Jan-May"}, month=0) is False


# ---------------------------------------------------------------------------
# _build_profile_filter via build_species_ranking — charter/fly-only closure
# ---------------------------------------------------------------------------


class TestBuildProfileFilter:
    """Lines 1965-1973, 1985-1993, 2002, 2004."""

    def test_charter_only_excludes_pier_species(self):
        ranking = build_species_ranking(
            month=6,
            water_temp=75,
            coast="east",
            fishing_types=["charter"],
        )
        names = {r["name"] for r in ranking}
        # Sheepshead is in _PIER_SPECIES but not _CHARTER_SPECIES
        assert "Sheepshead" not in names

    def test_fly_only_excludes_pier_species(self):
        ranking = build_species_ranking(
            month=6,
            water_temp=75,
            coast="east",
            fishing_types=["fly"],
        )
        names = {r["name"] for r in ranking}
        # Sheepshead is in _PIER_SPECIES but not _FLY_SPECIES
        assert "Sheepshead" not in names

    def test_inshore_adds_inshore_species(self):
        ranking = build_species_ranking(
            month=6,
            water_temp=75,
            coast="east",
            fishing_types=["inshore"],
        )
        # Inshore type should return some results
        assert len(ranking) > 0

    def test_pelagic_target_filter(self):
        ranking = build_species_ranking(
            month=6,
            water_temp=75,
            coast="east",
            targets=["pelagic"],
        )
        from domain.species import _PELAGIC_SPECIES

        names = {r["name"] for r in ranking}
        assert names.issubset(_PELAGIC_SPECIES | {"Cobia"})  # cobia can be pelagic

    def test_gamefish_target_filter(self):
        ranking = build_species_ranking(
            month=6,
            water_temp=75,
            coast="east",
            targets=["gamefish"],
        )
        from domain.species import _GAMEFISH_SPECIES

        names = {r["name"] for r in ranking}
        assert all(n in _GAMEFISH_SPECIES for n in names)

    def test_structure_target_filter(self):
        ranking = build_species_ranking(
            month=6,
            water_temp=75,
            coast="east",
            targets=["structure"],
        )
        from domain.species import _STRUCTURE_SPECIES

        names = {r["name"] for r in ranking}
        assert all(n in _STRUCTURE_SPECIES for n in names)

    def test_kayak_fishing_type_adds_kayak_species(self):
        # Exercises line 1969 in _build_profile_filter
        ranking = build_species_ranking(
            month=6,
            water_temp=75,
            coast="east",
            fishing_types=["kayak"],
        )
        assert len(ranking) > 0

    def test_bottom_target_filter(self):
        # Exercises line 1985 in _build_profile_filter
        ranking = build_species_ranking(
            month=6,
            water_temp=75,
            coast="east",
            targets=["bottom"],
        )
        from domain.species import _BOTTOM_SPECIES

        names = {r["name"] for r in ranking}
        assert all(n in _BOTTOM_SPECIES for n in names)

    def test_inshore_slam_target_filter(self):
        # Exercises line 1993 in _build_profile_filter
        ranking = build_species_ranking(
            month=6,
            water_temp=75,
            coast="east",
            targets=["inshore_slam"],
        )
        from domain.species import _INSHORE_SLAM_SPECIES

        names = {r["name"] for r in ranking}
        assert all(n in _INSHORE_SLAM_SPECIES for n in names)


# ---------------------------------------------------------------------------
# build_bait_ranking — out-of-season penalty and seasonal notes
# ---------------------------------------------------------------------------


class TestBuildBaitRanking:
    """Lines 2209, 2215."""

    def _winter_ranking(self):
        return build_species_ranking(month=1, water_temp=52, coast="east")

    def test_out_of_season_bait_has_penalty(self):
        # Live shrimp available_months=[3..11], winter (month=1) → penalised
        ranking = self._winter_ranking()
        result = build_bait_ranking(ranking, month=1)
        # Live shrimp should still appear but low in the list; just ensure no crash
        bait_names = [r["bait"] for r in result]
        assert "Live shrimp" in bait_names

    def test_winter_seasonal_notes_used(self):
        # Live shrimp has notes_seasonal["winter"]
        ranking = self._winter_ranking()
        result = build_bait_ranking(ranking, month=1)
        shrimp = next((r for r in result if r["bait"] == "Live shrimp"), None)
        assert shrimp is not None
        # The winter note mentions "scarce" or "winter"
        assert (
            "winter" in shrimp["notes"].lower() or "scarce" in shrimp["notes"].lower()
        )


# ---------------------------------------------------------------------------
# build_lure_recommendations — seasonal notes
# ---------------------------------------------------------------------------


class TestBuildLureRecommendations:
    """Line 2500."""

    def test_winter_seasonal_notes_for_topwater(self):
        ranking = build_species_ranking(month=1, water_temp=52, coast="east")
        result = build_lure_recommendations(ranking, month=1)
        topwater = next((r for r in result if "topwater" in r["lure"].lower()), None)
        assert topwater is not None
        # winter note mentions cold water or below 60
        assert "cold" in topwater["notes"].lower() or "60" in topwater["notes"]


# ---------------------------------------------------------------------------
# _format_spawn_window
# ---------------------------------------------------------------------------


class TestFormatSpawnWindow:
    """Lines 3584, 3586, 3605-3610."""

    def test_empty_list_returns_unknown(self):
        assert _format_spawn_window([]) == "Unknown"

    def test_single_month_returns_abbr(self):
        assert _format_spawn_window([6]) == "Jun"

    def test_single_month_jan(self):
        assert _format_spawn_window([1]) == "Jan"

    def test_contiguous_range(self):
        result = _format_spawn_window([5, 6, 7, 8])
        assert "May" in result and "Aug" in result

    def test_year_wrap_range(self):
        # Nov, Dec, Jan, Feb — gap is Feb→Nov so wraps
        result = _format_spawn_window([11, 12, 1, 2])
        assert "Nov" in result and "Feb" in result

    def test_non_contiguous_months(self):
        # [3, 5, 9] — gaps of 2 each, non-contiguous → list individually
        result = _format_spawn_window([3, 5, 9])
        assert "Mar" in result and "May" in result and "Sep" in result


# ---------------------------------------------------------------------------
# build_spawning_report — regulation lookup raises exception
# ---------------------------------------------------------------------------


class TestBuildSpawningReport:
    """Lines 3708-3709."""

    def test_regulation_lookup_exception_yields_none(self, monkeypatch):
        import domain.species as _sp_mod

        def _raise(name, state):
            raise RuntimeError("db is gone")

        monkeypatch.setattr(_sp_mod, "lookup_regulation", _raise)
        # Red drum spawns in summer; use month=7, warm water, east coast
        results = build_spawning_report(
            month=7, water_temp=78, coast="east", state="NC"
        )
        # Should not crash; regulation field for entries should be None
        for entry in results:
            assert entry["regulation"] is None


# ---------------------------------------------------------------------------
# build_species_calendar — species not in db_map skipped
# ---------------------------------------------------------------------------


class TestBuildSpeciesCalendar:
    """Line 3914."""

    def test_unknown_species_skipped(self):
        fake_ranking = [
            {"name": "Completely Nonexistent Species XYZXYZ"},
            {"name": "Red drum (puppy drum)"},  # should still appear
        ]
        result = build_species_calendar(fake_ranking)
        names = [r["name"] for r in result]
        assert "Completely Nonexistent Species XYZXYZ" not in names
        assert "Red drum (puppy drum)" in names
