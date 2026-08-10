#!/usr/bin/env bash
# Started by Playwright's webServer config (playwright.config.ts) before the
# e2e suite runs. Resets the dev DB, seeds the fixed e2e allowlist emails,
# and boots the API on :8000.
set -euo pipefail
cd "$(dirname "$0")/../../backend"

if [ -f .venv/bin/activate ]; then
  # local dev convenience — CI installs deps globally in the job, no venv
  source .venv/bin/activate
fi

mkdir -p data
rm -f data/app.db
alembic upgrade head
python3 scripts/seed_e2e.py

exec uvicorn app.main:app --port 8000
