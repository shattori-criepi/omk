#!/usr/bin/env bash
# Install the host-side, non-containerized BlueZ receiver on an OMK Pi.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
SERVICE_NAME=omk-ble-sensor-manager.service
SERVICE_SOURCE="${OMK_ROOT}/systemd/${SERVICE_NAME}.in"
VENV_PATH="${OMK_ROOT}/services/ble-sensor-manager/.venv"
AP_CONNECTION_NAME="${OMK_AP_CONNECTION_NAME:-omk-ap}"
CREDENTIAL_DIR=/etc/credstore.encrypted
ESP_PROVISIONING_ROOT=/opt/omk/esp-provisioning
ESP_IDF_VERSION=6.0.1
ESP_IDF_DIRECTORY="${ESP_PROVISIONING_ROOT}/esp-idf-${ESP_IDF_VERSION}"

log() { printf '[omk-ble-setup] %s\n' "$*"; }
warn() { printf '[omk-ble-setup] WARN: %s\n' "$*" >&2; }

valid_esp_provisioning_tree() {
  local tree="$1"
  [[ -f "${tree}/LICENSE" ]] &&
    [[ -f "${tree}/tools/esp_prov/esp_prov.py" ]] &&
    [[ -f "${tree}/tools/esp_prov/transport/transport_ble.py" ]] &&
    [[ -f "${tree}/components/protocomm/python/session_pb2.py" ]] &&
    grep -Fqx 'set(IDF_VERSION_MAJOR 6)' "${tree}/tools/cmake/version.cmake" &&
    grep -Fqx 'set(IDF_VERSION_MINOR 0)' "${tree}/tools/cmake/version.cmake" &&
    grep -Fqx 'set(IDF_VERSION_PATCH 1)' "${tree}/tools/cmake/version.cmake"
}

install_esp_provisioning_tooling() {
  if valid_esp_provisioning_tree "${ESP_IDF_DIRECTORY}"; then
    log "Using Espressif ESP-IDF v${ESP_IDF_VERSION} provisioning tooling at ${ESP_IDF_DIRECTORY}."
    return
  fi
  command -v curl >/dev/null || { echo "curl is required to install ESP provisioning tooling." >&2; exit 1; }
  command -v tar >/dev/null || { echo "tar is required to install ESP provisioning tooling." >&2; exit 1; }
  local temporary archive source backup
  temporary="$(mktemp -d)"
  archive="${temporary}/esp-idf-v${ESP_IDF_VERSION}.tar.gz"
  trap 'rm -rf "${temporary}" "${credential_tmp:-}"' EXIT
  log "Downloading official Espressif ESP-IDF v${ESP_IDF_VERSION} provisioning tooling."
  curl --fail --location --proto '=https' --tlsv1.2 \
    "https://github.com/espressif/esp-idf/archive/refs/tags/v${ESP_IDF_VERSION}.tar.gz" -o "${archive}"
  tar -xzf "${archive}" -C "${temporary}"
  source="${temporary}/esp-idf-${ESP_IDF_VERSION}"
  valid_esp_provisioning_tree "${source}" || { echo "Downloaded ESP-IDF tree did not validate as v${ESP_IDF_VERSION}." >&2; exit 1; }
  sudo install -d -m 0755 "${ESP_PROVISIONING_ROOT}"
  if [[ -e "${ESP_IDF_DIRECTORY}" ]]; then
    backup="${ESP_IDF_DIRECTORY}.backup-$(date +%s)"
    sudo mv "${ESP_IDF_DIRECTORY}" "${backup}"
    warn "Replaced invalid provisioning tooling; previous tree retained at ${backup}."
  fi
  sudo mv "${source}" "${ESP_IDF_DIRECTORY}"
  sudo chown -R root:root "${ESP_IDF_DIRECTORY}"
  sudo chmod -R a+rX "${ESP_IDF_DIRECTORY}"
  log "Installed Espressif ESP-IDF v${ESP_IDF_VERSION} provisioning tooling at ${ESP_IDF_DIRECTORY}."
}

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
command -v nmcli >/dev/null || { echo "nmcli is required to create the AP credential." >&2; exit 1; }
command -v systemd-creds >/dev/null || { echo "systemd-creds is required (systemd 250+)." >&2; exit 1; }
# Only setup runs with privilege. The manager never reads NetworkManager
# profiles or invokes nmcli --show-secrets at runtime.
ap_psk="$(sudo nmcli --show-secrets -g 802-11-wireless-security.psk connection show "${AP_CONNECTION_NAME}" 2>/dev/null | head -n 1)"
[[ -n "${ap_psk}" ]] || { echo "Cannot read a WPA PSK for ${AP_CONNECTION_NAME}." >&2; exit 1; }
credential_tmp="$(mktemp)"
trap 'rm -f "${credential_tmp}"' EXIT
umask 077
printf '%s' "${ap_psk}" > "${credential_tmp}"
unset ap_psk
sudo install -d -m 0700 "${CREDENTIAL_DIR}"
sudo systemd-creds encrypt --name=omk_ap_psk "${credential_tmp}" "${CREDENTIAL_DIR}/omk_ap_psk"
sudo chmod 0600 "${CREDENTIAL_DIR}/omk_ap_psk"
install_esp_provisioning_tooling
log "Creating or reusing Python virtual environment."
sudo -u "${TARGET_USER}" python3 -m venv "${VENV_PATH}"
log "Installing BLE manager requirements."
sudo -u "${TARGET_USER}" "${VENV_PATH}/bin/pip" install --upgrade pip
sudo -u "${TARGET_USER}" "${VENV_PATH}/bin/pip" install -r "${OMK_ROOT}/services/ble-sensor-manager/requirements.txt"
log "Installing systemd unit."
sed -e "s|@OMK_USER@|${TARGET_USER}|g" -e "s|@OMK_GROUP@|$(id -gn "${TARGET_USER}")|g" -e "s|@OMK_ROOT@|${OMK_ROOT}|g" "${SERVICE_SOURCE}" | sudo tee "/etc/systemd/system/${SERVICE_NAME}" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
# enable --now does not restart an already-active unit, so it would leave a
# newly added Environment= line unapplied during an update.
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl --no-pager --full status "${SERVICE_NAME}"
