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
# 6. Install and start systemd service
# ---------------------------------------------------------------------------
SERVICE_FILE="/etc/systemd/system/surf-forecast.service"
CURRENT_USER="$(id -un)"

info "Installing systemd service..."
sed \
    -e "s|REPLACE_USER|${CURRENT_USER}|g" \
    -e "s|REPLACE_DIR|${PROJECT_DIR}|g" \
    "${PROJECT_DIR}/surf-forecast.service" \
    | sudo tee "${SERVICE_FILE}" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now surf-forecast

echo ""
info "Service installed and started."
info "App is running at http://localhost:${PORT}"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status surf-forecast"
echo "    sudo systemctl restart surf-forecast"
echo "    sudo journalctl -u surf-forecast -f"
