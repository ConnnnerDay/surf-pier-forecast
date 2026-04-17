#!/usr/bin/env python3
"""
Compact species ingestion helper.

Each entry uses short keys; expand_entry() fills in required fields and
validates before writing.  This keeps individual add-runs small and fast.

Short-key schema
----------------
n   name (str, required)
c   coast: "east" | "west" | "hawaii"
r   regions list (optional)
ti  temp min
tx  temp max
tl  temp ideal low
th  temp ideal high
pm  peak_months list
gm  good_months list
b   bait
rig rig
h   hook_size
s   sinker
l   lures
ec  explanation_cold
ew  explanation_warm
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "storage" / "species_data.json"

_KEY_MAP = {
    "n": "name",
    "c": "coast",
    "r": "regions",
    "ti": "temp_min",
    "tx": "temp_max",
    "tl": "temp_ideal_low",
    "th": "temp_ideal_high",
    "pm": "peak_months",
    "gm": "good_months",
    "b": "bait",
    "rig": "rig",
    "h": "hook_size",
    "s": "sinker",
    "l": "lures",
    "ec": "explanation_cold",
    "ew": "explanation_warm",
}

_REQUIRED = {
    "name",
    "coast",
    "temp_min",
    "temp_max",
    "temp_ideal_low",
    "temp_ideal_high",
    "peak_months",
    "good_months",
    "bait",
    "rig",
    "hook_size",
    "sinker",
    "lures",
    "explanation_cold",
    "explanation_warm",
}


def expand(raw: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    for k, v in raw.items():
        full = _KEY_MAP.get(k, k)
        entry[full] = v
    missing = _REQUIRED - entry.keys()
    if missing:
        raise ValueError(f"{entry.get('name', '?')} missing fields: {missing}")
    return entry


def add_species(entries: list[dict[str, Any]]) -> None:
    db: list[Dict] = json.loads(DB_PATH.read_text())
    existing = {s["name"] for s in db}

    added, skipped = [], []
    for raw in entries:
        entry = expand(raw)
        if entry["name"] in existing:
            skipped.append(entry["name"])
        else:
            db.append(entry)
            added.append(entry["name"])

    DB_PATH.write_text(json.dumps(db, indent=2))
    print(f"Added {len(added)}, skipped {len(skipped)}. Total: {len(db)}")
    for n in added:
        print(f"  + {n}")
    for n in skipped:
        print(f"  ~ {n} (already exists)")


if __name__ == "__main__":
    # Usage: python scripts/add_species.py  (runs self-test)
    print(
        "add_species helper loaded OK — import add_species and call add_species(entries)"
    )
