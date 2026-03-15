"""Tests for domain.species scoring and ranking."""

from domain.species import (
    BAIT_DB,
    SPECIES_DB,
    _SPECIES_CATEGORIES,
    _retention_prohibited,
    _score_species,
    _species_matches_profile,
    build_bait_ranking,
    build_natural_bait_chart,
    build_species_calendar,
    build_species_ranking,
    build_spawning_report,
)


# Grab a known species entry for testing
def _get_species(name_prefix: str):
    for sp in SPECIES_DB:
        if sp["name"].startswith(name_prefix):
            return sp
    raise ValueError(f"Species starting with '{name_prefix}' not found")


class TestScoreSpecies:
    def test_ideal_temp_peak_month(self):
        """Species in ideal temp range during peak month should score high."""
        red_drum = _get_species("Red drum")
        # 65 F is in ideal range (55-75), month 10 is peak
        score = _score_species(red_drum, month=10, water_temp=65)
        assert score >= 75  # 50 (temp) + 30 (peak) - some conditions

    def test_outside_survivable_range_returns_negative(self):
        """Species outside survivable temp range should score -100."""
        red_drum = _get_species("Red drum")
        # 30 F is below temp_min=45
        score = _score_species(red_drum, month=10, water_temp=30)
        assert score == -100.0

    def test_off_month_scores_lower(self):
        """Species in an off month (not peak or good) should score lower."""
        red_drum = _get_species("Red drum")
        # Month 7 is good_months, month 10 is peak_months
        score_peak = _score_species(red_drum, month=10, water_temp=65)
        score_good = _score_species(red_drum, month=7, water_temp=65)
        assert score_peak > score_good

    def test_cold_water_species_in_winter(self):
        """Cold water species (tautog) should score well in winter."""
        tautog = _get_species("Tautog")
        # 55 F, January (peak month)
        score = _score_species(tautog, month=1, water_temp=55)
        assert score >= 70

    def test_score_with_all_params(self):
        """Score function should accept all optional params without error."""
        red_drum = _get_species("Red drum")
        score = _score_species(
            red_drum,
            month=5,
            water_temp=68,
            wind_dir="SW",
            wind_range=(8, 12),
            wave_range=(2, 3),
            hour=6,
            coast="east",
        )
        assert isinstance(score, float)


class TestSpeciesMatchesProfile:
    def test_no_profile_matches_all(self):
        sp = _get_species("Red drum")
        assert _species_matches_profile(sp, None) is True

    def test_empty_profile_matches_all(self):
        sp = _get_species("Red drum")
        assert _species_matches_profile(sp, {}) is True


class TestBuildNaturalBaitChart:
    def test_returns_list(self):
        chart = build_natural_bait_chart(month=6, coast="east")
        assert isinstance(chart, list)
        assert len(chart) > 0

    def test_entries_have_required_keys(self):
        chart = build_natural_bait_chart(month=6, coast="east")
        for entry in chart:
            assert "name" in entry
            assert "note" in entry
            assert "status" in entry
            assert entry["status"] in ("available", "off-season")

    def test_available_sorted_first(self):
        chart = build_natural_bait_chart(month=6, coast="east")
        # Find first off-season entry
        first_off = None
        for i, entry in enumerate(chart):
            if entry["status"] == "off-season":
                first_off = i
                break
        if first_off is not None:
            # All entries before it should be available
            for entry in chart[:first_off]:
                assert entry["status"] == "available"

    def test_west_coast_has_results(self):
        chart = build_natural_bait_chart(month=6, coast="west")
        assert len(chart) > 0


class TestBuildBaitRanking:
    def test_no_duplicate_bait_labels_by_canonical_name(self):
        species_ranking = [
            {"rank": 1, "name": "Red snapper"},
            {"rank": 2, "name": "Black sea bass"},
            {"rank": 3, "name": "Scup (porgy)"},
        ]

        ranking = build_bait_ranking(species_ranking=species_ranking, month=6)
        labels = [item["bait"] for item in ranking]

        squid_variants = {"Squid strips", "Cut squid strips"}
        assert len([label for label in labels if label in squid_variants]) == 1

    def test_returns_all_items_when_no_alias_duplicates(self):
        species_ranking = [
            {"rank": i + 1, "name": target}
            for i, target in enumerate(BAIT_DB[0]["targets"])
        ]

        ranking = build_bait_ranking(species_ranking=species_ranking, month=6)

        assert len(ranking) <= len(BAIT_DB)


class TestBuildSpeciesCalendar:
    def test_empty_list(self):
        cal = build_species_calendar([])
        assert cal == []

    def test_calendar_structure(self):
        ranked = [{"name": "Red drum (puppy drum)", "score": 80}]
        cal = build_species_calendar(ranked)
        assert len(cal) == 1
        assert cal[0]["name"] == "Red drum (puppy drum)"
        assert len(cal[0]["months"]) == 12
        for m in cal[0]["months"]:
            assert "abbr" in m
            assert "level" in m
            assert m["level"] in ("peak", "good", "")


class TestPersonalizationHardGate:
    """Fishing-type hard gate: incompatible species must be excluded.

    Species names used as anchors and why they're single-method:
    - "Sheepshead"               pier-only  (in _PIER_SPECIES, absent from _SURF/_INSHORE)
    - "Pompano"                  surf-only  (in _SURF_SPECIES, absent from _PIER/_INSHORE)
    - "Mahi-mahi (dolphinfish)"  offshore-only (in _OFFSHORE_ONLY_SPECIES)
    - "Speckled trout ..."       inshore-only  (in _INSHORE_SPECIES, absent from _SURF/_PIER)
    """

    # ------------------------------------------------------------------ #
    # Pier-only profile                                                    #
    # ------------------------------------------------------------------ #
    def test_pier_only_includes_pier_species(self):
        assert _species_matches_profile("Sheepshead", fishing_types=["pier"]) is True

    def test_pier_only_excludes_surf_only_species(self):
        assert _species_matches_profile("Pompano", fishing_types=["pier"]) is False

    def test_pier_only_excludes_offshore_only_species(self):
        assert (
            _species_matches_profile("Mahi-mahi (dolphinfish)", fishing_types=["pier"])
            is False
        )

    # ------------------------------------------------------------------ #
    # Surf-only profile                                                    #
    # ------------------------------------------------------------------ #
    def test_surf_only_includes_surf_species(self):
        assert _species_matches_profile("Pompano", fishing_types=["surf"]) is True

    def test_surf_only_excludes_pier_only_species(self):
        assert _species_matches_profile("Sheepshead", fishing_types=["surf"]) is False

    def test_surf_only_excludes_offshore_only_species(self):
        assert (
            _species_matches_profile("Mahi-mahi (dolphinfish)", fishing_types=["surf"])
            is False
        )

    # ------------------------------------------------------------------ #
    # Offshore/boat-only profile                                           #
    # ------------------------------------------------------------------ #
    def test_offshore_only_includes_offshore_species(self):
        assert (
            _species_matches_profile(
                "Mahi-mahi (dolphinfish)", fishing_types=["offshore"]
            )
            is True
        )

    def test_offshore_only_excludes_pier_only_species(self):
        assert (
            _species_matches_profile("Sheepshead", fishing_types=["offshore"]) is False
        )

    def test_offshore_only_excludes_surf_only_species(self):
        assert _species_matches_profile("Pompano", fishing_types=["offshore"]) is False

    def test_offshore_only_excludes_inshore_only_species(self):
        assert (
            _species_matches_profile(
                "Speckled trout (spotted seatrout)", fishing_types=["offshore"]
            )
            is False
        )

    # ------------------------------------------------------------------ #
    # Combinations: multi-method profiles should not over-exclude         #
    # ------------------------------------------------------------------ #
    def test_pier_surf_combo_includes_pier_and_surf_species(self):
        assert (
            _species_matches_profile("Sheepshead", fishing_types=["pier", "surf"])
            is True
        )
        assert (
            _species_matches_profile("Pompano", fishing_types=["pier", "surf"]) is True
        )

    def test_no_fishing_types_matches_all(self):
        """Empty fishing_types list (or missing) should not exclude anything."""
        assert _species_matches_profile("Sheepshead", fishing_types=None) is True
        assert (
            _species_matches_profile("Mahi-mahi (dolphinfish)", fishing_types=None)
            is True
        )

    # ------------------------------------------------------------------ #
    # Integration: build_species_ranking respects the hard gate end-to-end#
    # ------------------------------------------------------------------ #
    def test_ranking_pier_only_excludes_surf_and_offshore(self):
        """With a pier-only profile, surf-only and offshore-only names must not appear."""
        ranking = build_species_ranking(
            month=3, water_temp=62, coast="east", fishing_types=["pier"]
        )
        names = {sp["name"] for sp in ranking}
        assert "Pompano" not in names, (
            "Surf-only species Pompano should be absent for pier-only angler"
        )
        assert "Mahi-mahi (dolphinfish)" not in names, (
            "Offshore-only species should be absent for pier-only angler"
        )

    def test_ranking_surf_only_excludes_pier_and_offshore(self):
        """With a surf-only profile, pier-only and offshore-only names must not appear."""
        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", fishing_types=["surf"]
        )
        names = {sp["name"] for sp in ranking}
        assert "Sheepshead" not in names, (
            "Pier-only species Sheepshead should be absent for surf-only angler"
        )
        assert "Mahi-mahi (dolphinfish)" not in names, (
            "Offshore-only species should be absent for surf-only angler"
        )

    def test_ranking_offshore_only_excludes_pier_surf_inshore(self):
        """With an offshore-only profile, pier/surf/inshore-only species must not appear."""
        ranking = build_species_ranking(
            month=7, water_temp=80, coast="east", fishing_types=["offshore"]
        )
        names = {sp["name"] for sp in ranking}
        assert "Sheepshead" not in names, (
            "Pier-only Sheepshead should be absent for offshore angler"
        )
        assert "Pompano" not in names, (
            "Surf-only Pompano should be absent for offshore angler"
        )
        assert "Speckled trout (spotted seatrout)" not in names, (
            "Inshore-only species should be absent for offshore angler"
        )


class TestRetentionProhibited:
    """Tests for _retention_prohibited() — the "can the angler keep this fish?" helper.

    This is NOT a visibility test.  _retention_prohibited() returns True for
    both C&R-only and closed-season regulations because in neither case can
    the fish be retained.  Forecast visibility is controlled separately by
    should_hide_from_forecast(classify_legality(...)) in regulations.py, which
    keeps C&R species visible while hiding only truly-closed fisheries.
    """

    def test_bag_limit_zero_is_retention_prohibited(self):
        """bag_limit=0 means no retention (C&R); fish cannot be kept."""
        assert _retention_prohibited({"bag_limit": "0/day"}) is True

    def test_catch_and_release_phrase_is_retention_prohibited(self):
        """C&R phrase means no retention; fish cannot be kept."""
        assert _retention_prohibited({"notes": "Catch and release only."}) is True

    def test_open_season_is_not_retention_prohibited(self):
        """Open year-round with no restrictions means retention is permitted."""
        assert _retention_prohibited({"season": "Open year-round"}) is False

    def test_catch_and_release_species_visible_with_badge(self, monkeypatch):
        """C&R Sheepshead (bag_limit=0, 'No harvest') must stay in ranking with a C&R badge.

        Policy: 'catch and release only' means targeting is legal — anglers can fish
        for the species, they just must release every catch.  Hiding C&R species would
        silently remove useful forecast information.
        """
        def fake_lookup(species_name, _state):
            if species_name == "Sheepshead":
                return {
                    "bag_limit": "0/day",
                    "season": "Open",
                    "notes": "No harvest",
                }
            return {
                "bag_limit": "5/day",
                "season": "Open",
                "notes": "",
            }

        monkeypatch.setattr("domain.species.lookup_regulation", fake_lookup)

        ranking = build_species_ranking(
            month=3,
            water_temp=62,
            coast="east",
            fishing_types=["pier"],
            state="NC",
        )
        names = [sp["name"] for sp in ranking]

        assert "Sheepshead" in names, (
            "C&R Sheepshead must appear in ranking — targeting is legal"
        )
        sheepshead = next(sp for sp in ranking if sp["name"] == "Sheepshead")
        assert sheepshead["regulation_status"] == "catch_and_release", (
            f"Expected regulation_status='catch_and_release', got {sheepshead['regulation_status']!r}"
        )
        assert [sp["rank"] for sp in ranking] == list(range(1, len(ranking) + 1))

    def test_closed_season_species_hidden_from_ranking(self, monkeypatch):
        """A species with 'Season closed' must be absent — targeting is not permitted."""
        def fake_lookup(species_name, _state):
            if species_name == "Sheepshead":
                return {
                    "bag_limit": "",
                    "season": "Season closed",
                    "notes": "",
                }
            return {
                "bag_limit": "5/day",
                "season": "Open",
                "notes": "",
            }

        monkeypatch.setattr("domain.species.lookup_regulation", fake_lookup)

        ranking = build_species_ranking(
            month=3,
            water_temp=62,
            coast="east",
            fishing_types=["pier"],
            state="NC",
        )
        names = [sp["name"] for sp in ranking]

        assert "Sheepshead" not in names, "Season-closed Sheepshead must be hidden from forecast"
        assert [sp["rank"] for sp in ranking] == list(range(1, len(ranking) + 1))

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

_VALID_CATEGORIES = frozenset(
    {"game_fish", "bait_fish", "panfish", "shark", "ray", "reef_fish",
     "pelagic", "migratory", "shellfish", "other"}
)


class TestSpeciesCategories:
    """_SPECIES_CATEGORIES dict correctness and coverage."""

    def test_all_category_values_are_valid(self):
        """Every category label must belong to the controlled vocabulary."""
        for name, cats in _SPECIES_CATEGORIES.items():
            for cat in cats:
                assert cat in _VALID_CATEGORIES, (
                    f"Species '{name}' has unknown category '{cat}'"
                )

    def test_category_values_are_lists(self):
        """Every entry in _SPECIES_CATEGORIES must be a list."""
        for name, cats in _SPECIES_CATEGORIES.items():
            assert isinstance(cats, list), (
                f"Species '{name}' categories should be a list, got {type(cats)}"
            )
            assert len(cats) >= 1, f"Species '{name}' has empty categories list"

    def test_known_sport_fish_are_game_fish(self):
        assert "game_fish" in _SPECIES_CATEGORIES["Red drum (puppy drum)"]
        assert "game_fish" in _SPECIES_CATEGORIES["Striped bass (rockfish)"]
        assert "game_fish" in _SPECIES_CATEGORIES["Snook"]

    def test_sharks_have_shark_category(self):
        assert "shark" in _SPECIES_CATEGORIES["Blacktip shark"]
        assert "shark" in _SPECIES_CATEGORIES["Bull shark"]
        assert "shark" in _SPECIES_CATEGORIES["Sandbar shark"]

    def test_rays_have_ray_category(self):
        assert "ray" in _SPECIES_CATEGORIES["Cownose ray"]
        assert "ray" in _SPECIES_CATEGORIES["Southern stingray"]
        assert "ray" in _SPECIES_CATEGORIES["Bat ray"]

    def test_shellfish_category(self):
        assert "shellfish" in _SPECIES_CATEGORIES["Dungeness crab (from pier)"]

    def test_pelagic_tunas(self):
        assert "pelagic" in _SPECIES_CATEGORIES["Yellowfin tuna"]
        assert "pelagic" in _SPECIES_CATEGORIES["Mahi-mahi (dolphinfish)"]

    def test_bait_fish(self):
        assert "bait_fish" in _SPECIES_CATEGORIES["Atlantic menhaden (bunker)"]
        assert "bait_fish" in _SPECIES_CATEGORIES["Northern anchovy"]


class TestCategoriesInRankingPayload:
    """build_species_ranking() must include 'categories' in every entry."""

    def test_categories_present_in_ranking(self):
        ranking = build_species_ranking(month=7, water_temp=72, coast="east")
        assert len(ranking) > 0
        for entry in ranking:
            assert "categories" in entry, (
                f"Entry for '{entry['name']}' missing 'categories' key"
            )

    def test_categories_are_valid_vocabulary(self):
        ranking = build_species_ranking(month=7, water_temp=72, coast="east")
        for entry in ranking:
            for cat in entry["categories"]:
                assert cat in _VALID_CATEGORIES, (
                    f"'{entry['name']}' has unknown category '{cat}'"
                )

    def test_categories_non_empty(self):
        ranking = build_species_ranking(month=7, water_temp=72, coast="east")
        for entry in ranking:
            assert len(entry["categories"]) >= 1, (
                f"'{entry['name']}' has empty categories list"
            )

    def test_west_coast_ranking_has_categories(self):
        ranking = build_species_ranking(month=7, water_temp=62, coast="west")
        assert len(ranking) > 0
        for entry in ranking:
            assert "categories" in entry

    def test_hawaii_ranking_has_categories(self):
        ranking = build_species_ranking(month=7, water_temp=78, coast="hawaii")
        assert len(ranking) > 0
        for entry in ranking:
            assert "categories" in entry

    def test_json_categories_field_takes_precedence(self, monkeypatch):
        """If species JSON has a 'categories' field, it overrides the dict."""
        # Patch SPECIES_DB to inject a species with a categories field
        fake_sp = {
            "name": "Red drum (puppy drum)",
            "temp_min": 45, "temp_max": 85,
            "temp_ideal_low": 55, "temp_ideal_high": 75,
            "peak_months": [9, 10],
            "good_months": [3, 4],
            "bait": "Cut bait",
            "rig": "Fish finder",
            "hook_size": "3/0",
            "sinker": "2 oz",
            "explanation_cold": "Cold",
            "explanation_warm": "Warm",
            "coast": "east",
            "categories": ["panfish"],  # override: should produce panfish not game_fish
        }
        monkeypatch.setattr("domain.species.SPECIES_DB", [fake_sp])

        ranking = build_species_ranking(month=10, water_temp=65, coast="east")
        assert len(ranking) == 1
        assert ranking[0]["categories"] == ["panfish"]


class TestCategoriesInSpawningPayload:
    """build_spawning_report() must include 'categories' in every entry."""

    def test_categories_present_in_spawning(self):
        report = build_spawning_report(month=5, water_temp=68, coast="east")
        for entry in report:
            assert "categories" in entry, (
                f"Spawning entry for '{entry['name']}' missing 'categories'"
            )

    def test_spawning_categories_valid(self):
        report = build_spawning_report(month=5, water_temp=68, coast="east")
        for entry in report:
            for cat in entry["categories"]:
                assert cat in _VALID_CATEGORIES


class TestNewSpeciesInDB:
    """New species added in dataset expansion are present and well-formed."""

    def test_blue_crab_in_db(self):
        names = [s["name"] for s in SPECIES_DB]
        assert "Blue crab" in names

    def test_great_hammerhead_in_db(self):
        names = [s["name"] for s in SPECIES_DB]
        assert "Great hammerhead shark" in names

    def test_ca_spiny_lobster_in_db(self):
        names = [s["name"] for s in SPECIES_DB]
        assert "California spiny lobster" in names

    def test_pacific_barracuda_in_db(self):
        names = [s["name"] for s in SPECIES_DB]
        assert "Pacific barracuda (California barracuda)" in names

    def test_toau_in_db(self):
        names = [s["name"] for s in SPECIES_DB]
        assert "Toau (blacktail snapper)" in names

    def test_roi_in_db(self):
        names = [s["name"] for s in SPECIES_DB]
        assert "Roi (peacock grouper)" in names

    def test_weke_in_db(self):
        names = [s["name"] for s in SPECIES_DB]
        assert "Weke (goatfish)" in names

    def test_new_species_have_valid_categories_in_json(self):
        """New species JSON entries must have valid categories lists."""
        target_names = {
            "Blue crab", "Longfin inshore squid", "Florida spiny lobster",
            "Blue shark", "Great hammerhead shark", "California spiny lobster",
            "Pacific barracuda (California barracuda)",
            "Toau (blacktail snapper)", "Roi (peacock grouper)", "Weke (goatfish)",
        }
        for sp in SPECIES_DB:
            if sp["name"] in target_names:
                assert "categories" in sp, f"{sp['name']} missing categories in JSON"
                assert isinstance(sp["categories"], list)
                for cat in sp["categories"]:
                    assert cat in _VALID_CATEGORIES, (
                        f"{sp['name']} has unknown category '{cat}'"
                    )

    def test_blue_crab_shellfish_category(self):
        sp = next(s for s in SPECIES_DB if s["name"] == "Blue crab")
        assert "shellfish" in sp["categories"]

    def test_great_hammerhead_shark_category(self):
        sp = next(s for s in SPECIES_DB if s["name"] == "Great hammerhead shark")
        assert "shark" in sp["categories"]
        assert "game_fish" in sp["categories"]

    def test_pacific_barracuda_game_fish_pelagic(self):
        sp = next(
            s for s in SPECIES_DB
            if s["name"] == "Pacific barracuda (California barracuda)"
        )
        assert "game_fish" in sp["categories"]
        assert "pelagic" in sp["categories"]

    def test_new_species_appear_in_ranking_when_conditions_match(self):
        """Blue crab (east, warm water) should score above threshold and appear when
        filtered to bottom/inshore targets that compete with fewer species."""
        from domain.species import _score_species, SPECIES_SCORE_THRESHOLD

        blue_crab = next(s for s in SPECIES_DB if s["name"] == "Blue crab")
        score = _score_species(blue_crab, month=7, water_temp=76)
        assert score >= SPECIES_SCORE_THRESHOLD, (
            f"Blue crab score {score} is below threshold {SPECIES_SCORE_THRESHOLD}"
        )

    def test_great_hammerhead_scores_above_threshold(self):
        """Great hammerhead should score above the ranking threshold in summer."""
        from domain.species import _score_species, SPECIES_SCORE_THRESHOLD

        gh = next(s for s in SPECIES_DB if s["name"] == "Great hammerhead shark")
        score = _score_species(gh, month=7, water_temp=82)
        assert score >= SPECIES_SCORE_THRESHOLD

    def test_ca_spiny_lobster_appears_in_west_fall_ranking(self):
        """CA spiny lobster should appear in west coast fall ranking."""
        ranking = build_species_ranking(month=11, water_temp=62, coast="west")
        names = [sp["name"] for sp in ranking]
        assert "California spiny lobster" in names

    def test_hawaii_new_species_appear_in_ranking(self):
        """New Hawaii species should appear in Hawaii ranking."""
        ranking = build_species_ranking(month=6, water_temp=78, coast="hawaii")
        names = [sp["name"] for sp in ranking]
        # At least one of the new Hawaii species should be in top 10
        new_hawaii = {
            "Toau (blacktail snapper)", "Roi (peacock grouper)", "Weke (goatfish)"
        }
        assert new_hawaii & set(names), (
            f"None of {new_hawaii} appeared in Hawaii ranking: {names}"
        )
