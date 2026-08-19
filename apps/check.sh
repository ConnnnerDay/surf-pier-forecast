#!/usr/bin/env bash
set -uo pipefail

# Runs the same checks as .github/workflows/apps-ci.yml, for both apps, from
# one command. Run apps/setup.sh first if dependencies aren't installed.
#
# Deliberately does not use `set -e`: every check runs even if an earlier
# one fails, so a single run reports everything wrong at once instead of
# stopping at the first failure (the same reasoning behind apps-ci.yml's
# per-job/step isolation — see docs/R2_CI_BASELINE.md).

cd "$(dirname "$0")"

status=0

echo "==> apps/api: ruff check"
(cd api && source .venv/bin/activate && ruff check .) || status=1

echo "==> apps/api: ruff format --check"
(cd api && source .venv/bin/activate && ruff format --check .) || status=1

echo "==> apps/api: mypy"
(cd api && source .venv/bin/activate && mypy .) || status=1

echo "==> apps/api: pytest"
(cd api && source .venv/bin/activate && pytest -q) || status=1

echo "==> apps/web: lint"
(cd web && npm run lint) || status=1

echo "==> apps/web: build"
(cd web && npm run build) || status=1

if [ "$status" -ne 0 ]; then
  echo ""
  echo "One or more checks failed above."
  exit 1
fi

echo ""
echo "All checks passed."
