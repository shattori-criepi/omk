#!/usr/bin/env bash

# Thin, restartable entry point for building a standard OMK Gateway.
# Feature setup remains in the individual scripts; this script only orders them.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
# shellcheck source=lib/apt-helpers.sh
source "${SCRIPT_DIR}/lib/apt-helpers.sh"
WITH_SORACOM=false
WITH_BLE=false
WITH_BROUTE=false
WITH_KIOSK=true
WITH_BASE=false
DRY_RUN=false
PREBASE_MIN_FREE_KIB="${OMK_PREBASE_MIN_FREE_KIB:-8388608}"
PREBASE_OS_RELEASE_PATH="${OMK_TEST_OS_RELEASE:-/etc/os-release}"
PREBASE_DEVICE_MODEL_PATH="${OMK_TEST_DEVICE_MODEL:-/proc/device-tree/model}"
PREBASE_SYS_CLASS_NET_PATH="${OMK_TEST_SYS_CLASS_NET:-/sys/class/net}"

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
  --no-kiosk      Do not configure the standard Dashboard kiosk.
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
    --no-kiosk) WITH_KIOSK=false ;;
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
"${WITH_KIOSK}" && steps+=('setup-dashboard-kiosk.sh --prepare|Standard kiosk package preparation before AP activation')
steps+=(
  'setup-wifi-access-point.sh --prepare|Required OMK AP profile/socket preparation; does not switch wlan0'
  'setup-data-collection.sh --with-ap-proxies|Required Docker collection and Dashboard services'
)
"${WITH_KIOSK}" && steps+=('setup-dashboard-kiosk.sh|Standard Dashboard kiosk configuration (local Dashboard is now available)')
steps+=(
  'final-pre-activation-check|Final local Gateway health verification before AP activation'
  'setup-wifi-access-point.sh --activate|Final OMK AP activation; may disconnect wlan0 SSH'
)

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

preflight_before_base_setup() {
  local os_id os_version free_kib now_epoch model
  [[ "${OMK_SKIP_PREBASE_PREFLIGHT:-false}" == true ]] && { log 'WARN: pre-base preflight skipped by explicit test override.'; return 0; }
  log 'Preflight before base setup: validating a clean Raspberry Pi host before OS changes.'
  command -v dpkg >/dev/null 2>&1 || fail 'Pre-base preflight failed: dpkg is unavailable.'
  [[ "$(dpkg --print-architecture)" == arm64 ]] || fail "Pre-base preflight failed: 64-bit Debian arm64 userland is required (got $(dpkg --print-architecture))."
  [[ "$(getconf LONG_BIT)" == 64 ]] || fail 'Pre-base preflight failed: a 64-bit userland is required.'
  [[ -r "${PREBASE_OS_RELEASE_PATH}" ]] || fail 'Pre-base preflight failed: /etc/os-release is unavailable.'
  # shellcheck disable=SC1091
  source "${PREBASE_OS_RELEASE_PATH}"
  os_id="${ID:-}"; os_version="${VERSION_ID:-unknown}"
  [[ "${os_id}" == debian || "${os_id}" == raspbian ]] || fail "Pre-base preflight failed: Raspberry Pi OS/Debian is required (got ${os_id:-unknown} ${os_version})."
  [[ -r "${PREBASE_DEVICE_MODEL_PATH}" ]] || fail 'Pre-base preflight failed: Raspberry Pi hardware model is unavailable.'
  model="$(tr -d '\0' <"${PREBASE_DEVICE_MODEL_PATH}")"
  [[ "${model}" == *'Raspberry Pi 4'* ]] || fail "Pre-base preflight failed: Raspberry Pi 4 is required (got ${model})."
  free_kib="$(df -Pk "${OMK_ROOT}" | awk 'NR==2 {print $4}')"
  [[ "${free_kib}" =~ ^[0-9]+$ ]] || fail 'Pre-base preflight failed: could not determine free disk space.'
  ((free_kib >= PREBASE_MIN_FREE_KIB)) || fail "Pre-base preflight failed: free disk is ${free_kib} KiB; at least ${PREBASE_MIN_FREE_KIB} KiB is required for OS update and Docker builds."
  ((EUID != 0)) || fail 'Pre-base preflight failed: run the standard setup as the normal user, not root.'
  command -v sudo >/dev/null 2>&1 || fail 'Pre-base preflight failed: sudo is unavailable.'
  sudo -v || fail 'Pre-base preflight failed: sudo authorization failed.'
  now_epoch="$(date +%s)"
  ((now_epoch >= 1704067200)) || fail 'Pre-base preflight failed: system clock predates 2024-01-01; correct time before HTTPS package access.'
  command -v ip >/dev/null 2>&1 && ip route show default | grep -q . || fail 'Pre-base preflight failed: no default network route is available.'
  getent ahosts deb.debian.org >/dev/null 2>&1 || fail 'Pre-base preflight failed: DNS cannot resolve deb.debian.org.'
  getent ahosts archive.raspberrypi.com >/dev/null 2>&1 || fail 'Pre-base preflight failed: DNS cannot resolve archive.raspberrypi.com.'
  [[ -d "${PREBASE_SYS_CLASS_NET_PATH}/wlan0" ]] || fail 'Pre-base preflight failed: wlan0 is unavailable.'
  if command -v iw >/dev/null 2>&1; then
    iw list 2>/dev/null | grep -Eq '^[[:space:]]*\* AP$' || fail 'Pre-base preflight failed: wlan0 hardware/driver does not report AP-mode support.'
  else
    log 'WARN: iw is not installed yet; AP-mode capability will be checked again by setup-wifi-access-point.sh before activation.'
  fi
  if command -v timedatectl >/dev/null 2>&1 && [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)" == no ]]; then
    log 'WARN: NTP is not synchronized yet; clock range is valid, but allow time sync before package HTTPS access.'
  fi
  if dpkg --audit | grep -q .; then
    fail 'Pre-base preflight failed: dpkg reports unfinished package configuration. Do not remove locks; allow the existing package task to finish, then rerun setup.'
  fi
  omk_apt sudo apt-get check >/dev/null || fail 'Pre-base preflight failed: apt package state is inconsistent; repair it deliberately, then rerun setup.'
  log 'PASS: pre-base host prerequisites are ready.'
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
    "${systemd_unit_dir}/omk-dashboard-ap-proxy.socket"
    "${systemd_unit_dir}/omk-dashboard-ap-proxy.service"
    "${systemd_unit_dir}/omk-mqtt-ap-proxy.socket"
    "${systemd_unit_dir}/omk-mqtt-ap-proxy.service"
    "${systemd_unit_dir}/omk-ap-activation.service"
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
  docker compose -f "${OMK_ROOT}/compose.yaml" config --no-env-resolution --format json |
    "${OMK_ROOT}/scripts/lib/validate-compose-publishes.py" ||
    fail 'AP preflight failed: Dashboard/MQTT Docker publishes are not IPv4-loopback-only.'
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
  "${OMK_ROOT}/scripts/lib/validate-ap-socket-units.sh" "${systemd_unit_dir}" --runtime >/dev/null ||
    fail 'AP preflight failed: AP socket proxy security contract is invalid.'
  systemctl is-active --quiet omk-dashboard-ap-proxy.socket || fail 'AP preflight failed: Dashboard AP socket is not active.'
  systemctl is-active --quiet omk-mqtt-ap-proxy.socket || fail 'AP preflight failed: MQTT AP socket is not active.'
  log 'PASS: AP preflight completed; local Compose startup prerequisites are available.'
}

base_reboot_is_required() {
  local current_kernel latest_kernel module_dir release
  [[ -e "${OMK_REBOOT_REQUIRED_PATH:-/var/run/reboot-required}" ]] && return 0
  current_kernel="$(uname -r)"
  latest_kernel="$(
    for module_dir in "${OMK_KERNEL_MODULES_DIR:-/lib/modules}"/*; do
      release="${module_dir##*/}"
      [[ "${current_kernel}" != *-rpi-v8 || "${release}" == *-rpi-v8 ]] || continue
      [[ -d "${module_dir}/kernel" ]] || continue
      find "${module_dir}/kernel" -type f -print -quit 2>/dev/null | grep -q . || continue
      printf '%s\n' "${release}"
    done | sort -V | tail -n 1
  )"
  [[ -n "${latest_kernel}" && "${latest_kernel}" != "${current_kernel}" ]] || return 1
  [[ "$(printf '%s\n%s\n' "${current_kernel}" "${latest_kernel}" | sort -V | tail -n 1)" == "${latest_kernel}" ]]
}

continuation_preflight() {
  log 'Continuation preflight: checking reboot state and non-root Docker access before setup changes.'
  base_reboot_is_required && fail 'A reboot is required before setup can continue.'
  ((EUID != 0)) && [[ -z "${SUDO_USER:-}" ]] || fail 'Continue setup from a normal login shell, not root or sudo.'
  id -nG | tr ' ' '\n' | grep -Fxq docker || fail 'The current login session does not include the docker group. Re-login before continuing.'
  command -v docker >/dev/null 2>&1 || fail 'Docker is unavailable. Run the base setup first.'
  docker info >/dev/null 2>&1 || fail 'The current login cannot access the Docker daemon without sudo.'
  docker compose version >/dev/null 2>&1 || fail 'The Docker Compose plugin is unavailable to the current login.'
  command -v python3 >/dev/null 2>&1 || fail 'python3 is required for the production publish security check.'
  [[ -x "${OMK_ROOT}/scripts/lib/validate-compose-publishes.py" ]] || fail 'The production publish security validator is missing or not executable.'
  docker compose -f "${OMK_ROOT}/compose.yaml" config --no-env-resolution --format json |
    "${OMK_ROOT}/scripts/lib/validate-compose-publishes.py" >/dev/null ||
    fail 'Dashboard/MQTT Docker publishing is not restricted to IPv4 loopback.'
  log 'PASS: continuation prerequisites are ready.'
}

if "${WITH_BASE}"; then
  preflight_before_base_setup
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

continuation_preflight

for index in "${!steps[@]}"; do
  if "${WITH_BASE}" && ((index == 0)); then
    continue
  fi
  IFS='|' read -r command description <<<"${steps[index]}"
  [[ "${command}" == 'final-pre-activation-check' || "${command}" == 'setup-wifi-access-point.sh --activate' ]] && continue
  run_step "${command}" "${description}"
done

validate_prepared_ap_profile() {
  local property value expected_autoconnect=no
  command -v nmcli >/dev/null 2>&1 || fail 'Prepared AP verification failed: nmcli is unavailable.'
  nmcli connection show omk-ap >/dev/null 2>&1 || fail 'Prepared AP verification failed: omk-ap NetworkManager profile is missing.'
  if nmcli -g NAME connection show --active 2>/dev/null | grep -Fxq omk-ap; then
    expected_autoconnect=yes
  fi
  local -a expected=(
    'connection.interface-name|wlan0'
    '802-11-wireless.mode|ap'
    "connection.autoconnect|${expected_autoconnect}"
    'ipv4.method|shared'
    'ipv4.addresses|192.168.50.1/24'
    'ipv6.method|disabled'
  )
  for property in "${expected[@]}"; do
    IFS='|' read -r property value <<<"${property}"
    [[ "$(nmcli -g "${property}" connection show omk-ap 2>/dev/null | head -n 1)" == "${value}" ]] ||
      fail "Prepared AP verification failed: omk-ap ${property} is not ${value}."
  done
  [[ -n "$(sudo -n nmcli --show-secrets -g 802-11-wireless-security.psk connection show omk-ap 2>/dev/null | head -n 1)" ]] ||
    fail 'Prepared AP verification failed: omk-ap WPA2 PSK is missing.'
}

ssh_uses_ap_interface() {
  local ssh_peer ssh_route
  [[ -n "${SSH_CONNECTION:-}" ]] || return 1
  ssh_peer="${SSH_CONNECTION%% *}"
  ssh_route="$(ip route get "${ssh_peer}" 2>/dev/null || true)"
  [[ "${ssh_route}" == *' dev wlan0 '* ]]
}

show_ap_credentials_before_wlan0_handoff() {
  local ssid psk answer credential_tty credential_input
  ssh_uses_ap_interface || return 0

  ssid="$(nmcli -g 802-11-wireless.ssid connection show omk-ap 2>/dev/null | head -n 1)"
  [[ -n "${ssid}" ]] || fail 'Cannot read the prepared OMK AP SSID before wlan0 activation.'
  log 'This SSH session routes through wlan0; offering OMK AP credentials before final activation.'

  # Secrets deliberately bypass stdout/stderr, which may be tee-backed to a
  # persistent setup log. The test-only paths let the shell regression test
  # exercise this boundary without changing the production /dev/tty contract.
  credential_tty="${OMK_TEST_CREDENTIAL_TTY:-/dev/tty}"
  credential_input="${OMK_TEST_CREDENTIAL_INPUT:-${credential_tty}}"
  if [[ -z "${OMK_TEST_CREDENTIAL_TTY:-}" ]] && [[ ! -c "${credential_tty}" ]]; then
    fail 'Cannot securely display the OMK AP password without a controlling terminal. Re-run from an interactive SSH terminal before activating wlan0.'
  fi
  [[ -r "${credential_input}" && -w "${credential_tty}" ]] ||
    fail 'Cannot securely display the OMK AP password without a readable and writable controlling terminal.'

  {
    printf '\nOMK AP is ready for its final activation.\n'
    printf 'SSID: %s\n' "${ssid}"
    printf 'Activating wlan0 as the OMK AP will disconnect this SSH session.\n'
    printf 'After activation, connect your PC to this SSID and open http://192.168.50.1:8000/.\n'
    printf 'Show the OMK AP password now? [y/N] '
  } >"${credential_tty}"
  IFS= read -r answer <"${credential_input}" || answer=''
  if [[ "${answer}" =~ ^[Yy]([Ee][Ss])?$ ]]; then
    if ((EUID == 0)); then
      psk="$(sudo -n nmcli --show-secrets -g 802-11-wireless-security.psk connection show omk-ap 2>/dev/null | head -n 1)"
    else
      psk="$(sudo -n nmcli --show-secrets -g 802-11-wireless-security.psk connection show omk-ap 2>/dev/null | head -n 1)"
    fi
    [[ -n "${psk}" ]] || fail 'Cannot read the prepared OMK AP password from NetworkManager.'
    printf '\nOMK AP password: %s\n' "${psk}" >>"${credential_tty}"
    unset psk
  else
    printf '\nOMK AP password was not displayed. Retrieve it before disconnecting if needed.\n' >>"${credential_tty}"
  fi
}

final_pre_activation_check() {
log 'Final pre-activation Gateway health check:'
systemctl is-active --quiet omk-system-manager.service || fail 'system-manager is not active.'
systemctl is-active --quiet omk-data-transformer.timer || fail 'data-transformer timer is not active.'
systemctl is-active --quiet omk-ap-isolation.service || fail 'OMK AP isolation service is not active.'
systemctl is-active --quiet omk-dashboard-ap-proxy.socket || fail 'Dashboard AP proxy socket is not active.'
systemctl is-active --quiet omk-mqtt-ap-proxy.socket || fail 'MQTT AP proxy socket is not active.'
running_services="$(docker compose -f "${OMK_ROOT}/compose.yaml" ps --status running --services)"
for service in mosquitto sensor-collector dashboard harvest-uploader; do
  grep -Fxq "${service}" <<<"${running_services}" || fail "${service} container is not running."
done
curl --fail --silent --show-error http://127.0.0.1:8000/health >/dev/null || fail 'Dashboard health check failed.'
"${WITH_BLE}" && systemctl is-active --quiet omk-ble-sensor-manager.service || ! "${WITH_BLE}" || fail 'BLE sensor-manager is not active.'
if "${WITH_BROUTE}" && ! systemctl is-active --quiet omk-broute-meter.service; then
  log 'WARN: B-route meter is installed and enabled but not active. This is expected until its adapter and credentials are ready.'
fi
validate_prepared_ap_profile
if "${WITH_KIOSK}"; then
  kiosk_user="${SUDO_USER:-$(id -un)}"
  kiosk_home="$(getent passwd "${kiosk_user}" | cut -d: -f6)"
  kiosk_unit="${OMK_KIOSK_UNIT_PATH:-${kiosk_home}/.config/systemd/user/omk-dashboard-kiosk.service}"
  kiosk_enable_link="${OMK_KIOSK_ENABLE_LINK:-${kiosk_home}/.config/systemd/user/default.target.wants/omk-dashboard-kiosk.service}"
  if [[ -S "/run/user/$(id -u)/${DASHBOARD_KIOSK_WAYLAND_DISPLAY:-wayland-0}" ]]; then
    systemctl --user is-active --quiet omk-dashboard-kiosk.service || fail 'Dashboard kiosk service is not active in the current Wayland session.'
  else
    [[ -f "${kiosk_unit}" ]] || fail "Dashboard kiosk unit is missing: ${kiosk_unit}"
    [[ -L "${kiosk_enable_link}" ]] || fail "Dashboard kiosk unit is not enabled: ${kiosk_enable_link}"
    log 'Dashboard kiosk runtime verification is deferred because no active Wayland GUI session is present.'
  fi
fi
log 'PASS: all Gateway services and OMK AP configuration are ready.'
log 'Final step: activating the OMK AP. The current SSH session may disconnect if it uses wlan0.'
}

preflight_before_ap_activation
final_pre_activation_check
show_ap_credentials_before_wlan0_handoff
if ! run_step 'setup-wifi-access-point.sh --activate' 'Final OMK AP activation; may disconnect wlan0 SSH'; then
  fail 'OMK AP profile is prepared but activation could not be handed to the worker.'
fi
log 'OMK AP activation has been queued independently of this SSH session.'
