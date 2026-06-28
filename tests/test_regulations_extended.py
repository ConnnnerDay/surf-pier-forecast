"""Extended tests for regulations.py covering missed branches.

Targets lines: 44-45, 128, 148, 152-154, 156, 178, 183, 205, 319,
               467, 506-507, 522, 526-528, 620, 640, 662, 671-680.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import regulations as reg_module
from regulations import (
    _bg_refresh_regulation,
    _load_data_file,
    _lookup_regulation_impl,
    _months_since,
    _schedule_reg_refresh,
    classify_legality,
    lookup_regulation,
    season_status,
)


# ---------------------------------------------------------------------------
# Lines 44-45: _months_since exception handler
# ---------------------------------------------------------------------------


class TestMonthsSince:
    def test_valid_date_returns_float(self):
        result = _months_since("2025-01")
        assert isinstance(result, (int, float))

    def test_bad_string_returns_inf(self):
        assert _months_since("not-a-date") == float("inf")

    def test_empty_string_returns_inf(self):
        assert _months_since("") == float("inf")

    def test_none_returns_inf(self):
        assert _months_since(None) == float("inf")


# ---------------------------------------------------------------------------
# Line 128: empty-name entry skipped in _build_default_name_map
# Lines 148, 152-154, 156, 178, 183: _load_data_file branches
# ---------------------------------------------------------------------------


class TestLoadDataFile:
    def test_empty_name_in_species_db_is_skipped(self, monkeypatch, tmp_path):
        """Line 128: SPECIES_DB entry with empty/None name is skipped."""
        monkeypatch.setattr(
            "regulations.SPECIES_DB",
            [{"name": ""}, {"name": None}, {"name": "Red Drum"}],
        )
        monkeypatch.setattr(
            "regulations._resolve_path", lambda: tmp_path / "nonexistent.json"
        )
        data = _load_data_file()
        # Empty/None names must not appear as keys
        assert "" not in data.name_map
        assert "Red Drum" in data.name_map

    def test_missing_file_returns_empty_data(self, monkeypatch, tmp_path):
        """Line 148: when the regulations file does not exist, return empty _RegData."""
        monkeypatch.setattr(
            "regulations._resolve_path", lambda: tmp_path / "nonexistent.json"
        )
        data = _load_data_file()
        assert data.states == {}
        assert data.last_updated == ""

    def test_invalid_json_returns_empty_data(self, monkeypatch, tmp_path):
        """Lines 152-154: malformed JSON file → exception caught, return empty _RegData."""
        bad = tmp_path / "regs.json"
        bad.write_text("{invalid json!!!")
        monkeypatch.setattr("regulations._resolve_path", lambda: bad)
        data = _load_data_file()
        assert data.states == {}

    def test_non_dict_json_returns_empty_data(self, monkeypatch, tmp_path):
        """Line 156: JSON root is a list (not a dict) → return empty _RegData."""
        f = tmp_path / "regs.json"
        f.write_text(json.dumps([1, 2, 3]))
        monkeypatch.setattr("regulations._resolve_path", lambda: f)
        data = _load_data_file()
        assert data.states == {}

    def test_non_dict_state_value_skipped(self, monkeypatch, tmp_path):
        """Line 178: state entry whose value is not a dict is skipped."""
        content = {
            "states": {
                "NC": "not-a-dict",  # triggers line 178
                "SC": {
                    "flounder": {
                        "bag_limit": "10",
                        "min_size": "",
                        "season": "",
                        "notes": "",
                        "source": "",
                    }
                },
            }
        }
        f = tmp_path / "regs.json"
        f.write_text(json.dumps(content))
        monkeypatch.setattr("regulations._resolve_path", lambda: f)
        data = _load_data_file()
        # SC is valid; NC value was not a dict → skipped
        assert "SC" in data.states
        assert "NC" not in data.states

    def test_non_dict_species_details_skipped(self, monkeypatch, tmp_path):
        """Line 183: species entry whose details value is not a dict is skipped."""
        content = {
            "states": {
                "NC": {
                    "red_drum": "not-a-dict",  # triggers line 183
                    "flounder": {
                        "bag_limit": "10",
                        "min_size": "",
                        "season": "",
                        "notes": "",
                        "source": "",
                    },
                }
            }
        }
        f = tmp_path / "regs.json"
        f.write_text(json.dumps(content))
        monkeypatch.setattr("regulations._resolve_path", lambda: f)
        data = _load_data_file()
        assert "NC" in data.states
        assert "flounder" in data.states["NC"]
        assert "red_drum" not in data.states["NC"]


# ---------------------------------------------------------------------------
# Line 205: inner double-check in _ensure_data_loaded (concurrent reload guard)
# ---------------------------------------------------------------------------


class TestEnsureDataLoadedInnerCheck:
    def test_inner_check_returns_early_after_concurrent_reload(self, monkeypatch):
        """Line 205: when _LAST_LOADED_MONO is updated between the outer and inner
        monotonic() checks (as if a concurrent thread reloaded), the inner guard
        returns early without calling _load_data_file."""
        calls = [0]

        def fake_mono():
            calls[0] += 1
            if calls[0] == 1:
                # Outer check: make it look expired so we proceed to the lock
                return 1.0  # 1.0 - (-1000) = 1001 >= 300 → outer passes
            else:
                # Simulate a concurrent thread having reloaded before we got the lock
                reg_module._LAST_LOADED_MONO = 1.0
                return 1.0  # 1.0 - 1.0 = 0 < 300 → inner check returns at line 205

        monkeypatch.setattr("regulations.monotonic", fake_mono)
        monkeypatch.setattr(reg_module, "_LAST_LOADED_MONO", -1000.0)
        reg_module._ensure_data_loaded()
        assert calls[0] == 2  # both outer and inner monotonic() were called


# ---------------------------------------------------------------------------
# Line 319: season_status falls through to "unknown"
# ---------------------------------------------------------------------------


class TestSeasonStatusUnknown:
    def test_text_without_open_keyword_returns_unknown(self):
        """Line 319: season text that has no year-round phrase, no closed/open month
        range, and no 'open' keyword → 'unknown'."""
        reg = {"season": "Check regulations before fishing", "notes": "Size limit applies"}
        assert season_status(reg, 6) == "unknown"


# ---------------------------------------------------------------------------
# Line 467: classify_legality permit-required path
# ---------------------------------------------------------------------------


class TestClassifyLegalityPermitRequired:
    def test_harvest_tag_required_returns_restricted(self):
        """Line 467: 'harvest tag required' in notes triggers 'restricted'."""
        reg = {
            "bag_limit": "2/day",
            "season": "Open",
            "notes": "Harvest tag required to keep fish.",
        }
        assert classify_legality(reg) == "restricted"

    def test_harvest_permit_required_returns_restricted(self):
        reg = {"bag_limit": "1/day", "season": "Open", "notes": "Harvest permit required."}
        assert classify_legality(reg) == "restricted"

    def test_tag_required_to_harvest_returns_restricted(self):
        reg = {"bag_limit": "1", "season": "Open", "notes": "Tag required to harvest."}
        assert classify_legality(reg) == "restricted"


# ---------------------------------------------------------------------------
# Line 457: bag_limit startswith("0 ") / "0—" / "0 —" → catch_and_release
# ---------------------------------------------------------------------------


class TestClassifyLegalityExplicitZeroBagLimit:
    def test_zero_dash_phrase_returns_catch_and_release(self):
        """Line 457: bag_limit '0 — prohibited' is not in the exact-match set but
        startswith('0 '), so it still triggers catch_and_release."""
        reg = {"bag_limit": "0 — prohibited", "season": "Open", "notes": ""}
        assert classify_legality(reg) == "catch_and_release"

    def test_zero_em_dash_returns_catch_and_release(self):
        reg = {"bag_limit": "0—closed", "season": "Open", "notes": ""}
        assert classify_legality(reg) == "catch_and_release"


# ---------------------------------------------------------------------------
# Lines 506-507: _bg_refresh_regulation exception handler
# ---------------------------------------------------------------------------


class TestBgRefreshRegulation:
    def test_scrape_exception_is_caught_and_pending_cleared(self, monkeypatch):
        """Lines 506-507: exception from _scrape_regulation is caught and logged;
        the pending entry is removed from _reg_refresh_pending in the finally block."""
        monkeypatch.setattr(
            "regulations._scrape_regulation", lambda *a: 1 / 0
        )
        reg_module._reg_refresh_pending.add(("TestFish", "NC"))
        _bg_refresh_regulation("TestFish", "NC")
        assert ("TestFish", "NC") not in reg_module._reg_refresh_pending

    def test_successful_scrape_clears_pending(self, monkeypatch):
        """_bg_refresh_regulation success path also clears pending set."""
        monkeypatch.setattr(
            "regulations._scrape_regulation", lambda *a: {"bag_limit": "3"}
        )
        reg_module._reg_refresh_pending.add(("TestFish2", "NC"))
        _bg_refresh_regulation("TestFish2", "NC")
        assert ("TestFish2", "NC") not in reg_module._reg_refresh_pending


# ---------------------------------------------------------------------------
# Line 522: _schedule_reg_refresh early return when already pending
# Lines 526-528: RuntimeError from executor.submit is caught
# ---------------------------------------------------------------------------


class TestScheduleRegRefresh:
    def test_already_pending_skips_submit(self, monkeypatch):
        """Line 522: if (species, state) is already in _reg_refresh_pending, return early."""
        mock_exec = MagicMock()
        monkeypatch.setattr("regulations._reg_bg_executor", mock_exec)
        reg_module._reg_refresh_pending.add(("SkipFish", "NC"))
        try:
            _schedule_reg_refresh("SkipFish", "NC")
            mock_exec.submit.assert_not_called()
        finally:
            reg_module._reg_refresh_pending.discard(("SkipFish", "NC"))

    def test_executor_runtime_error_removes_from_pending(self, monkeypatch):
        """Lines 526-528: RuntimeError from executor.submit is caught; key removed."""
        reg_module._reg_refresh_pending.discard(("ErrorFish", "NC"))

        def _boom(*_a):
            raise RuntimeError("executor shut down")

        mock_exec = MagicMock(submit=_boom)
        monkeypatch.setattr("regulations._reg_bg_executor", mock_exec)
        _schedule_reg_refresh("ErrorFish", "NC")
        # Must have been removed from the pending set
        assert ("ErrorFish", "NC") not in reg_module._reg_refresh_pending


# ---------------------------------------------------------------------------
# Line 620: lookup_regulation gear-restriction enrichment
# ---------------------------------------------------------------------------


class TestLookupRegulationGearEnrichment:
    def test_gear_restriction_added_to_payload(self, monkeypatch):
        """Line 620: when gear restrictions are detected, payload gains a 'gear' key."""

        def _mock_impl(species, state):
            return {
                "bag_limit": "3/day",
                "season": "Open",
                "notes": "Non-offset circle hooks required.",
                "official_source": "https://example.com",
                "min_size": "",
            }

        monkeypatch.setattr("regulations._lookup_regulation_impl", _mock_impl)
        result = lookup_regulation("Red Drum", "NC")
        assert result is not None
        assert "gear" in result
        assert result["gear"] != ""


# ---------------------------------------------------------------------------
# Line 640: _lookup_regulation_impl returns None for empty state
# ---------------------------------------------------------------------------


class TestLookupRegulationImplEmptyState:
    def test_empty_state_string_returns_none(self):
        """Line 640: an empty (or whitespace-only) state string returns None."""
        assert _lookup_regulation_impl("Red Drum", "") is None
        assert _lookup_regulation_impl("Red Drum", "  ") is None


# ---------------------------------------------------------------------------
# Line 662: official_source fallback in the live-stale data path
# ---------------------------------------------------------------------------


class TestLookupRegulationImplOfficialSourceFallback:
    def test_official_source_filled_when_stale_data_clears_it(self, monkeypatch):
        """Line 662: stale_data that sets official_source='' gets the fallback URL."""
        stale_data = {"bag_limit": "3", "official_source": ""}  # empty official_source
        monkeypatch.setattr(
            "regulations._get_regulation_stale", lambda *a: (stale_data, False)
        )
        monkeypatch.setattr("regulations._schedule_reg_refresh", lambda *a: None)
        result = _lookup_regulation_impl("Red Drum", "NC")
        assert result is not None
        assert result.get("official_source")  # must be filled in


# ---------------------------------------------------------------------------
# Lines 671-680: blocking scrape (no-cache path) — success and exception
# ---------------------------------------------------------------------------


class TestLookupRegulationImplBlockingScrape:
    def test_blocking_scrape_success_returns_live_payload(self, monkeypatch):
        """Lines 671-678: when there is nothing in cache, a blocking scrape is done;
        on success the payload carries data_status='live'."""
        monkeypatch.setattr(
            "regulations._get_regulation_stale", lambda *a: (None, False)
        )
        monkeypatch.setattr(
            "regulations._scrape_regulation",
            lambda *a: {"bag_limit": "3", "min_size": "15 in", "season": "Open"},
        )
        result = _lookup_regulation_impl("Red Drum", "NC")
        assert result is not None
        assert result["data_status"] == "live"
        assert result["is_stale"] is False

    def test_blocking_scrape_fills_official_source_when_scraped_clears_it(
        self, monkeypatch
    ):
        """Lines 674-677: if scraped result clears official_source, fallback URL is used."""
        monkeypatch.setattr(
            "regulations._get_regulation_stale", lambda *a: (None, False)
        )
        monkeypatch.setattr(
            "regulations._scrape_regulation",
            lambda *a: {"bag_limit": "3", "official_source": ""},  # clears it
        )
        result = _lookup_regulation_impl("Red Drum", "NC")
        assert result is not None
        assert result.get("official_source")  # fallback applied

    def test_scraper_exception_falls_back_to_snapshot_or_link_only(self, monkeypatch):
        """Lines 679-680: if scraper raises, the exception is caught and we fall through
        to the static snapshot / link-only payload."""

        def _boom(*a):
            raise RuntimeError("network error")

        monkeypatch.setattr("regulations._get_regulation_stale", _boom)
        result = _lookup_regulation_impl("Red Drum", "NC")
        assert result is not None  # always returns at least a link-only payload


# ---------------------------------------------------------------------------
# Line 556: _extract_slot_limit success return
# Line 602: _extract_gear_restrictions empty-text early return
# Line 624: slot enrichment in lookup_regulation
# ---------------------------------------------------------------------------


from regulations import _extract_gear_restrictions, _extract_slot_limit


class TestExtractSlotLimit:
    def test_slot_limit_returned_when_slot_text_present(self):
        """Line 556: when 'slot' keyword + numeric range are found, return the range."""
        reg = {
            "notes": "Slot limit: 18-27 in. Fish outside slot must be released.",
            "min_size": "",
            "bag_limit": "",
            "season": "",
        }
        result = _extract_slot_limit(reg)
        assert result == "18-27 in"

    def test_slot_keyword_present_but_no_numeric_range_returns_empty(self):
        """Line 556: 'slot' in text but regex finds no numeric range → return ''."""
        reg = {
            "notes": "Slot restrictions apply; check local rules.",
            "min_size": "",
            "bag_limit": "",
            "season": "",
        }
        assert _extract_slot_limit(reg) == ""


class TestExtractGearRestrictionsEmptyText:
    def test_all_empty_fields_returns_empty(self):
        """Line 602: when all reg fields are empty, the joined text is blank → return ''."""
        reg = {"min_size": "", "bag_limit": "", "season": "", "notes": ""}
        assert _extract_gear_restrictions(reg) == ""


class TestLookupRegulationSlotEnrichment:
    def test_slot_added_to_payload(self, monkeypatch):
        """Line 624: when a slot limit is detected, payload gains a 'slot' key."""

        def _mock_impl(species, state):
            return {
                "bag_limit": "5/day",
                "season": "Open",
                "notes": "Slot limit: 18-27 in. Fish outside slot must be released.",
                "min_size": "",
                "official_source": "https://example.com",
            }

        monkeypatch.setattr("regulations._lookup_regulation_impl", _mock_impl)
        result = lookup_regulation("Red Drum", "NC")
        assert result is not None
        assert "slot" in result
        assert result["slot"] == "18-27 in"
