"""The API's breaking-change guard (sprint 25).

Mirrors test_domain_models.py's test_schema_matches_snapshot: the
committed tests/openapi_snapshot.json is the API's public contract.
Any route/schema change that isn't a deliberate, reviewed regeneration
(scripts/generate_openapi_snapshot.py) fails this test.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app

SNAPSHOT_PATH = Path(__file__).resolve().parent / "openapi_snapshot.json"


def test_openapi_schema_matches_snapshot() -> None:
    assert SNAPSHOT_PATH.exists(), (
        "missing tests/openapi_snapshot.json — run "
        "`python -m scripts.generate_openapi_snapshot` from apps/api and commit the result"
    )
    expected = json.loads(SNAPSHOT_PATH.read_text())
    actual = app.openapi()
    assert actual == expected, (
        "The API's OpenAPI schema drifted from the committed snapshot. If this is a "
        "deliberate route or model change, run `python -m scripts.generate_openapi_snapshot` "
        "from apps/api, review the diff, and commit it."
    )
