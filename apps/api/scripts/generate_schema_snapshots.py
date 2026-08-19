"""Regenerate the JSON schema snapshots under tests/schema_snapshots/.

Run this after a deliberate change to a domain model, then review the diff
before committing — an unreviewed regeneration defeats the point of a
snapshot test (docs/PR_GOVERNANCE.md's AI review contract: every change
needs a stated reason).

    python -m scripts.generate_schema_snapshots
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from app.domain.models import (
    Confidence,
    ForecastEnvelope,
    Location,
    Observation,
    SourceStatus,
    Warning,
)

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "tests" / "schema_snapshots"

MODELS: list[type[BaseModel]] = [
    Location,
    Observation,
    SourceStatus,
    Confidence,
    Warning,
    ForecastEnvelope,
]


def main() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        path = SNAPSHOT_DIR / f"{model.__name__}.json"
        path.write_text(
            json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        )
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
