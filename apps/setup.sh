#!/usr/bin/env bash
set -euo pipefail

# One-command fresh-machine setup for both canonical apps.
# See apps/README.md for what this does and how to run/check afterward.

cd "$(dirname "$0")"

echo "==> apps/api: creating venv + installing dependencies"
(
  cd api
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -r requirements-dev.txt
)

echo "==> apps/web: installing dependencies"
(
  cd web
  npm install
)

echo "==> Setup complete. See apps/README.md to run dev servers or checks."
