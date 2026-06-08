"""Validation tests for species data integrity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_CLASSIFICATIONS_PATH = Path(__file__).parent.parent / "storage" / "species_classifications.json"
_REQUIRED_SPECIES_FIELDS = {
    "name", "temp_min", "temp_max", "temp_ideal_low", "temp_ideal_high",
    "peak_months", "good_months", "bait", "rig", "explanation_cold",
    "explanation_warm", "coast",
}


@pytest.fixture(scope="module")
def species_db():
    from storage.species_loader import SPECIES_DB
    return SPECIES_DB


@pytest.fixture(scope="module")
def classifications():
    with _CLASSIFICATIONS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def test_no_duplicate_species_names(species_db):
    """No two entries in the species DB share the same name (case-insensitive)."""
    names = [sp["name"].lower() for sp in species_db]
    seen = set()
    duplicates = []
    for name in names:
        if name in seen:
            duplicates.append(name)
        seen.add(name)
    assert not duplicates, f"Duplicate species names found: {duplicates}"


def test_required_fields_present(species_db):
    """Every species entry has all required fields."""
    missing = []
    for sp in species_db:
        absent = _REQUIRED_SPECIES_FIELDS - sp.keys()
        if absent:
            missing.append((sp.get("name", "<unnamed>"), sorted(absent)))
    assert not missing, f"Species missing required fields: {missing[:10]}"


def test_no_duplicate_classification_entries(classifications):
    """No list in species_classifications.json contains duplicate entries."""
    duplicates = {}
    for section_key, section in classifications.items():
        if isinstance(section, dict):
            for key, lst in section.items():
                if not isinstance(lst, list):
                    continue
                seen: set = set()
                dups = []
                for item in lst:
                    if item in seen:
                        dups.append(item)
                    seen.add(item)
                if dups:
                    duplicates[f"{section_key}.{key}"] = dups
        elif isinstance(section, list):
            seen = set()
            dups = []
            for item in section:
                if item in seen:
                    dups.append(item)
                seen.add(item)
            if dups:
                duplicates[section_key] = dups
    assert not duplicates, f"Duplicate entries in classifications: {duplicates}"


def test_classifications_reference_known_species(classifications, species_db):
    """Every species name in the classification lists exists in the species DB."""
    known = {sp["name"].lower() for sp in species_db}
    unknown = []
    for section_key, section in classifications.items():
        if section_key == "species_categories":
            items = list(section.keys())
        elif isinstance(section, dict):
            items = [name for lst in section.values() if isinstance(lst, list) for name in lst]
        elif isinstance(section, list):
            items = section
        else:
            continue
        for name in items:
            if isinstance(name, str) and name.lower() not in known:
                unknown.append((section_key, name))
    assert not unknown, (
        f"{len(unknown)} classification entries not found in species DB "
        f"(first 20): {unknown[:20]}"
    )
