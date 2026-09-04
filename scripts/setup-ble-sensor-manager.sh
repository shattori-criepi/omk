#!/usr/bin/env bash
# Install the host-side, non-containerized passive BlueZ receiver on an OMK Pi.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
# shellcheck source=lib/apt-helpers.sh
source "${SCRIPT_DIR}/lib/apt-helpers.sh"
TARGET_USER="${SUDO_USER:-$(id -un)}"
SERVICE_NAME=omk-ble-sensor-manager.service
SERVICE_SOURCE="${OMK_ROOT}/systemd/${SERVICE_NAME}.in"
VENV_PATH="${OMK_ROOT}/services/ble-sensor-manager/.venv"
log() { printf '[omk-ble-setup] %s\n' "$*"; }
warn() { printf '[omk-ble-setup] WARN: %s\n' "$*" >&2; }
[[ "$(uname -s)" == Linux ]] || { echo "Linux is required." >&2; exit 1; }
command -v apt-get >/dev/null || { echo "apt-get is required." >&2; exit 1; }
SUDO=()
if ((EUID != 0)); then
  command -v sudo >/dev/null || { echo "sudo is required when not run as root." >&2; exit 1; }
  SUDO=(sudo)
  "${SUDO[@]}" -v
fi
missing_packages=()
for package in bluez rfkill python3 python3-venv python3-pip; do
  dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null | grep -q '^ii' || missing_packages+=("${package}")
done
if ((${#missing_packages[@]})); then
  log "Installing direct BLE manager dependencies: ${missing_packages[*]}"
  omk_apt "${SUDO[@]}" apt-get update
  omk_apt "${SUDO[@]}" apt-get install -y "${missing_packages[@]}"
else
  log 'Direct BLE manager dependencies are already installed.'
fi
command -v bluetoothctl >/dev/null || { echo "BlueZ is unavailable after installation." >&2; exit 1; }
[[ -f "${SERVICE_SOURCE}" ]] || { echo "Missing ${SERVICE_SOURCE}" >&2; exit 1; }
if bluetoothctl show 2>/dev/null | grep -q '^Controller '; then
  if command -v rfkill >/dev/null && rfkill list bluetooth 2>/dev/null | grep -q 'Soft blocked: yes'; then
    "${SUDO[@]}" rfkill unblock bluetooth || warn "Could not unblock Bluetooth; check rfkill manually."
  fi
else
  warn "No Bluetooth adapter detected; service will retry."
fi
"${SUDO[@]}" systemctl enable bluetooth.service || warn "Could not enable bluetooth.service."
"${SUDO[@]}" systemctl start bluetooth.service || warn "Could not start bluetooth.service."
if bluetoothctl show 2>/dev/null | grep -q '^Controller ' && ! bluetoothctl show 2>/dev/null | grep -q 'Powered: yes'; then
  "${SUDO[@]}" bluetoothctl power on >/dev/null 2>&1 || warn "Could not power on Bluetooth; run: sudo bluetoothctl power on"
fi
"${SUDO[@]}" install -d -o "${TARGET_USER}" -g "$(id -gn "${TARGET_USER}")" -m 0750 "${OMK_ROOT}/data/ble"
log "Creating or reusing Python virtual environment."
if ((EUID == 0)); then runuser -u "${TARGET_USER}" -- python3 -m venv "${VENV_PATH}"; else "${SUDO[@]}" -u "${TARGET_USER}" python3 -m venv "${VENV_PATH}"; fi
log "Installing BLE manager requirements."
if ((EUID == 0)); then
  runuser -u "${TARGET_USER}" -- "${VENV_PATH}/bin/pip" install --upgrade pip
  runuser -u "${TARGET_USER}" -- "${VENV_PATH}/bin/pip" install -r "${OMK_ROOT}/services/ble-sensor-manager/requirements.txt"
else
  "${SUDO[@]}" -u "${TARGET_USER}" "${VENV_PATH}/bin/pip" install --upgrade pip
  "${SUDO[@]}" -u "${TARGET_USER}" "${VENV_PATH}/bin/pip" install -r "${OMK_ROOT}/services/ble-sensor-manager/requirements.txt"
fi
log "Installing systemd unit."
sed -e "s|@OMK_USER@|${TARGET_USER}|g" -e "s|@OMK_GROUP@|$(id -gn "${TARGET_USER}")|g" -e "s|@OMK_ROOT@|${OMK_ROOT}|g" "${SERVICE_SOURCE}" | "${SUDO[@]}" tee "/etc/systemd/system/${SERVICE_NAME}" >/dev/null
"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable "${SERVICE_NAME}"
"${SUDO[@]}" systemctl restart "${SERVICE_NAME}"
"${SUDO[@]}" systemctl --no-pager --full status "${SERVICE_NAME}"
