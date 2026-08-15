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
NETWORK_PROVISIONING_VERSION=1.2.4
# Pinned by the official component's repository_info.commit_sha for v1.2.4.
NETWORK_PROVISIONING_COMMIT=2de4980640bbe3d2d69473d7251640039e185b92
ESP_PROVISIONING_RUNTIME="${ESP_PROVISIONING_ROOT}/runtime-network-${NETWORK_PROVISIONING_VERSION}-idf-${ESP_IDF_VERSION}"

log() { printf '[omk-ble-setup] %s\n' "$*"; }
warn() { printf '[omk-ble-setup] WARN: %s\n' "$*" >&2; }

valid_network_provisioning_tree() {
  local tree="$1"
  [[ -f "${tree}/LICENSE" ]] &&
    [[ -f "${tree}/idf_component.yml" ]] &&
    [[ -f "${tree}/tool/esp_prov/esp_prov.py" ]] &&
    [[ -f "${tree}/tool/esp_prov/transport/transport_ble.py" ]] &&
    grep -Fqx "version: ${NETWORK_PROVISIONING_VERSION}" "${tree}/idf_component.yml"
}

valid_protocomm_tree() {
  local tree="$1"
  [[ -f "${tree}/LICENSE" ]] &&
    [[ -f "${tree}/components/protocomm/python/session_pb2.py" ]] &&
    grep -Fqx 'set(IDF_VERSION_MAJOR 6)' "${tree}/tools/cmake/version.cmake" &&
    grep -Fqx 'set(IDF_VERSION_MINOR 0)' "${tree}/tools/cmake/version.cmake" &&
    grep -Fqx 'set(IDF_VERSION_PATCH 1)' "${tree}/tools/cmake/version.cmake"
}

valid_esp_provisioning_runtime() {
  local tree="$1"
  valid_network_provisioning_tree "${tree}/network_provisioning-${NETWORK_PROVISIONING_VERSION}" &&
    valid_protocomm_tree "${tree}/esp-idf-${ESP_IDF_VERSION}-protocomm"
}

install_esp_provisioning_tooling() {
  if valid_esp_provisioning_runtime "${ESP_PROVISIONING_RUNTIME}"; then
    sudo ln -sfn "$(basename "${ESP_PROVISIONING_RUNTIME}")" "${ESP_PROVISIONING_ROOT}/current.new"
    sudo mv -Tf "${ESP_PROVISIONING_ROOT}/current.new" "${ESP_PROVISIONING_ROOT}/current"
    log "Using network_provisioning v${NETWORK_PROVISIONING_VERSION} and ESP-IDF v${ESP_IDF_VERSION} protocomm tooling."
    return
  fi
  command -v curl >/dev/null || { echo "curl is required to install ESP provisioning tooling." >&2; exit 1; }
  command -v tar >/dev/null || { echo "tar is required to install ESP provisioning tooling." >&2; exit 1; }
  local temporary component_archive idf_archive component_source idf_source staging backup
  temporary="$(mktemp -d)"
  component_archive="${temporary}/network-provisioning-${NETWORK_PROVISIONING_VERSION}.tar.gz"
  idf_archive="${temporary}/esp-idf-v${ESP_IDF_VERSION}.tar.gz"
  trap 'rm -rf "${temporary}" "${credential_tmp:-}"' EXIT
  log "Downloading official network_provisioning v${NETWORK_PROVISIONING_VERSION} and ESP-IDF v${ESP_IDF_VERSION} protocomm tooling."
  curl --fail --location --proto '=https' --tlsv1.2 \
    "https://github.com/espressif/idf-extra-components/archive/${NETWORK_PROVISIONING_COMMIT}.tar.gz" -o "${component_archive}"
  curl --fail --location --proto '=https' --tlsv1.2 \
    "https://github.com/espressif/esp-idf/archive/refs/tags/v${ESP_IDF_VERSION}.tar.gz" -o "${idf_archive}"
  tar -xzf "${component_archive}" -C "${temporary}"
  tar -xzf "${idf_archive}" -C "${temporary}"
  component_source="${temporary}/idf-extra-components-${NETWORK_PROVISIONING_COMMIT}/network_provisioning"
  idf_source="${temporary}/esp-idf-${ESP_IDF_VERSION}"
  valid_network_provisioning_tree "${component_source}" || { echo "Downloaded network_provisioning tree did not validate as v${NETWORK_PROVISIONING_VERSION}." >&2; exit 1; }
  valid_protocomm_tree "${idf_source}" || { echo "Downloaded ESP-IDF tree did not validate as v${ESP_IDF_VERSION}." >&2; exit 1; }
  staging="${temporary}/runtime-network-${NETWORK_PROVISIONING_VERSION}-idf-${ESP_IDF_VERSION}"
  mkdir -p "${staging}/esp-idf-${ESP_IDF_VERSION}-protocomm/components/protocomm" "${staging}/esp-idf-${ESP_IDF_VERSION}-protocomm/tools/cmake"
  cp -a "${component_source}" "${staging}/network_provisioning-${NETWORK_PROVISIONING_VERSION}"
  cp -a "${idf_source}/LICENSE" "${staging}/esp-idf-${ESP_IDF_VERSION}-protocomm/LICENSE"
  cp -a "${idf_source}/components/protocomm/python" "${staging}/esp-idf-${ESP_IDF_VERSION}-protocomm/components/protocomm/python"
  cp -a "${idf_source}/tools/cmake/version.cmake" "${staging}/esp-idf-${ESP_IDF_VERSION}-protocomm/tools/cmake/version.cmake"
  valid_esp_provisioning_runtime "${staging}" || { echo "Provisioning runtime assembly validation failed." >&2; exit 1; }
  sudo install -d -m 0755 "${ESP_PROVISIONING_ROOT}"
  if [[ -e "${ESP_PROVISIONING_RUNTIME}" ]]; then
    backup="${ESP_PROVISIONING_RUNTIME}.backup-$(date +%s)"
    sudo mv "${ESP_PROVISIONING_RUNTIME}" "${backup}"
    warn "Replaced invalid provisioning tooling; previous tree retained at ${backup}."
  fi
  sudo mv "${staging}" "${ESP_PROVISIONING_RUNTIME}"
  sudo chown -R root:root "${ESP_PROVISIONING_RUNTIME}"
  sudo chmod -R a+rX "${ESP_PROVISIONING_RUNTIME}"
  sudo ln -sfn "$(basename "${ESP_PROVISIONING_RUNTIME}")" "${ESP_PROVISIONING_ROOT}/current.new"
  sudo mv -Tf "${ESP_PROVISIONING_ROOT}/current.new" "${ESP_PROVISIONING_ROOT}/current"
  log "Installed network_provisioning v${NETWORK_PROVISIONING_VERSION} and ESP-IDF v${ESP_IDF_VERSION} protocomm tooling at ${ESP_PROVISIONING_ROOT}."
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
