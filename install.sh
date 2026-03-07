#!/usr/bin/env bash
# install.sh -- Full local dev setup for Surf & Pier Fishing Forecast
#
# Usage (one command):
#   git clone https://github.com/ConnnnerDay/surf-pier-forecast.git && cd surf-pier-forecast && ./install.sh

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-5757}"

# ---------------------------------------------------------------------------
# 1. System dependencies
# ---------------------------------------------------------------------------
info "Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip

# ---------------------------------------------------------------------------
# 2. Python virtual environment
# ---------------------------------------------------------------------------
info "Creating virtual environment..."
python3 -m venv "${PROJECT_DIR}/.venv"
"${PROJECT_DIR}/.venv/bin/pip" install --quiet --upgrade pip

# ---------------------------------------------------------------------------
# 3. Python dependencies
# ---------------------------------------------------------------------------
info "Installing requirements..."
"${PROJECT_DIR}/.venv/bin/pip" install --quiet -r "${PROJECT_DIR}/requirements.txt"

# ---------------------------------------------------------------------------
# 4. Database init + migrations
# ---------------------------------------------------------------------------
info "Initialising database..."
"${PROJECT_DIR}/.venv/bin/python" "${PROJECT_DIR}/migrate_sqlite.py"

info "Migrating any legacy JSON forecasts..."
"${PROJECT_DIR}/.venv/bin/python" "${PROJECT_DIR}/migrate.py"

# ---------------------------------------------------------------------------
# 5. Smoke test
# ---------------------------------------------------------------------------
info "Verifying app loads..."
"${PROJECT_DIR}/.venv/bin/python" -c "import app; print('  app.py OK')"

# ---------------------------------------------------------------------------
# 6. Start
# ---------------------------------------------------------------------------
echo ""
info "Setup complete. Starting app on http://localhost:${PORT} ..."
echo ""
"${PROJECT_DIR}/.venv/bin/python" "${PROJECT_DIR}/app.py"
