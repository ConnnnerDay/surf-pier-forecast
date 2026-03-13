"""Tests for regulation legality filtering, status classification, and UI signal behavior.

Covers:
  1. A legal species that remains visible in 'What's Biting'.
  2. A prohibited/closed species that is hidden from 'What's Biting'.
  3. A prohibited/closed species that is hidden from 'What's Spawning Now'.
  4. A species with unknown regulation status that is NOT presented as definitely legal.
  5. Stale cached regulations flagged as stale in the returned payload.
  6. Official state regulation URL present in species ranking and forecast payloads.
"""

from __future__ import annotations

import pytest

from domain.species import build_spawning_report, build_species_ranking
from regulations import classify_legality, get_official_regulations_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_reg(**extra) -> dict:
    """Return a minimal regulation dict indicating an open, legal fishery."""
    base = {
        "bag_limit": "5 per day",
        "season": "Open year-round",
        "notes": "",
        "official_source": "https://example.com/regs",
        "data_status": "snapshot",
        "is_stale": False,
        "last_updated": "2025-01",
    }
    base.update(extra)
    return base


def _prohibited_reg(**extra) -> dict:
    """Return a minimal regulation dict indicating harvest is prohibited."""
    base = {
        "bag_limit": "0/day",
        "season": "Catch and release only.",
        "notes": "No harvest permitted.",
        "official_source": "https://example.com/regs",
        "data_status": "snapshot",
        "is_stale": False,
        "last_updated": "2025-01",
    }
    base.update(extra)
    return base


def _unknown_reg(**extra) -> dict:
    """Return a minimal regulation dict with no usable data (link-only payload)."""
    base = {
        "bag_limit": "",
        "season": "",
        "notes": (
            "Species-specific limits were not found. "
            "Use the official source link for current bag, size, and season rules."
        ),
        "official_source": "https://example.com/regs",
        "data_status": "official_reference",
        "is_stale": True,
        "last_updated": "",
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Tests: classify_legality() unit behaviour
# ---------------------------------------------------------------------------

class TestClassifyLegality:
    """Unit tests for the public classify_legality() helper in regulations.py."""

    def test_open_year_round_returns_legal(self):
        assert classify_legality({"bag_limit": "3 per day", "season": "Open year-round", "notes": ""}) == "legal"

    def test_explicit_bag_limit_no_season_keywords_returns_legal(self):
        assert classify_legality({"bag_limit": "10 per day", "season": "Open", "notes": ""}) == "legal"

    def test_catch_release_season_returns_prohibited(self):
        reg = {"bag_limit": "", "season": "Catch and release only", "notes": ""}
        assert classify_legality(reg) == "prohibited"

    def test_catch_release_notes_returns_prohibited(self):
        reg = {"bag_limit": "5 per day", "season": "Open", "notes": "Catch and release only."}
        assert classify_legality(reg) == "prohibited"

    def test_bag_limit_zero_returns_prohibited(self):
        assert classify_legality({"bag_limit": "0/day", "season": "Open", "notes": ""}) == "prohibited"

    def test_bag_limit_zero_per_day_returns_prohibited(self):
        assert classify_legality({"bag_limit": "0 per day", "season": "Open", "notes": ""}) == "prohibited"

    def test_harvest_prohibited_phrase_returns_prohibited(self):
        reg = {"bag_limit": "", "season": "", "notes": "Harvest prohibited in all areas."}
        assert classify_legality(reg) == "prohibited"

    def test_no_harvest_phrase_returns_prohibited(self):
        reg = {"bag_limit": "", "season": "No harvest", "notes": ""}
        assert classify_legality(reg) == "prohibited"

    def test_federally_protected_returns_prohibited(self):
        reg = {"bag_limit": "", "season": "", "notes": "Federally protected species."}
        assert classify_legality(reg) == "prohibited"

    def test_closed_year_round_returns_prohibited(self):
        reg = {"bag_limit": "", "season": "Closed year-round", "notes": ""}
        assert classify_legality(reg) == "prohibited"

    def test_month_in_closed_range_returns_out_of_season(self):
        # Season says closed Jan-Apr; month=2 (February) is inside that window
        reg = {"bag_limit": "5/day", "season": "Closed Jan-Apr", "notes": ""}
        assert classify_legality(reg, month=2) == "out_of_season"

    def test_month_outside_closed_range_returns_legal(self):
        # Season says closed Jan-Apr; month=7 (July) is outside that window
        reg = {"bag_limit": "5/day", "season": "Closed Jan-Apr", "notes": ""}
        assert classify_legality(reg, month=7) == "legal"

    def test_year_wrapping_closed_range(self):
        # Closed Nov-Feb wraps the year end
        reg = {"bag_limit": "5/day", "season": "Closed Nov-Feb", "notes": ""}
        assert classify_legality(reg, month=1) == "out_of_season"   # Jan is closed
        assert classify_legality(reg, month=6) == "legal"           # Jun is open

    def test_standalone_season_closed_returns_out_of_season(self):
        reg = {"bag_limit": "3/day", "season": "Season closed", "notes": ""}
        assert classify_legality(reg) == "out_of_season"

    def test_qualified_season_closed_returns_restricted(self):
        # Qualifiers like "some areas have closed seasons" must NOT trigger out_of_season
        reg = {"bag_limit": "3/day", "season": "Some areas have closed seasons", "notes": ""}
        result = classify_legality(reg)
        assert result in ("restricted",), f"Expected restricted, got {result!r}"

    def test_seasonal_keyword_returns_restricted(self):
        reg = {"bag_limit": "2/day", "season": "Seasonal closure may apply; check state", "notes": ""}
        assert classify_legality(reg) == "restricted"

    def test_some_areas_closed_returns_restricted(self):
        reg = {"bag_limit": "5/day", "season": "Open; some areas closed", "notes": ""}
        assert classify_legality(reg) == "restricted"

    def test_none_reg_returns_unknown(self):
        assert classify_legality(None) == "unknown"

    def test_empty_fields_returns_unknown(self):
        assert classify_legality({"bag_limit": "", "season": "", "notes": ""}) == "unknown"

    def test_link_only_payload_returns_unknown(self):
        reg = {
            "bag_limit": "",
            "season": "",
            "notes": (
                "Species-specific limits were not found. "
                "Use the official source link for current bag, size, and season rules."
            ),
        }
        assert classify_legality(reg) == "unknown"

    def test_no_month_skips_month_specific_check(self):
        # With month=0 (default), a closed range should NOT fire
        reg = {"bag_limit": "5/day", "season": "Closed Jan-Apr", "notes": ""}
        result = classify_legality(reg, month=0)
        # Month check is skipped; season text does not contain standalone "season closed"
        assert result == "legal"


# ---------------------------------------------------------------------------
# Tests: get_official_regulations_url()
# ---------------------------------------------------------------------------

class TestGetOfficialRegulationsUrl:
    def test_known_state_returns_url(self):
        url = get_official_regulations_url("NC")
        assert url.startswith("https://")
        assert "nc" in url.lower() or "deq" in url.lower() or "marine" in url.lower()

    def test_unknown_state_returns_fallback(self):
        url = get_official_regulations_url("ZZ")
        assert url.startswith("https://")

    def test_empty_state_returns_fallback(self):
        url = get_official_regulations_url("")
        assert url.startswith("https://")

    def test_case_insensitive(self):
        assert get_official_regulations_url("nc") == get_official_regulations_url("NC")


# ---------------------------------------------------------------------------
# Tests: build_species_ranking() — regulation_status field & filtering
# ---------------------------------------------------------------------------

class TestSpeciesRankingRegulationStatus:

    # -- Test 1: legal species remains visible and carries regulation_status='legal'
    def test_legal_species_visible_and_status_is_legal(self, monkeypatch):
        """Species with open regulations must appear in ranking with regulation_status='legal'."""
        monkeypatch.setattr("domain.species.lookup_regulation", lambda name, st: _open_reg())

        ranking = build_species_ranking(month=6, water_temp=72, coast="east", state="NC")

        assert len(ranking) > 0, "Expected at least one species in ranking"
        statuses = {sp.get("regulation_status") for sp in ranking}
        assert "legal" in statuses, f"Expected 'legal' among statuses; got {statuses}"

    # -- Test 2: prohibited species is hidden from "What's Biting"
    def test_prohibited_species_hidden_from_biting(self, monkeypatch):
        """Sheepshead with bag_limit=0 / C&R must NOT appear in What's Biting."""
        def fake_lookup(name, state):
            if name == "Sheepshead":
                return _prohibited_reg()
            return _open_reg()

        monkeypatch.setattr("domain.species.lookup_regulation", fake_lookup)

        ranking = build_species_ranking(
            month=3, water_temp=62, coast="east", fishing_types=["pier"], state="NC"
        )
        names = [sp["name"] for sp in ranking]
        assert "Sheepshead" not in names, "Prohibited species Sheepshead should be absent"

    def test_out_of_season_species_hidden_from_biting(self, monkeypatch):
        """Species in a closed season this month must NOT appear in What's Biting."""
        # Use month=2; regulation says closed Jan-Apr → out_of_season for Feb
        def fake_lookup(name, state):
            if name == "Sheepshead":
                return {"bag_limit": "5/day", "season": "Closed Jan-Apr", "notes": "",
                        "official_source": "https://example.com", "is_stale": False, "data_status": "snapshot"}
            return _open_reg()

        monkeypatch.setattr("domain.species.lookup_regulation", fake_lookup)

        ranking = build_species_ranking(
            month=2, water_temp=55, coast="east", fishing_types=["pier"], state="NC"
        )
        names = [sp["name"] for sp in ranking]
        assert "Sheepshead" not in names, "Out-of-season Sheepshead should be absent in Feb"

    # -- Test 4: unknown regulation status NOT presented as definitely legal
    def test_unknown_status_not_claimed_legal(self, monkeypatch):
        """When no regulation data exists the species must carry regulation_status='unknown'."""
        monkeypatch.setattr("domain.species.lookup_regulation", lambda name, st: _unknown_reg())

        ranking = build_species_ranking(month=6, water_temp=72, coast="east", state="NC")

        assert len(ranking) > 0
        for sp in ranking:
            status = sp.get("regulation_status")
            assert status == "unknown", (
                f"'{sp['name']}' has regulation_status={status!r}; expected 'unknown' "
                "when data is link-only"
            )

    def test_regulation_status_field_present_when_state_given(self, monkeypatch):
        """Every ranked species should have a regulation_status key when state is provided."""
        monkeypatch.setattr("domain.species.lookup_regulation", lambda name, st: _open_reg())
        ranking = build_species_ranking(month=6, water_temp=72, coast="east", state="NC")
        for sp in ranking:
            assert "regulation_status" in sp, (
                f"'{sp['name']}' missing regulation_status field"
            )

    def test_regulation_status_absent_without_state(self):
        """Without a state, regulation_status should NOT be added to entries."""
        ranking = build_species_ranking(month=6, water_temp=72, coast="east", state="")
        # Some entries may legitimately have no regulation at all
        for sp in ranking:
            assert "regulation_status" not in sp, (
                f"'{sp['name']}' should not have regulation_status when no state given"
            )

    def test_ranks_are_sequential_after_regulation_filter(self, monkeypatch):
        """Ranks must form a continuous sequence 1..N after prohibited species are removed."""
        def fake_lookup(name, state):
            if name == "Sheepshead":
                return _prohibited_reg()
            return _open_reg()

        monkeypatch.setattr("domain.species.lookup_regulation", fake_lookup)
        ranking = build_species_ranking(
            month=3, water_temp=62, coast="east", fishing_types=["pier"], state="NC"
        )
        ranks = [sp["rank"] for sp in ranking]
        assert ranks == list(range(1, len(ranking) + 1)), "Ranks must be 1..N with no gaps"


# ---------------------------------------------------------------------------
# Tests: build_spawning_report() — filtering and regulation_status field
# ---------------------------------------------------------------------------

class TestSpawningReportRegulationStatus:

    # -- Test 3: prohibited species hidden from "What's Spawning Now"
    def test_prohibited_species_hidden_from_spawning(self, monkeypatch):
        """Species that are catch-and-release (bag=0, C&R) must not appear in spawning list."""
        monkeypatch.setattr("domain.species.lookup_regulation", lambda name, st: _prohibited_reg())

        spawning = build_spawning_report(month=5, water_temp=72, coast="east", state="NC")
        assert spawning == [], (
            "All species should be hidden when every lookup returns a prohibited regulation"
        )

    def test_legal_species_visible_in_spawning(self, monkeypatch):
        """Species with open regulations should appear in the spawning report."""
        monkeypatch.setattr("domain.species.lookup_regulation", lambda name, st: _open_reg())

        spawning = build_spawning_report(month=5, water_temp=72, coast="east", state="NC")
        assert len(spawning) > 0, "Expected at least one species in spawning report"

    def test_spawning_regulation_status_legal_for_open_regs(self, monkeypatch):
        """Spawning entries with open regs must carry regulation_status='legal'."""
        monkeypatch.setattr("domain.species.lookup_regulation", lambda name, st: _open_reg())

        spawning = build_spawning_report(month=5, water_temp=72, coast="east", state="NC")
        for sp in spawning:
            assert "regulation_status" in sp, f"'{sp['name']}' missing regulation_status"
            assert sp["regulation_status"] == "legal", (
                f"'{sp['name']}' has regulation_status={sp['regulation_status']!r}"
            )

    def test_spawning_regulation_status_unknown_when_no_data(self, monkeypatch):
        """When no reg data exists, spawning entries should carry regulation_status='unknown'."""
        monkeypatch.setattr("domain.species.lookup_regulation", lambda name, st: _unknown_reg())

        spawning = build_spawning_report(month=5, water_temp=72, coast="east", state="NC")
        # Unknown regs → show the entry (conservative but useful)
        for sp in spawning:
            assert sp.get("regulation_status") == "unknown", (
                f"'{sp['name']}' should be unknown when no regulation data is found"
            )

    def test_spawning_legal_status_field_preserved(self, monkeypatch):
        """The legacy legal_status field must still be present (backward compatibility)."""
        monkeypatch.setattr("domain.species.lookup_regulation", lambda name, st: _open_reg())

        spawning = build_spawning_report(month=5, water_temp=72, coast="east", state="NC")
        for sp in spawning:
            assert "legal_status" in sp, f"'{sp['name']}' missing legacy legal_status field"
            assert sp["legal_status"] in ("catch_release", "restricted", "open", "unknown")


# ---------------------------------------------------------------------------
# Tests: stale regulation data is flagged
# ---------------------------------------------------------------------------

class TestStaleRegulationBehavior:

    # -- Test 5: stale cached regulations flagged via is_stale
    def test_stale_species_still_shown_in_ranking(self, monkeypatch):
        """Stale regulation data should NOT hide a species — show it with a warning instead."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda name, st: _open_reg(is_stale=True, last_updated="2023-01"),
        )

        ranking = build_species_ranking(month=6, water_temp=72, coast="east", state="NC")
        assert len(ranking) > 0, "Stale regs must not hide species from the ranking"

    def test_stale_regulation_is_flagged_in_entry(self, monkeypatch):
        """is_stale=True must be present in each species' regulation dict when data is stale."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda name, st: _open_reg(is_stale=True, last_updated="2023-01"),
        )

        ranking = build_species_ranking(month=6, water_temp=72, coast="east", state="NC")
        for sp in ranking:
            reg = sp.get("regulation") or {}
            assert reg.get("is_stale") is True, (
                f"'{sp['name']}' should have is_stale=True in its regulation dict"
            )

    def test_stale_spawning_entry_still_shown(self, monkeypatch):
        """Stale regulation data should not hide spawning entries."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda name, st: _open_reg(is_stale=True, last_updated="2023-01"),
        )

        spawning = build_spawning_report(month=5, water_temp=72, coast="east", state="NC")
        assert len(spawning) > 0, "Stale regs must not hide spawning entries"
        for sp in spawning:
            reg = sp.get("regulation") or {}
            assert reg.get("is_stale") is True


# ---------------------------------------------------------------------------
# Tests: official regulations URL present in payloads
# ---------------------------------------------------------------------------

class TestOfficialRegulationsUrl:

    # -- Test 6: official state regulation link present
    def test_official_source_in_species_regulation(self, monkeypatch):
        """Each ranked species must carry an official_source URL in its regulation dict."""
        expected_url = "https://example.com/official-regs"
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda name, st: _open_reg(official_source=expected_url),
        )

        ranking = build_species_ranking(month=6, water_temp=72, coast="east", state="NC")
        assert len(ranking) > 0
        for sp in ranking:
            assert sp.get("regulation", {}).get("official_source") == expected_url

    def test_official_source_in_spawning_regulation(self, monkeypatch):
        """Each spawning entry must carry an official_source URL in its regulation dict."""
        expected_url = "https://example.com/spawning-regs"
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda name, st: _open_reg(official_source=expected_url),
        )

        spawning = build_spawning_report(month=5, water_temp=72, coast="east", state="NC")
        assert len(spawning) > 0
        for sp in spawning:
            assert sp.get("regulation", {}).get("official_source") == expected_url

    def test_get_official_regulations_url_for_all_supported_states(self):
        """Every state we explicitly support should return a non-empty URL."""
        supported = ["AL", "CA", "DE", "FL", "GA", "HI", "LA", "MA", "MD",
                     "ME", "MS", "NC", "NJ", "NY", "OR", "RI", "SC", "TX", "VA", "WA"]
        for state in supported:
            url = get_official_regulations_url(state)
            assert url.startswith("https://"), (
                f"State {state} returned unexpected URL: {url!r}"
            )
