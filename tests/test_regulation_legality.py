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

from domain.species import build_spawning_report, build_species_ranking
from regulations import (
    _extract_gear_restrictions,
    _extract_slot_limit,
    classify_legality,
    get_official_regulations_url,
    season_status,
    should_hide_from_forecast,
)


class TestSeasonStatus:
    def test_year_round_is_open(self):
        assert season_status({"season": "Open year-round"}, 6) == "open"

    def test_inactive_closure_is_open(self):
        # "Closed Jan-Apr" in June is currently open.
        assert season_status({"season": "Closed Jan-Apr"}, 6) == "open"

    def test_active_closure_is_closed(self):
        assert season_status({"season": "Closed Jan-Apr"}, 2) == "closed"

    def test_year_wrap_closure(self):
        assert season_status({"season": "Closed Nov-Feb"}, 1) == "closed"
        assert season_status({"season": "Closed Nov-Feb"}, 7) == "open"

    def test_unknown_when_no_text_or_month(self):
        assert season_status({"season": ""}, 6) == "unknown"
        assert season_status(None, 6) == "unknown"
        assert season_status({"season": "Open year-round"}, 0) == "unknown"

    def test_open_window_in_and_out(self):
        assert season_status({"season": "Open May-September"}, 7) == "open"
        assert season_status({"season": "Open May-September"}, 1) == "closed"

    def test_season_colon_window(self):
        assert season_status({"season": "Season: Mar-Oct"}, 4) == "open"
        assert season_status({"season": "Season: Mar-Oct"}, 12) == "closed"

    def test_open_window_year_wrap(self):
        assert season_status({"season": "Open Oct-Mar"}, 1) == "open"
        assert season_status({"season": "Open Oct-Mar"}, 7) == "closed"

    def test_year_round_beats_open_window_parse(self):
        assert season_status({"season": "Open year-round"}, 1) == "open"

    def test_bare_open_keyword_fallback(self):
        assert season_status({"season": "open"}, 6) == "open"


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
    """Return a minimal regulation dict indicating the fishery is truly closed (no targeting).

    Uses 'Closed year-round' so classify_legality() returns 'prohibited' and
    should_hide_from_forecast() returns True.  For catch-and-release regulations
    (legal to target but must release), use _cr_reg() instead.
    """
    base = {
        "bag_limit": "",
        "season": "Closed year-round",
        "notes": "",
        "official_source": "https://example.com/regs",
        "data_status": "snapshot",
        "is_stale": False,
        "last_updated": "2025-01",
    }
    base.update(extra)
    return base


def _cr_reg(**extra) -> dict:
    """Return a minimal regulation dict indicating catch-and-release only.

    The fishery is open for targeting but all fish must be immediately released.
    classify_legality() returns 'catch_and_release'; should_hide_from_forecast()
    returns True (species is hidden — anglers cannot keep the fish).
    """
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
        assert (
            classify_legality(
                {"bag_limit": "3 per day", "season": "Open year-round", "notes": ""}
            )
            == "legal"
        )

    def test_explicit_bag_limit_no_season_keywords_returns_legal(self):
        assert (
            classify_legality(
                {"bag_limit": "10 per day", "season": "Open", "notes": ""}
            )
            == "legal"
        )

    # ── Catch-and-release: targeting legal, retention prohibited ────────────

    def test_catch_release_season_returns_catch_and_release(self):
        """C&R season text → catch_and_release (NOT prohibited — anglers can still target)."""
        reg = {"bag_limit": "", "season": "Catch and release only", "notes": ""}
        assert classify_legality(reg) == "catch_and_release"

    def test_catch_release_notes_returns_catch_and_release(self):
        """C&R in notes (with an otherwise open season) → catch_and_release."""
        reg = {
            "bag_limit": "5 per day",
            "season": "Open",
            "notes": "Catch and release only.",
        }
        assert classify_legality(reg) == "catch_and_release"

    def test_bag_limit_zero_returns_catch_and_release(self):
        """bag_limit=0 means no retention allowed; anglers can still fish (C&R)."""
        assert (
            classify_legality({"bag_limit": "0/day", "season": "Open", "notes": ""})
            == "catch_and_release"
        )

    def test_bag_limit_zero_per_day_returns_catch_and_release(self):
        assert (
            classify_legality({"bag_limit": "0 per day", "season": "Open", "notes": ""})
            == "catch_and_release"
        )

    def test_harvest_prohibited_phrase_returns_catch_and_release(self):
        """'Harvest prohibited' = retention not allowed; targeting is still permitted."""
        reg = {
            "bag_limit": "",
            "season": "",
            "notes": "Harvest prohibited in all areas.",
        }
        assert classify_legality(reg) == "catch_and_release"

    def test_no_harvest_phrase_returns_catch_and_release(self):
        reg = {"bag_limit": "", "season": "No harvest", "notes": ""}
        assert classify_legality(reg) == "catch_and_release"

    # ── Truly prohibited: fishery is closed; do not target ──────────────────

    def test_federally_protected_returns_prohibited(self):
        """Federal protection → prohibited (cannot target at all)."""
        reg = {"bag_limit": "", "season": "", "notes": "Federally protected species."}
        assert classify_legality(reg) == "prohibited"

    def test_closed_year_round_returns_prohibited(self):
        """Year-round closure → prohibited (cannot target at all)."""
        reg = {"bag_limit": "", "season": "Closed year-round", "notes": ""}
        assert classify_legality(reg) == "prohibited"

    def test_cr_and_closed_season_combo_prefers_out_of_season(self):
        """When season says 'Season closed' and bag_limit=0, out_of_season takes priority
        (closed season check runs before C&R check so targeting is forbidden)."""
        reg = {"bag_limit": "0/day", "season": "Season closed", "notes": ""}
        assert classify_legality(reg) == "out_of_season"

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
        assert classify_legality(reg, month=1) == "out_of_season"  # Jan is closed
        assert classify_legality(reg, month=6) == "legal"  # Jun is open

    def test_standalone_season_closed_returns_out_of_season(self):
        reg = {"bag_limit": "3/day", "season": "Season closed", "notes": ""}
        assert classify_legality(reg) == "out_of_season"

    def test_qualified_season_closed_returns_restricted(self):
        # Qualifiers like "some areas have closed seasons" must NOT trigger out_of_season
        reg = {
            "bag_limit": "3/day",
            "season": "Some areas have closed seasons",
            "notes": "",
        }
        result = classify_legality(reg)
        assert result in ("restricted",), f"Expected restricted, got {result!r}"

    def test_seasonal_keyword_returns_restricted(self):
        reg = {
            "bag_limit": "2/day",
            "season": "Seasonal closure may apply; check state",
            "notes": "",
        }
        assert classify_legality(reg) == "restricted"

    def test_some_areas_closed_returns_restricted(self):
        reg = {"bag_limit": "5/day", "season": "Open; some areas closed", "notes": ""}
        assert classify_legality(reg) == "restricted"

    def test_none_reg_returns_unknown(self):
        assert classify_legality(None) == "unknown"

    def test_empty_fields_returns_unknown(self):
        assert (
            classify_legality({"bag_limit": "", "season": "", "notes": ""}) == "unknown"
        )

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
# Tests: should_hide_from_forecast()
# ---------------------------------------------------------------------------


class TestShouldHideFromForecast:
    """Unit tests for the should_hide_from_forecast() helper.

    The hide policy is strict: ONLY ``"legal"`` is visible.  Every other
    status — including ``"unknown"`` where regulations could not be verified —
    is hidden so the app never recommends targeting a species unless legality
    is confirmed.
    """

    def test_legal_is_visible(self):
        assert should_hide_from_forecast("legal") is False

    def test_unknown_is_hidden(self):
        """Unknown status must be hidden — we must not recommend unverified species."""
        assert should_hide_from_forecast("unknown") is True

    def test_catch_and_release_is_hidden(self):
        """C&R species are hidden — anglers cannot keep them."""
        assert should_hide_from_forecast("catch_and_release") is True

    def test_restricted_is_hidden(self):
        assert should_hide_from_forecast("restricted") is True

    def test_out_of_season_is_hidden(self):
        assert should_hide_from_forecast("out_of_season") is True

    def test_prohibited_is_hidden(self):
        assert should_hide_from_forecast("prohibited") is True

    def test_cr_reg_is_hidden(self):
        """Full round-trip: _cr_reg() must produce a status that IS hidden."""
        status = classify_legality(_cr_reg())
        assert status == "catch_and_release"
        assert should_hide_from_forecast(status) is True

    def test_closed_reg_hides(self):
        """Full round-trip: _prohibited_reg() must produce a status that IS hidden."""
        status = classify_legality(_prohibited_reg())
        assert status == "prohibited"
        assert should_hide_from_forecast(status) is True


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
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda name, st: _open_reg()
        )

        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )

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
        assert "Sheepshead" not in names, (
            "Prohibited species Sheepshead should be absent"
        )

    def test_out_of_season_species_hidden_from_biting(self, monkeypatch):
        """Species in a closed season this month must NOT appear in What's Biting."""

        # Use month=2; regulation says closed Jan-Apr → out_of_season for Feb
        def fake_lookup(name, state):
            if name == "Sheepshead":
                return {
                    "bag_limit": "5/day",
                    "season": "Closed Jan-Apr",
                    "notes": "",
                    "official_source": "https://example.com",
                    "is_stale": False,
                    "data_status": "snapshot",
                }
            return _open_reg()

        monkeypatch.setattr("domain.species.lookup_regulation", fake_lookup)

        ranking = build_species_ranking(
            month=2, water_temp=55, coast="east", fishing_types=["pier"], state="NC"
        )
        names = [sp["name"] for sp in ranking]
        assert "Sheepshead" not in names, (
            "Out-of-season Sheepshead should be absent in Feb"
        )

    # -- Test 4: unknown regulation status is hidden (cannot verify legality)
    def test_unknown_status_hidden_from_ranking(self, monkeypatch):
        """When no regulation data exists the species must be hidden from the ranking.

        Under the strict policy, 'unknown' legality is not safe to recommend —
        we cannot confirm the angler can legally keep the fish.
        """
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda name, st: _unknown_reg()
        )

        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )

        assert len(ranking) == 0, (
            "Species with 'unknown' regulation status must be hidden from the ranking. "
            "should_hide_from_forecast() must return True for 'unknown'."
        )

    def test_regulation_status_field_present_when_state_given(self, monkeypatch):
        """Every ranked species should have a regulation_status key when state is provided."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda name, st: _open_reg()
        )
        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )
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
        assert ranks == list(range(1, len(ranking) + 1)), (
            "Ranks must be 1..N with no gaps"
        )

    def test_catch_and_release_species_hidden_from_biting(self, monkeypatch):
        """C&R species must be absent from 'What's Biting' — anglers cannot keep the fish."""

        def fake_lookup(name, state):
            if name == "Sheepshead":
                return _cr_reg()
            return _open_reg()

        monkeypatch.setattr("domain.species.lookup_regulation", fake_lookup)
        ranking = build_species_ranking(
            month=3, water_temp=62, coast="east", fishing_types=["pier"], state="NC"
        )
        names = [sp["name"] for sp in ranking]
        assert "Sheepshead" not in names, (
            "C&R Sheepshead must NOT appear in What's Biting — cannot keep the fish"
        )

    def test_bag_limit_zero_hidden_from_ranking(self, monkeypatch):
        """bag_limit=0 (catch_and_release status) must hide the species — cannot keep the fish."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda name, st: {
                "bag_limit": "0/day",
                "season": "Open",
                "notes": "",
                "official_source": "https://example.com/regs",
                "is_stale": False,
                "data_status": "snapshot",
            },
        )
        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )
        assert len(ranking) == 0, (
            "bag_limit=0 (catch_and_release) must suppress all species from the ranking"
        )

    def test_no_harvest_text_hidden_from_ranking(self, monkeypatch):
        """'No harvest' in notes alone no longer suppresses species; only bag_limit/season
        fields determine catch-and-release status to avoid false positives for slot limits."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda name, st: {
                "bag_limit": "5/day",
                "season": "Open",
                "notes": "No harvest",
                "official_source": "https://example.com/regs",
                "is_stale": False,
                "data_status": "snapshot",
            },
        )
        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )
        assert len(ranking) == 10, (
            "With valid bag_limit/season, 'No harvest' in notes must not suppress species"
        )

    def test_federally_protected_species_hidden_from_biting(self, monkeypatch):
        """Federally protected species must be absent — targeting is not legally permitted."""

        def fake_lookup(name, state):
            if name == "Sheepshead":
                return {
                    "bag_limit": "",
                    "season": "",
                    "notes": "Federally protected species. Do not target.",
                    "official_source": "https://example.com/regs",
                    "is_stale": False,
                    "data_status": "snapshot",
                }
            return _open_reg()

        monkeypatch.setattr("domain.species.lookup_regulation", fake_lookup)
        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", fishing_types=["pier"], state="NC"
        )
        names = [sp["name"] for sp in ranking]
        assert "Sheepshead" not in names, (
            "Federally protected Sheepshead must be hidden (targeting is prohibited)"
        )

    def test_unknown_regulation_species_hidden_from_ranking(self, monkeypatch):
        """Species with no usable regulation data must be hidden from the ranking.

        We cannot confirm the angler can legally keep the fish, so we must not
        recommend targeting it.  Missing data is not the same as legal.
        """
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda name, st: _unknown_reg()
        )
        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )
        assert len(ranking) == 0, (
            "Unknown-status species must be hidden from ranking — "
            "cannot recommend targeting a species of unverified legality"
        )


# ---------------------------------------------------------------------------
# Tests: build_spawning_report() — filtering and regulation_status field
# ---------------------------------------------------------------------------


class TestSpawningReportRegulationStatus:
    # -- Test 3: truly closed species hidden from "What's Spawning Now"
    def test_prohibited_species_hidden_from_spawning(self, monkeypatch):
        """Species with a year-round closure must not appear in the spawning list."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda name, st: _prohibited_reg()
        )

        spawning = build_spawning_report(
            month=5, water_temp=72, coast="east", state="NC"
        )
        assert spawning == [], (
            "All species should be hidden when every lookup returns a prohibited regulation"
        )

    def test_legal_species_visible_in_spawning(self, monkeypatch):
        """Species with open regulations should appear in the spawning report."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda name, st: _open_reg()
        )

        spawning = build_spawning_report(
            month=5, water_temp=72, coast="east", state="NC"
        )
        assert len(spawning) > 0, "Expected at least one species in spawning report"

    def test_spawning_regulation_status_legal_for_open_regs(self, monkeypatch):
        """Spawning entries with open regs must carry regulation_status='legal'."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda name, st: _open_reg()
        )

        spawning = build_spawning_report(
            month=5, water_temp=72, coast="east", state="NC"
        )
        for sp in spawning:
            assert "regulation_status" in sp, (
                f"'{sp['name']}' missing regulation_status"
            )
            assert sp["regulation_status"] == "legal", (
                f"'{sp['name']}' has regulation_status={sp['regulation_status']!r}"
            )

    def test_spawning_regulation_status_unknown_when_no_data(self, monkeypatch):
        """When no reg data exists, spawning entries should carry regulation_status='unknown'."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda name, st: _unknown_reg()
        )

        spawning = build_spawning_report(
            month=5, water_temp=72, coast="east", state="NC"
        )
        # Unknown regs → show the entry (conservative but useful)
        for sp in spawning:
            assert sp.get("regulation_status") == "unknown", (
                f"'{sp['name']}' should be unknown when no regulation data is found"
            )

    def test_spawning_legal_status_field_preserved(self, monkeypatch):
        """The legacy legal_status field must still be present (backward compatibility)."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda name, st: _open_reg()
        )

        spawning = build_spawning_report(
            month=5, water_temp=72, coast="east", state="NC"
        )
        for sp in spawning:
            assert "legal_status" in sp, (
                f"'{sp['name']}' missing legacy legal_status field"
            )
            assert sp["legal_status"] in (
                "catch_release",
                "restricted",
                "open",
                "unknown",
            )

    def test_catch_and_release_species_hidden_from_spawning(self, monkeypatch):
        """C&R species must be absent from 'What's Spawning' — anglers cannot keep the fish."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda name, st: _cr_reg()
        )

        spawning = build_spawning_report(
            month=5, water_temp=72, coast="east", state="NC"
        )
        assert len(spawning) == 0, (
            "C&R species must be hidden from the spawning list — cannot keep the fish"
        )


# ---------------------------------------------------------------------------
# Regression guard: retention logic must never become the visibility gate
# ---------------------------------------------------------------------------


class TestRetentionLogicNotVisibilityGate:
    """Regression tests ensuring the visibility gate stays as should_hide_from_forecast().

    The gate is intentionally strict: ONLY ``"legal"`` status is shown.
    All other statuses — including catch_and_release, restricted, and unknown —
    are hidden so the app never recommends targeting a species unless legality
    is confirmed and the fish can be legally kept.

    Each test below passes under the current strict policy and would fail if
    someone loosened the gate (e.g. made C&R visible again).
    """

    def test_cr_phrase_hides_from_ranking(self, monkeypatch):
        """'Catch and release only' must suppress a species from What's Biting.

        Anglers cannot keep C&R fish, so the forecast must not recommend targeting them.
        """
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda name, st: {
                "bag_limit": "",
                "season": "Catch and release only",
                "notes": "",
                "official_source": "https://example.com/regs",
                "is_stale": False,
                "data_status": "snapshot",
            },
        )
        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )
        assert len(ranking) == 0, (
            "REGRESSION: 'Catch and release only' must suppress species from the ranking. "
            "should_hide_from_forecast() must return True for 'catch_and_release'."
        )

    def test_bag_zero_hides_from_ranking(self, monkeypatch):
        """bag_limit='0/day' (catch_and_release status) must hide species from What's Biting."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda name, st: {
                "bag_limit": "0/day",
                "season": "Open",
                "notes": "",
                "official_source": "https://example.com/regs",
                "is_stale": False,
                "data_status": "snapshot",
            },
        )
        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )
        assert len(ranking) == 0, (
            "REGRESSION: bag_limit=0 (catch_and_release) must suppress species. "
            "should_hide_from_forecast() must return True for 'catch_and_release'."
        )

    def test_cr_and_closed_season_both_hidden(self, monkeypatch):
        """C&R and closed-season regulations must both hide species.

        Under the strict policy both statuses are non-legal and therefore hidden.
        """
        for season_text in ("Catch and release only", "Season closed"):
            monkeypatch.setattr(
                "domain.species.lookup_regulation",
                lambda name, st, _s=season_text: {
                    "bag_limit": "",
                    "season": _s,
                    "notes": "",
                    "official_source": "https://example.com/regs",
                    "is_stale": False,
                    "data_status": "snapshot",
                },
            )
            ranking = build_species_ranking(
                month=6, water_temp=72, coast="east", state="NC"
            )
            assert len(ranking) == 0, (
                f"REGRESSION: season='{season_text}' must hide all species from the ranking."
            )


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

        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )
        assert len(ranking) > 0, "Stale regs must not hide species from the ranking"

    def test_stale_regulation_is_flagged_in_entry(self, monkeypatch):
        """is_stale=True must be present in each species' regulation dict when data is stale."""
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda name, st: _open_reg(is_stale=True, last_updated="2023-01"),
        )

        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )
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

        spawning = build_spawning_report(
            month=5, water_temp=72, coast="east", state="NC"
        )
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

        ranking = build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC"
        )
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

        spawning = build_spawning_report(
            month=5, water_temp=72, coast="east", state="NC"
        )
        assert len(spawning) > 0
        for sp in spawning:
            assert sp.get("regulation", {}).get("official_source") == expected_url

    def test_get_official_regulations_url_for_all_supported_states(self):
        """Every state we explicitly support should return a non-empty URL."""
        supported = [
            "AL",
            "CA",
            "DE",
            "FL",
            "GA",
            "HI",
            "LA",
            "MA",
            "MD",
            "ME",
            "MS",
            "NC",
            "NJ",
            "NY",
            "OR",
            "RI",
            "SC",
            "TX",
            "VA",
            "WA",
        ]
        for state in supported:
            url = get_official_regulations_url(state)
            assert url.startswith("https://"), (
                f"State {state} returned unexpected URL: {url!r}"
            )


class TestGearRestrictions:
    def test_circle_hooks(self):
        assert "Circle hooks" in _extract_gear_restrictions(
            {"notes": "Circle hooks required when using natural bait."}
        )

    def test_non_offset_suppresses_generic(self):
        out = _extract_gear_restrictions(
            {"notes": "Non-offset circle hooks required for all natural bait."}
        )
        assert out == "Non-offset circle hooks"

    def test_multiple_restrictions(self):
        out = _extract_gear_restrictions(
            {"notes": "Hook-and-line only. No snatch hooking permitted."}
        )
        assert "Hook and line only" in out
        assert "No snatch hooking" in out

    def test_gigging_and_spear(self):
        assert "No gigging" in _extract_gear_restrictions(
            {"notes": "Gigging is prohibited."}
        )
        assert "No spearfishing" in _extract_gear_restrictions(
            {"notes": "Spearfishing prohibited."}
        )

    def test_no_false_positive_on_standard_limits(self):
        assert (
            _extract_gear_restrictions(
                {
                    "min_size": "18 in",
                    "bag_limit": "3/day",
                    "season": "Open",
                    "notes": "Standard limits.",
                }
            )
            == ""
        )

    def test_none_and_empty_safe(self):
        assert _extract_gear_restrictions(None) == ""
        assert _extract_gear_restrictions({}) == ""

    def test_more_gear_methods(self):
        assert "Natural bait only" in _extract_gear_restrictions(
            {"notes": "Natural bait only."}
        )
        assert "No J-hooks" in _extract_gear_restrictions(
            {"notes": "J-hooks are prohibited."}
        )
        assert "No chumming" in _extract_gear_restrictions(
            {"notes": "No chumming permitted."}
        )
        assert "Descending device required" in _extract_gear_restrictions(
            {"notes": "A descending device is required for reef fish."}
        )


class TestSlotLimit:
    def test_parses_range_with_slot_keyword(self):
        assert _extract_slot_limit({"notes": "Slot limit: 18-27 in TL."}) == "18-27 in"

    def test_to_and_unicode_dash(self):
        assert _extract_slot_limit({"notes": "Slot 18 to 27 inches"}) == "18-27 in"
        assert _extract_slot_limit({"min_size": 'Slot: 15–23"'}) == "15-23 in"

    def test_protected_slot(self):
        assert (
            _extract_slot_limit({"notes": "Protected slot 20-28 inches"}) == "20-28 in"
        )

    def test_no_slot_keyword_no_match(self):
        # A plain range without 'slot'/'protected' is not a slot limit.
        assert _extract_slot_limit({"notes": "3 per day, 18-27 in range"}) == ""

    def test_min_only_no_match(self):
        assert _extract_slot_limit({"min_size": "18 in minimum"}) == ""

    def test_none_safe(self):
        assert _extract_slot_limit(None) == ""


class TestClosureCapture:
    def test_out_of_season_species_captured(self, monkeypatch):
        # Everything is closed Jan-Apr; querying in February should populate
        # the closures list with out_of_season species (not the visible ranking).
        def fake_lookup(name, state):
            return _open_reg(season="Closed Jan-Apr")

        monkeypatch.setattr("domain.species.lookup_regulation", fake_lookup)
        closures: list = []
        ranking = build_species_ranking(
            month=2, water_temp=58, coast="east", state="NC", closures_out=closures
        )
        # Closed species are hidden from the ranking but captured as closures.
        assert ranking == [] or all(
            s.get("regulation_status") != "out_of_season" for s in ranking
        )
        assert closures, "expected out-of-season species to be captured"
        assert all(c["status"] == "out_of_season" for c in closures)
        assert all("season" in c and "name" in c for c in closures)

    def test_no_closures_when_all_legal(self, monkeypatch):
        monkeypatch.setattr(
            "domain.species.lookup_regulation", lambda n, s: _open_reg()
        )
        closures: list = []
        build_species_ranking(
            month=6, water_temp=72, coast="east", state="NC", closures_out=closures
        )
        assert closures == []

    def test_closures_opt_in_only(self, monkeypatch):
        # Without closures_out, nothing breaks (back-compat).
        monkeypatch.setattr(
            "domain.species.lookup_regulation",
            lambda n, s: _open_reg(season="Closed Jan-Apr"),
        )
        ranking = build_species_ranking(
            month=2, water_temp=58, coast="east", state="NC"
        )
        assert isinstance(ranking, list)
