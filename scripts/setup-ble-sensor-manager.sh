#!/usr/bin/env bash
# Install the host-side, non-containerized BlueZ receiver on an OMK Pi.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
SERVICE_NAME=omk-ble-sensor-manager.service
SERVICE_SOURCE="${OMK_ROOT}/systemd/${SERVICE_NAME}.in"
VENV_PATH="${OMK_ROOT}/services/ble-sensor-manager/.venv"

[[ "$(uname -s)" == Linux ]] || { echo "Linux is required." >&2; exit 1; }
command -v bluetoothctl >/dev/null || { echo "BlueZ is not installed; install bluetooth before continuing." >&2; exit 1; }
[[ -f "${SERVICE_SOURCE}" ]] || { echo "Missing ${SERVICE_SOURCE}" >&2; exit 1; }
systemctl is-enabled bluetooth.service >/dev/null 2>&1 || sudo systemctl enable --now bluetooth.service
sudo install -d -o "${TARGET_USER}" -g "$(id -gn "${TARGET_USER}")" -m 0750 "${OMK_ROOT}/data/ble"
sudo -u "${TARGET_USER}" python3 -m venv "${VENV_PATH}"
sudo -u "${TARGET_USER}" "${VENV_PATH}/bin/pip" install --upgrade pip
sudo -u "${TARGET_USER}" "${VENV_PATH}/bin/pip" install -r "${OMK_ROOT}/services/ble-sensor-manager/requirements.txt"
sed -e "s|@OMK_USER@|${TARGET_USER}|g" -e "s|@OMK_GROUP@|$(id -gn "${TARGET_USER}")|g" -e "s|@OMK_ROOT@|${OMK_ROOT}|g" "${SERVICE_SOURCE}" | sudo tee "/etc/systemd/system/${SERVICE_NAME}" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"
sudo systemctl --no-pager --full status "${SERVICE_NAME}"
