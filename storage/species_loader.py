"""Load and validate the species database from JSON.

The canonical species data lives in storage/species_data.json.
Import SPECIES_DB from this module; do NOT import it from domain.species.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

_JSON_PATH = pathlib.Path(__file__).parent / "species_data.json"

_REQUIRED_FIELDS: frozenset = frozenset({
    "name",
    "temp_min", "temp_max", "temp_ideal_low", "temp_ideal_high",
    "peak_months", "good_months",
    "bait", "rig", "hook_size", "sinker",
    "explanation_cold", "explanation_warm",
    "coast",
})

_VALID_COASTS: frozenset = frozenset({"east", "west", "hawaii"})


def _validate(entries: List[Dict[str, Any]]) -> None:
    """Raise ValueError with a descriptive message if any entry is malformed."""
    if not isinstance(entries, list) or len(entries) == 0:
        raise ValueError("species_data.json must be a non-empty JSON array")

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry #{i} is not a JSON object")

        name = entry.get("name", f"<entry #{i}>")
        missing = _REQUIRED_FIELDS - entry.keys()
        if missing:
            raise ValueError(
                f"Species '{name}' is missing required fields: "
                f"{', '.join(sorted(missing))}"
            )

        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Entry #{i}: 'name' must be a non-empty string")

        coast = entry["coast"]
        if coast not in _VALID_COASTS:
            raise ValueError(
                f"Species '{name}': 'coast' must be one of "
                f"{sorted(_VALID_COASTS)}, got '{coast}'"
            )

        for field in ("temp_min", "temp_max", "temp_ideal_low", "temp_ideal_high"):
            if not isinstance(entry[field], (int, float)):
                raise ValueError(
                    f"Species '{name}': '{field}' must be numeric, "
                    f"got {type(entry[field]).__name__}"
                )

        if entry["temp_min"] > entry["temp_max"]:
            raise ValueError(
                f"Species '{name}': temp_min ({entry['temp_min']}) "
                f"> temp_max ({entry['temp_max']})"
            )

        for field in ("peak_months", "good_months"):
            val = entry[field]
            if not isinstance(val, list):
                raise ValueError(
                    f"Species '{name}': '{field}' must be a JSON array, "
                    f"got {type(val).__name__}"
                )
            for m in val:
                if not isinstance(m, int) or not (1 <= m <= 12):
                    raise ValueError(
                        f"Species '{name}': '{field}' contains invalid month {m!r}"
                    )

        if "regions" in entry:
            regions = entry["regions"]
            if not isinstance(regions, list) or not all(isinstance(r, str) for r in regions):
                raise ValueError(
                    f"Species '{name}': 'regions' must be a list of strings"
                )


def load_species_db(path: pathlib.Path | None = None) -> List[Dict[str, Any]]:
    """Read, parse, and validate the species JSON file.

    Parameters
    ----------
    path:
        Override the default JSON path (useful in tests).

    Raises
    ------
    FileNotFoundError
        If the JSON file does not exist.
    ValueError
        If the JSON is malformed or any entry fails validation.
    """
    resolved = path or _JSON_PATH
    try:
        raw = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Species data file not found: {resolved}\n"
            "Ensure storage/species_data.json is present in the project root."
        )

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"species_data.json contains invalid JSON: {exc}"
        ) from exc

    _validate(entries)
    return entries


# Module-level singleton — loaded once at import time.
SPECIES_DB: List[Dict[str, Any]] = load_species_db()
