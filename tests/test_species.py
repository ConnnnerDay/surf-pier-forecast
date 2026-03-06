"""Tests for domain.species scoring and ranking."""

import pytest

from domain.species import (
    BAIT_DB,
    SPECIES_DB,
    _regulation_disallows_keep,
    _score_species,
    _species_matches_profile,
    build_bait_ranking,
    build_natural_bait_chart,
    build_species_calendar,
    build_species_ranking,
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
            red_drum, month=5, water_temp=68,
            wind_dir="SW", wind_range=(8, 12),
            wave_range=(2, 3), hour=6, coast="east",
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
        species_ranking = [{"rank": i + 1, "name": target}
                           for i, target in enumerate(BAIT_DB[0]["targets"])]

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
        assert _species_matches_profile("Mahi-mahi (dolphinfish)", fishing_types=["pier"]) is False

    # ------------------------------------------------------------------ #
    # Surf-only profile                                                    #
    # ------------------------------------------------------------------ #
    def test_surf_only_includes_surf_species(self):
        assert _species_matches_profile("Pompano", fishing_types=["surf"]) is True

    def test_surf_only_excludes_pier_only_species(self):
        assert _species_matches_profile("Sheepshead", fishing_types=["surf"]) is False

    def test_surf_only_excludes_offshore_only_species(self):
        assert _species_matches_profile("Mahi-mahi (dolphinfish)", fishing_types=["surf"]) is False

    # ------------------------------------------------------------------ #
    # Offshore/boat-only profile                                           #
    # ------------------------------------------------------------------ #
    def test_offshore_only_includes_offshore_species(self):
        assert _species_matches_profile("Mahi-mahi (dolphinfish)", fishing_types=["offshore"]) is True

    def test_offshore_only_excludes_pier_only_species(self):
        assert _species_matches_profile("Sheepshead", fishing_types=["offshore"]) is False

    def test_offshore_only_excludes_surf_only_species(self):
        assert _species_matches_profile("Pompano", fishing_types=["offshore"]) is False

    def test_offshore_only_excludes_inshore_only_species(self):
        assert _species_matches_profile(
            "Speckled trout (spotted seatrout)", fishing_types=["offshore"]
        ) is False

    # ------------------------------------------------------------------ #
    # Combinations: multi-method profiles should not over-exclude         #
    # ------------------------------------------------------------------ #
    def test_pier_surf_combo_includes_pier_and_surf_species(self):
        assert _species_matches_profile("Sheepshead", fishing_types=["pier", "surf"]) is True
        assert _species_matches_profile("Pompano", fishing_types=["pier", "surf"]) is True

    def test_no_fishing_types_matches_all(self):
        """Empty fishing_types list (or missing) should not exclude anything."""
        assert _species_matches_profile("Sheepshead", fishing_types=None) is True
        assert _species_matches_profile("Mahi-mahi (dolphinfish)", fishing_types=None) is True

    # ------------------------------------------------------------------ #
    # Integration: build_species_ranking respects the hard gate end-to-end#
    # ------------------------------------------------------------------ #
    def test_ranking_pier_only_excludes_surf_and_offshore(self):
        """With a pier-only profile, surf-only and offshore-only names must not appear."""
        ranking = build_species_ranking(
            month=3, water_temp=62, coast="east", fishing_types=["pier"]
        )
        names = {sp["name"] for sp in ranking}
        assert "Pompano" not in names, "Surf-only species Pompano should be absent for pier-only angler"
        assert "Mahi-mahi (dolphinfish)" not in names, "Offshore-only species should be absent for pier-only angler"

    def test_ranking_surf_only_excludes_pier_and_offshore(self):
        """With a surf-only profile, pier-only and offshore-only names must not appear."""
        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", fishing_types=["surf"]
        )
        names = {sp["name"] for sp in ranking}
        assert "Sheepshead" not in names, "Pier-only species Sheepshead should be absent for surf-only angler"
        assert "Mahi-mahi (dolphinfish)" not in names, "Offshore-only species should be absent for surf-only angler"

    def test_ranking_offshore_only_excludes_pier_surf_inshore(self):
        """With an offshore-only profile, pier/surf/inshore-only species must not appear."""
        ranking = build_species_ranking(
            month=7, water_temp=80, coast="east", fishing_types=["offshore"]
        )
        names = {sp["name"] for sp in ranking}
        assert "Sheepshead" not in names, "Pier-only Sheepshead should be absent for offshore angler"
        assert "Pompano" not in names, "Surf-only Pompano should be absent for offshore angler"
        assert "Speckled trout (spotted seatrout)" not in names, "Inshore-only species should be absent for offshore angler"


class TestRegulationHarvestFilter:
    def test_regulation_disallow_parser(self):
        assert _regulation_disallows_keep({"bag_limit": "0/day"}) is True
        assert _regulation_disallows_keep({"notes": "Catch and release only."}) is True
        assert _regulation_disallows_keep({"season": "Open year-round"}) is False

    def test_ranking_hides_species_that_cannot_be_kept(self, monkeypatch):
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

        assert "Sheepshead" not in names
        assert [sp["rank"] for sp in ranking] == list(range(1, len(ranking) + 1))
