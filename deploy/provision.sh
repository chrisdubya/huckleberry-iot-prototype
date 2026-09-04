#!/usr/bin/env bash
# Provision a fresh Raspberry Pi (OS Lite, user 'pi') as a huckdeck device.
# Run ON the Pi:  bash provision.sh
# Prereqs: flashed with Raspberry Pi Imager with WiFi + SSH configured.
set -euo pipefail

REPO_URL="https://github.com/chrisdubya/huckleberry-iot-prototype.git"
APP_DIR="$HOME/huckleberry-iot-prototype"

echo "==> apt packages (lgpio builds from source on Trixie)"
sudo apt-get update
sudo apt-get install -y git python3-dev swig liblgpio-dev network-manager dnsmasq-base

echo "==> uv"
if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

echo "==> clone + sync"
if [ ! -d "$APP_DIR" ]; then
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
git pull --ff-only
uv sync --extra pi

echo "==> setup mode (hotspot) support: polkit rule + captive DNS"
sudo install -m 644 deploy/polkit-huckdeck-networkmanager.rules /etc/polkit-1/rules.d/50-huckdeck-networkmanager.rules
sudo install -d /etc/NetworkManager/dnsmasq-shared.d
sudo install -m 644 deploy/nm-dnsmasq-captive.conf /etc/NetworkManager/dnsmasq-shared.d/huckdeck-captive.conf

if [ ! -f "$APP_DIR/.env" ]; then
  echo
  echo "!! No .env found. Create $APP_DIR/.env with your Huckleberry credentials"
  echo "   (copy .env.example; or from your Mac: scp .env pi@huckdeck.local:$APP_DIR/)"
  echo "   Then re-run this script to install the service."
  exit 1
fi

echo "==> systemd service"
sudo cp deploy/huckdeck.service /etc/systemd/system/huckdeck.service
sudo systemctl daemon-reload
sudo systemctl enable --now huckdeck.service
sleep 3
systemctl --no-pager status huckdeck.service || true

echo
echo "Done. Logs: journalctl -u huckdeck -f"
