#!/usr/bin/env bash

# Thin, restartable entry point for building a standard OMK Gateway.
# Feature setup remains in the individual scripts; this script only orders them.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
WITH_SORACOM=false
WITH_BLE=false
WITH_BROUTE=false
WITH_KIOSK=false
DRY_RUN=false

usage() {
  cat <<'EOF'
Usage: scripts/setup-omk-gateway.sh [OPTIONS]

Build a standard Raspberry Pi 4/5 (64-bit Raspberry Pi OS) OMK Gateway by
calling the individual, independently rerunnable setup scripts in order.

Options:
  --with-soracom  Configure SORACOM Onyx after the base host setup.
  --with-ble      Install the optional BLE sensor-manager host service.
  --with-broute   Install the optional B-route meter host service.
  --with-kiosk    Install the Dashboard kiosk (only in an active GUI login).
  --dry-run       Print the selected sequence without making changes.
  -h, --help      Show this help.

The OMK AP step prompts for a PSK without placing it in argv or logs. It may
disconnect SSH when it activates wlan0. If base setup requests a reboot or
re-login, this script stops safely; reconnect and run it again to resume.
EOF
}

log() { printf '[omk-gateway-setup] %s\n' "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --with-soracom) WITH_SORACOM=true ;;
    --with-ble) WITH_BLE=true ;;
    --with-broute) WITH_BROUTE=true ;;
    --with-kiosk) WITH_KIOSK=true ;;
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "Unknown option: $1" ;;
  esac
  shift
done

[[ -f "${SCRIPT_DIR}/setup-raspberry-pi.sh" ]] || fail "Run from a complete OMK repository."
[[ "$(uname -s)" == Linux ]] || fail 'Linux is required.'

steps=(
  'setup-raspberry-pi.sh|Base OS, Docker, and runtime directories'
)
"${WITH_SORACOM}" && steps+=('setup-soracom-onyx.sh|Optional SORACOM Onyx')
steps+=(
  'setup-wifi-access-point.sh --activate|Required OMK AP (interactive PSK; may disconnect SSH)'
  'setup-system-manager.sh|Required Dashboard host API and token environment'
  'setup-data-collection.sh|Required Docker collection and Dashboard services'
  'setup-data-transformer.sh|Required JSONL to Parquet timer'
)
"${WITH_BLE}" && steps+=('setup-ble-sensor-manager.sh|Optional BLE host service')
"${WITH_BROUTE}" && steps+=('setup-broute-meter.sh|Optional B-route host service')
"${WITH_KIOSK}" && steps+=('setup-dashboard-kiosk.sh|Optional GUI kiosk')

log "Repository root: ${OMK_ROOT}"
log 'Selected setup sequence:'
for index in "${!steps[@]}"; do
  IFS='|' read -r command description <<<"${steps[index]}"
  printf '  %d. %s — %s\n' "$((index + 1))" "${command}" "${description}"
done
if "${DRY_RUN}"; then
  log 'DRY-RUN: no individual setup script was invoked.'
  exit 0
fi

run_step() {
  local command="$1" description="$2"
  local -a argv=()
  read -r -a argv <<<"${command}"
  log "Starting: ${description}"
  "${SCRIPT_DIR}/${argv[0]}" "${argv[@]:1}"
}

run_step 'setup-raspberry-pi.sh' 'Base OS, Docker, and runtime directories'
if [[ -e /var/run/reboot-required ]]; then
  log 'A reboot is required. Reboot, reconnect, then rerun this command to resume.'
  exit 0
fi
if ! id -nG "${SUDO_USER:-$(id -un)}" | tr ' ' '\n' | grep -Fxq docker; then
  log 'Docker group membership needs a new login session. Re-login, then rerun this command to resume.'
  exit 0
fi

for index in "${!steps[@]}"; do
  ((index == 0)) && continue
  IFS='|' read -r command description <<<"${steps[index]}"
  run_step "${command}" "${description}"
done

log 'Final Gateway health check:'
systemctl is-active --quiet omk-system-manager.service || fail 'system-manager is not active.'
systemctl is-active --quiet omk-data-transformer.timer || fail 'data-transformer timer is not active.'
systemctl is-active --quiet omk-ap-isolation.service || fail 'OMK AP isolation service is not active.'
for service in mosquitto sensor-collector dashboard harvest-uploader; do
  docker compose -f "${OMK_ROOT}/compose.yaml" ps --status running --services | grep -Fxq "${service}" || fail "${service} container is not running."
done
curl --fail --silent --show-error http://127.0.0.1:8000/health >/dev/null || fail 'Dashboard health check failed.'
ip -4 addr show wlan0 | grep -Fq '192.168.50.1/' || fail 'OMK AP address is not assigned to wlan0.'
"${WITH_BLE}" && systemctl is-active --quiet omk-ble-sensor-manager.service || ! "${WITH_BLE}" || fail 'BLE sensor-manager is not active.'
"${WITH_BROUTE}" && systemctl is-active --quiet omk-broute-meter.service || ! "${WITH_BROUTE}" || fail 'B-route meter is not active.'
log 'SUCCESS: required Gateway services are healthy.'
log 'Optional services were installed only when their --with-* option was specified.'
