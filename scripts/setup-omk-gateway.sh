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
WITH_BASE=false
DRY_RUN=false

usage() {
  cat <<'EOF'
Usage: scripts/setup-omk-gateway.sh [OPTIONS]

Build a standard 64-bit Raspberry Pi OS OMK Gateway by calling the individual,
independently rerunnable setup scripts in order. Raspberry Pi 4 is the
officially verified target.

Options:
  --with-base     Run the base OS/Docker setup (required for a new Gateway).
  --with-soracom  Configure SORACOM Onyx after the base host setup.
  --with-ble      Install the optional BLE sensor-manager host service.
  --with-broute   Install the optional B-route meter host service.
  --with-kiosk    Install the Dashboard kiosk (only in an active GUI login).
  --dry-run       Print the selected sequence without making changes.
  -h, --help      Show this help.

The OMK AP step runs after package installation and Docker image preparation,
automatically generates a PSK on first setup, and preserves an existing PSK.
It may disconnect SSH when it activates wlan0. Existing Gateways skip base setup by default. If --with-base
requests a reboot or re-login, this script stops safely; reconnect and rerun
without --with-base to continue.
EOF
}

log() { printf '[omk-gateway-setup] %s\n' "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --with-base) WITH_BASE=true ;;
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

steps=()
"${WITH_BASE}" && steps+=('setup-raspberry-pi.sh|Base OS, Docker, and runtime directories')
"${WITH_SORACOM}" && steps+=('setup-soracom-onyx.sh|Optional SORACOM Onyx')
steps+=(
  'setup-system-manager.sh|Required Dashboard host API and token environment'
  'setup-data-collection.sh --prepare|Required Docker image preparation before AP activation'
  'setup-data-transformer.sh|Required JSONL to Parquet timer'
  'setup-data-exporter.sh|Required SSH CSV/ZIP export CLI'
)
"${WITH_BLE}" && steps+=('setup-ble-sensor-manager.sh|Optional BLE host service')
"${WITH_BROUTE}" && steps+=('setup-broute-meter.sh|Optional B-route host service')
"${WITH_KIOSK}" && steps+=('setup-dashboard-kiosk.sh --prepare|Optional kiosk package preparation before AP activation')
steps+=(
  'setup-wifi-access-point.sh --activate|Required OMK AP (automatic PSK; may disconnect SSH)'
  'setup-data-collection.sh|Required Docker collection and Dashboard services'
)
"${WITH_KIOSK}" && steps+=('setup-dashboard-kiosk.sh|Optional GUI kiosk (local Dashboard is now available)')

log "Repository root: ${OMK_ROOT}"
if ! "${WITH_BASE}"; then
  log 'Base setup: skipped (use --with-base for a new Gateway or an explicit base refresh).'
fi
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

preflight_before_ap_activation() {
  local systemd_unit_dir="${OMK_PREFLIGHT_SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
  local dashboard_env_file="${OMK_PREFLIGHT_DASHBOARD_ENV_FILE:-/etc/omk/dashboard-system-manager.env}"
  local -a required_commands=(docker ip ss systemctl curl grep)
  local -a required_paths=(
    "${OMK_ROOT}/compose.yaml"
    "${OMK_ROOT}/services/mosquitto/config/mosquitto.conf"
    "${OMK_ROOT}/services/system-manager/.venv/bin/python"
    "${OMK_ROOT}/services/data-transformer/.venv/bin/python"
    "${OMK_ROOT}/data/sensors"
    "${OMK_ROOT}/data/latest"
    "${OMK_ROOT}/data/processed"
    "${OMK_ROOT}/data/dashboard"
    "${OMK_ROOT}/data/harvest-uploader"
    "${OMK_ROOT}/data/errors/transform"
    "${OMK_ROOT}/services/mosquitto/data"
    "${dashboard_env_file}"
  )
  local -a units=(
    "${systemd_unit_dir}/omk-system-manager.service"
    "${systemd_unit_dir}/omk-data-transformer.service"
    "${systemd_unit_dir}/omk-data-transformer.timer"
  )
  local -a images=()
  local command_name path image

  log 'Preflight before OMK AP activation: validating local-only startup prerequisites.'
  for command_name in "${required_commands[@]}"; do
    command -v "${command_name}" >/dev/null 2>&1 || fail "AP preflight failed: required post-AP command is unavailable: ${command_name}"
  done
  for path in "${required_paths[@]}"; do
    [[ -e "${path}" ]] || fail "AP preflight failed: required post-AP path is missing: ${path}"
  done

  docker compose -f "${OMK_ROOT}/compose.yaml" config --quiet ||
    fail 'AP preflight failed: Docker Compose configuration is invalid.'
  # compose.yaml contains only the four production services.  Do not depend on
  # optional positional-service support in older Compose plugin versions.
  mapfile -t images < <(docker compose -f "${OMK_ROOT}/compose.yaml" config --images)
  ((${#images[@]} > 0)) || fail 'AP preflight failed: Docker Compose did not resolve production images.'
  for image in "${images[@]}"; do
    docker image inspect "${image}" >/dev/null 2>&1 ||
      fail "AP preflight failed: required production image is not local: ${image}"
  done

  if command -v systemd-analyze >/dev/null 2>&1; then
    for path in "${units[@]}"; do
      [[ -f "${path}" ]] || fail "AP preflight failed: expected systemd unit is missing: ${path}"
    done
    systemd-analyze verify "${units[@]}" ||
      fail 'AP preflight failed: systemd unit verification failed.'
  else
    log 'WARN: systemd-analyze is unavailable; OMK systemd unit syntax was not verified before AP activation.'
  fi
  log 'PASS: AP preflight completed; local Compose startup prerequisites are available.'
}

base_reboot_is_required() {
  local current_kernel latest_kernel
  [[ -e /var/run/reboot-required ]] && return 0
  current_kernel="$(uname -r)"
  latest_kernel="$(find /lib/modules -mindepth 2 -maxdepth 2 -type d -name kernel -printf '%h\n' 2>/dev/null | sed 's|.*/||' | sort -V | tail -n 1)"
  [[ -n "${latest_kernel}" && "${latest_kernel}" != "${current_kernel}" ]] || return 1
  [[ "$(printf '%s\n%s\n' "${current_kernel}" "${latest_kernel}" | sort -V | tail -n 1)" == "${latest_kernel}" ]]
}

if "${WITH_BASE}"; then
  run_step 'setup-raspberry-pi.sh' 'Base OS, Docker, and runtime directories'
  if base_reboot_is_required; then
    log 'A reboot is required. Reboot, reconnect, then rerun this command without --with-base to resume.'
    exit 0
  fi
  # Do not query group membership for a named user here: NSS reports a newly
  # added docker group immediately, while this shell still lacks that group.
  # Only the current non-root login session can safely continue to Docker.
  if ((EUID == 0)) || [[ -n "${SUDO_USER:-}" ]] || ! id -nG | tr ' ' '\n' | grep -Fxq docker; then
    log 'Docker group membership needs a new login session. Re-login, then rerun this command without --with-base to resume.'
    exit 0
  fi
fi

for index in "${!steps[@]}"; do
  if "${WITH_BASE}" && ((index == 0)); then
    continue
  fi
  IFS='|' read -r command description <<<"${steps[index]}"
  if [[ "${command}" == 'setup-wifi-access-point.sh --activate' ]]; then
    preflight_before_ap_activation
  fi
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
if "${WITH_BROUTE}" && ! systemctl is-active --quiet omk-broute-meter.service; then
  log 'WARN: B-route meter is installed and enabled but not active. This is expected until its adapter and credentials are ready.'
fi
log 'SUCCESS: required Gateway services are healthy.'
log 'Optional services were installed only when their --with-* option was specified.'
