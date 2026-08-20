"""Regenerate tests/openapi_snapshot.json, the API's breaking-change guard.

Run this after a deliberate route/schema change, then review the diff
before committing — an unreviewed regeneration defeats the point of a
snapshot test (docs/PR_GOVERNANCE.md's AI review contract: every change
needs a stated reason), same rationale as
scripts/generate_schema_snapshots.py.

    python -m scripts.generate_openapi_snapshot
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "openapi_snapshot.json"
)


def main() -> None:
    SNAPSHOT_PATH.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
