#!/usr/bin/env bash
# Install the host-side, non-containerized BlueZ receiver on an OMK Pi.
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
command -v bluetoothctl >/dev/null || { echo "BlueZ is not installed; install bluetooth before continuing." >&2; exit 1; }
[[ -f "${SERVICE_SOURCE}" ]] || { echo "Missing ${SERVICE_SOURCE}" >&2; exit 1; }
if bluetoothctl show 2>/dev/null | grep -q '^Controller '; then
  log "Bluetooth adapter detected."
  if command -v rfkill >/dev/null 2>&1 && rfkill list bluetooth 2>/dev/null | grep -q 'Soft blocked: yes'; then
    log "Bluetooth is soft blocked; attempting unblock."
    sudo rfkill unblock bluetooth || warn "Could not unblock Bluetooth; continue after checking rfkill manually."
  fi
else
  warn "No Bluetooth adapter is currently detected; installing the service anyway. It will remain available and retry after an adapter is added."
fi

if ! systemctl is-enabled bluetooth.service >/dev/null 2>&1; then
  log "Enabling bluetooth.service."
  sudo systemctl enable bluetooth.service || warn "Could not enable bluetooth.service."
fi
if ! systemctl is-active bluetooth.service >/dev/null 2>&1; then
  log "Starting bluetooth.service."
  sudo systemctl start bluetooth.service || warn "Could not start bluetooth.service."
fi
if bluetoothctl show 2>/dev/null | grep -q '^Controller '; then
  if bluetoothctl show 2>/dev/null | grep -q 'Powered: yes'; then
    log "Bluetooth adapter is already powered."
  else
    log "Powering on Bluetooth adapter."
    sudo bluetoothctl power on >/dev/null 2>&1 || warn "Could not power on adapter; run: sudo bluetoothctl power on"
  fi
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
sudo systemctl enable --now "${SERVICE_NAME}"
sudo systemctl --no-pager --full status "${SERVICE_NAME}"
