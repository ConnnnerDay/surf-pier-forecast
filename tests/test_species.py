"""Tests for domain.species scoring and ranking."""

import pytest

from domain.species import (
    SPECIES_DB,
    _score_species,
    _species_matches_profile,
    build_natural_bait_chart,
    build_species_calendar,
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
