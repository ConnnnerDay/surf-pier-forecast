"""Tests for storage.species_loader."""

from __future__ import annotations

import json
import pathlib

import pytest

from storage.species_loader import (
    SPECIES_DB,
    _REQUIRED_FIELDS,
    _VALID_COASTS,
    load_species_db,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(tmp_path: pathlib.Path, data) -> pathlib.Path:
    p = tmp_path / "species_data.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _minimal_entry(**overrides) -> dict:
    """Return a valid minimal species entry, optionally overriding fields."""
    base = {
        "name": "Test fish",
        "temp_min": 50, "temp_max": 80,
        "temp_ideal_low": 55, "temp_ideal_high": 75,
        "peak_months": [3, 4, 5],
        "good_months": [2, 6],
        "bait": "Shrimp",
        "rig": "Fish finder rig",
        "hook_size": "2/0 circle hook",
        "sinker": "2 oz pyramid",
        "explanation_cold": "Hides in deep holes.",
        "explanation_warm": "Active in the surf.",
        "coast": "east",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Module-level SPECIES_DB
# ---------------------------------------------------------------------------

class TestModuleLevelDB:
    def test_loaded_at_import(self):
        """SPECIES_DB must be a non-empty list available at import time."""
        assert isinstance(SPECIES_DB, list)
        assert len(SPECIES_DB) > 0

    def test_count_matches_known_total(self):
        """Expect 307 entries — catches accidental data truncation."""
        assert len(SPECIES_DB) == 307

    def test_all_required_fields_present(self):
        for sp in SPECIES_DB:
            missing = _REQUIRED_FIELDS - sp.keys()
            assert not missing, f"'{sp.get('name')}' missing: {missing}"

    def test_all_coasts_valid(self):
        for sp in SPECIES_DB:
            assert sp["coast"] in _VALID_COASTS, (
                f"'{sp['name']}' has invalid coast '{sp['coast']}'"
            )

    def test_three_coasts_represented(self):
        coasts = {sp["coast"] for sp in SPECIES_DB}
        assert coasts == {"east", "west", "hawaii"}

    def test_regions_entries_are_lists_of_strings(self):
        for sp in SPECIES_DB:
            if "regions" in sp:
                assert isinstance(sp["regions"], list), (
                    f"'{sp['name']}' regions is not a list"
                )
                assert all(isinstance(r, str) for r in sp["regions"]), (
                    f"'{sp['name']}' regions contains non-string"
                )

    def test_temperature_bounds_sane(self):
        for sp in SPECIES_DB:
            assert sp["temp_min"] < sp["temp_max"], (
                f"'{sp['name']}': temp_min >= temp_max"
            )
            assert sp["temp_ideal_low"] >= sp["temp_min"]
            assert sp["temp_ideal_high"] <= sp["temp_max"]

    def test_months_are_valid_integers(self):
        for sp in SPECIES_DB:
            for field in ("peak_months", "good_months"):
                for m in sp[field]:
                    assert isinstance(m, int) and 1 <= m <= 12, (
                        f"'{sp['name']}' {field} contains bad month {m!r}"
                    )

    def test_first_entry_is_red_drum(self):
        """Ordering sanity: Red drum should be first (east coast, entry #1)."""
        assert SPECIES_DB[0]["name"].startswith("Red drum")

    def test_last_entry_is_hawaii(self):
        assert SPECIES_DB[-1]["coast"] == "hawaii"


# ---------------------------------------------------------------------------
# load_species_db() — happy path
# ---------------------------------------------------------------------------

class TestLoadSpeciesDbValid:
    def test_returns_list(self, tmp_path):
        p = _write_json(tmp_path, [_minimal_entry()])
        result = load_species_db(path=p)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_entry_contents_preserved(self, tmp_path):
        entry = _minimal_entry(name="Custom fish", coast="west", regions=["norcal"])
        p = _write_json(tmp_path, [entry])
        result = load_species_db(path=p)
        sp = result[0]
        assert sp["name"] == "Custom fish"
        assert sp["coast"] == "west"
        assert sp["regions"] == ["norcal"]

    def test_new_species_via_json_only(self, tmp_path):
        """Adding an entry to JSON surfaces in the returned DB without code changes."""
        entries = [_minimal_entry(name="Brand new species", coast="east")]
        p = _write_json(tmp_path, entries)
        result = load_species_db(path=p)
        names = [sp["name"] for sp in result]
        assert "Brand new species" in names

    def test_entry_without_regions_is_valid(self, tmp_path):
        entry = _minimal_entry()
        assert "regions" not in entry
        p = _write_json(tmp_path, [entry])
        result = load_species_db(path=p)
        assert "regions" not in result[0]

    def test_hawaii_coast_valid(self, tmp_path):
        p = _write_json(tmp_path, [_minimal_entry(coast="hawaii")])
        result = load_species_db(path=p)
        assert result[0]["coast"] == "hawaii"


# ---------------------------------------------------------------------------
# load_species_db() — error paths (fail fast, helpful messages)
# ---------------------------------------------------------------------------

class TestLoadSpeciesDbErrors:
    def test_file_not_found(self, tmp_path):
        missing = tmp_path / "no_such_file.json"
        with pytest.raises(FileNotFoundError, match="not found"):
            load_species_db(path=missing)

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "species_data.json"
        p.write_text("{ this is not json }", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            load_species_db(path=p)

    def test_empty_array_raises(self, tmp_path):
        p = _write_json(tmp_path, [])
        with pytest.raises(ValueError, match="non-empty"):
            load_species_db(path=p)

    def test_top_level_not_array_raises(self, tmp_path):
        p = _write_json(tmp_path, {"name": "oops"})
        with pytest.raises(ValueError, match="non-empty"):
            load_species_db(path=p)

    def test_missing_required_field_raises(self, tmp_path):
        entry = _minimal_entry()
        del entry["bait"]
        p = _write_json(tmp_path, [entry])
        with pytest.raises(ValueError, match="bait"):
            load_species_db(path=p)

    def test_error_message_includes_species_name(self, tmp_path):
        entry = _minimal_entry(name="Spotty McSpotface")
        del entry["rig"]
        p = _write_json(tmp_path, [entry])
        with pytest.raises(ValueError, match="Spotty McSpotface"):
            load_species_db(path=p)

    def test_invalid_coast_raises(self, tmp_path):
        p = _write_json(tmp_path, [_minimal_entry(coast="lake")])
        with pytest.raises(ValueError, match="coast"):
            load_species_db(path=p)

    def test_temp_min_greater_than_max_raises(self, tmp_path):
        p = _write_json(tmp_path, [_minimal_entry(temp_min=90, temp_max=50)])
        with pytest.raises(ValueError, match="temp_min"):
            load_species_db(path=p)

    def test_non_numeric_temp_raises(self, tmp_path):
        p = _write_json(tmp_path, [_minimal_entry(temp_min="warm")])
        with pytest.raises(ValueError, match="temp_min"):
            load_species_db(path=p)

    def test_months_not_list_raises(self, tmp_path):
        p = _write_json(tmp_path, [_minimal_entry(peak_months="march")])
        with pytest.raises(ValueError, match="peak_months"):
            load_species_db(path=p)

    def test_invalid_month_value_raises(self, tmp_path):
        p = _write_json(tmp_path, [_minimal_entry(peak_months=[0, 3, 13])])
        with pytest.raises(ValueError, match="peak_months"):
            load_species_db(path=p)

    def test_regions_not_list_raises(self, tmp_path):
        p = _write_json(tmp_path, [_minimal_entry(regions="northeast")])
        with pytest.raises(ValueError, match="regions"):
            load_species_db(path=p)
