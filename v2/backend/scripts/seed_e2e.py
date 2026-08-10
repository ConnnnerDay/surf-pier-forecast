"""Seed the beta allowlist with the fixed set of emails v2/frontend's
Playwright e2e suite signs up with. Run against a freshly-migrated DB —
see v2/frontend/e2e/start-backend.sh, which calls this after `alembic
upgrade head` and before starting uvicorn.

Not for production use: this is test fixture data, not a real invite flow.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

_E2E_EMAILS = [
    "e2e-existing@example.com",  # pre-signed-up by e2e/global-setup.ts for login tests
    "e2e-newuser@example.com",  # consumed by the signup-flow test itself
    "e2e-forecast@example.com",  # consumed by the add-location/forecast test
]


def main() -> None:
    if not _DB_PATH.exists():
        print(
            f"error: {_DB_PATH} does not exist — run `alembic upgrade head` first",
            file=sys.stderr,
        )
        raise SystemExit(1)

    conn = sqlite3.connect(_DB_PATH)
    for email in _E2E_EMAILS:
        conn.execute(
            """
            INSERT INTO beta_allowlist (id, email, invited_at, used)
            VALUES (?, ?, datetime('now'), 0)
            ON CONFLICT(email) DO UPDATE SET used = 0
            """,
            (f"e2e-seed-{email}", email),
        )
    conn.commit()
    conn.close()
    print(f"Seeded {len(_E2E_EMAILS)} beta allowlist entries for e2e tests")


if __name__ == "__main__":
    main()
