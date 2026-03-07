#!/usr/bin/env bash
# install.sh -- Set up the Surf & Pier Fishing Forecast for local development
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
# What it does:
#   1. Creates a Python virtual environment
#   2. Installs all dependencies
#   3. Runs a quick smoke test to verify the app loads

set -euo pipefail

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
NC='\033[0m'

info() { echo -e "${GREEN}[+]${NC} $*"; }

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-5757}"

# ---------------------------------------------------------------------------
# 1. Python virtual environment
# ---------------------------------------------------------------------------
info "Creating Python virtual environment..."
python3 -m venv "${PROJECT_DIR}/.venv"
"${PROJECT_DIR}/.venv/bin/pip" install --quiet --upgrade pip
"${PROJECT_DIR}/.venv/bin/pip" install --quiet -r "${PROJECT_DIR}/requirements.txt"
info "Dependencies installed."

# ---------------------------------------------------------------------------
# 2. Quick smoke test
# ---------------------------------------------------------------------------
info "Verifying app imports..."
"${PROJECT_DIR}/.venv/bin/python" -c "import app; print('  app.py OK')"

# ---------------------------------------------------------------------------
# Done — start the app
# ---------------------------------------------------------------------------
echo ""
info "Setup complete. Starting app on http://localhost:${PORT} ..."
echo ""
"${PROJECT_DIR}/.venv/bin/python" "${PROJECT_DIR}/app.py"
