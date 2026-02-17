#!/usr/bin/env bash
# install.sh -- Set up the Surf & Pier Fishing Forecast as a systemd service
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
# What it does:
#   1. Creates a Python virtual environment and installs dependencies
#   2. Installs a systemd service so the dashboard starts on boot
#   3. Enables and starts the service immediately
#
# After running this script, open http://<your-ip>:5757 in any browser.
# You will never need to manually start the server again.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PORT="${PORT:-5757}"
SERVICE_NAME="surf-forecast"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }

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
# 3. Install systemd service
# ---------------------------------------------------------------------------
info "Installing systemd service..."

CURRENT_USER="$(whoami)"

# Build the unit file from the template
sed \
  -e "s|REPLACE_USER|${CURRENT_USER}|g" \
  -e "s|REPLACE_DIR|${PROJECT_DIR}|g" \
  "${PROJECT_DIR}/surf-forecast.service" \
  > /tmp/${SERVICE_NAME}.service

sudo cp /tmp/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service
rm /tmp/${SERVICE_NAME}.service

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}.service
sudo systemctl restart ${SERVICE_NAME}.service

# ---------------------------------------------------------------------------
# 4. Wait for startup and verify
# ---------------------------------------------------------------------------
sleep 2
if systemctl is-active --quiet ${SERVICE_NAME}; then
  info "Service is running!"
else
  warn "Service may not have started. Check: sudo journalctl -u ${SERVICE_NAME} -n 20"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
info "Setup complete. Your fishing forecast dashboard is live at:"
echo ""
echo "    http://localhost:${PORT}"
echo ""
# Try to detect LAN IP for convenience
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -n "${LAN_IP}" ]; then
  echo "    http://${LAN_IP}:${PORT}  (from other devices on your network)"
  echo ""
fi
info "The service starts automatically on boot. Useful commands:"
echo ""
echo "    sudo systemctl status ${SERVICE_NAME}    # Check status"
echo "    sudo systemctl restart ${SERVICE_NAME}   # Restart"
echo "    sudo journalctl -u ${SERVICE_NAME} -f    # Live logs"
echo ""
