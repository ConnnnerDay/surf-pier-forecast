#!/usr/bin/env python3
"""One-time migration: import legacy JSON forecast files into the SQLite database.

Run once after upgrading to the SQLite-backed forecast cache:

    python migrate.py

Safe to run multiple times -- uses INSERT OR REPLACE so existing rows
are overwritten with the JSON data.
"""

from __future__ import annotations

import glob
import json
import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from storage.db import init_db, save_forecast_to_db

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def migrate_json_forecasts() -> int:
    """Scan data/ for forecast_*.json files and insert them into the DB."""
    init_db()

    pattern = os.path.join(DATA_DIR, "forecast_*.json")
    files = glob.glob(pattern)

    # Also check for the bare forecast.json (no location suffix)
    bare = os.path.join(DATA_DIR, "forecast.json")
    if os.path.exists(bare) and bare not in files:
        files.append(bare)

    migrated = 0
    for path in sorted(files):
        fname = os.path.basename(path)
        # Extract location_id from filename: forecast_<loc_id>.json
        if fname == "forecast.json":
            # No location_id -- skip (can't store without a key)
            print(f"  skip  {fname} (no location_id)")
            continue

        loc_id = fname.replace("forecast_", "").replace(".json", "")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            save_forecast_to_db(loc_id, data)
            generated = data.get("generated_at", "unknown")
            print(f"  ok    {loc_id} (generated {generated})")
            migrated += 1
        except Exception as exc:
            print(f"  FAIL  {fname}: {exc}")

    return migrated


if __name__ == "__main__":
    print("Migrating JSON forecast files to SQLite...")
    count = migrate_json_forecasts()
    print(f"\nDone. Migrated {count} forecast(s).")
    print("JSON files are kept as backups. You can delete them once verified.")
