#!/usr/bin/env bash
# Install the host-side, non-containerized passive BlueZ receiver on an OMK Pi.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
SERVICE_NAME=omk-ble-sensor-manager.service
SERVICE_SOURCE="${OMK_ROOT}/systemd/${SERVICE_NAME}.in"
VENV_PATH="${OMK_ROOT}/services/ble-sensor-manager/.venv"
log() { printf '[omk-ble-setup] %s\n' "$*"; }
warn() { printf '[omk-ble-setup] WARN: %s\n' "$*" >&2; }
[[ "$(uname -s)" == Linux ]] || { echo "Linux is required." >&2; exit 1; }
command -v bluetoothctl >/dev/null || { echo "BlueZ is required." >&2; exit 1; }
[[ -f "${SERVICE_SOURCE}" ]] || { echo "Missing ${SERVICE_SOURCE}" >&2; exit 1; }
if bluetoothctl show 2>/dev/null | grep -q '^Controller '; then
  if command -v rfkill >/dev/null && rfkill list bluetooth 2>/dev/null | grep -q 'Soft blocked: yes'; then
    sudo rfkill unblock bluetooth || warn "Could not unblock Bluetooth; check rfkill manually."
  fi
else
  warn "No Bluetooth adapter detected; service will retry."
fi
sudo systemctl enable bluetooth.service || warn "Could not enable bluetooth.service."
sudo systemctl start bluetooth.service || warn "Could not start bluetooth.service."
if bluetoothctl show 2>/dev/null | grep -q '^Controller ' && ! bluetoothctl show 2>/dev/null | grep -q 'Powered: yes'; then
  sudo bluetoothctl power on >/dev/null 2>&1 || warn "Could not power on Bluetooth; run: sudo bluetoothctl power on"
fi
sudo install -d -o "${TARGET_USER}" -g "$(id -gn "${TARGET_USER}")" -m 0750 "${OMK_ROOT}/data/ble"
log "Creating or reusing Python virtual environment."
sudo -u "${TARGET_USER}" python3 -m venv "${VENV_PATH}"
log "Installing BLE manager requirements."
sudo -u "${TARGET_USER}" "${VENV_PATH}/bin/pip" install --upgrade pip
sudo -u "${TARGET_USER}" "${VENV_PATH}/bin/pip" install -r "${OMK_ROOT}/services/ble-sensor-manager/requirements.txt"
log "Installing systemd unit."
sed -e "s|@OMK_USER@|${TARGET_USER}|g" -e "s|@OMK_GROUP@|$(id -gn "${TARGET_USER}")|g" -e "s|@OMK_ROOT@|${OMK_ROOT}|g" "${SERVICE_SOURCE}" | sudo tee "/etc/systemd/system/${SERVICE_NAME}" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl --no-pager --full status "${SERVICE_NAME}"
