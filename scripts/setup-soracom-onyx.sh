#!/usr/bin/env bash

# Configure a SORACOM Onyx (Quectel EG25/EC25 family) on Raspberry Pi OS.
# This script deliberately keeps LTE setup separate from the base OMK host setup.
set -euo pipefail

readonly EXIT_GENERAL=1
readonly EXIT_ONYX_NOT_FOUND=2
readonly EXIT_MODEM_NOT_FOUND=3
readonly EXIT_SIM_NOT_FOUND=4
readonly EXIT_NETWORK_NOT_REGISTERED=5
readonly EXIT_NO_IPV4=6
readonly EXIT_SORACOM_UNREACHABLE=7
readonly DEFAULT_APN="soracom.io"
readonly MODEM_WAIT_SECONDS=60
readonly CONNECTION_WAIT_SECONDS=30

APN="${SORACOM_APN:-${DEFAULT_APN}}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6 || true)"
LOG_FILE=""
MODEM_DETAILS=""

log() {
  printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"
}

# Never copy modem identifiers into the persistent setup log.
redact_modem_identifiers() {
  sed -E \
    -e '/(imei|imsi|iccid)/I s/(:[[:space:]]*).*/\1[REDACTED]/' \
    -e '/(equipment id|subscriber identity)/I s/(:[[:space:]]*).*/\1[REDACTED]/'
}

show_diagnostics() {
  log "Diagnostic information follows. Modem identifiers are redacted."
  log "lsusb:"
  (lsusb 2>&1 || true) | redact_modem_identifiers
  log "/dev/ttyUSB*:"
  (ls -l /dev/ttyUSB* 2>/dev/null || true) | redact_modem_identifiers
  log "/dev/cdc-wdm*:"
  (ls -l /dev/cdc-wdm* 2>/dev/null || true) | redact_modem_identifiers
  log "wwan0 link:"
  (ip link show wwan0 2>/dev/null || true) | redact_modem_identifiers
  log "NetworkManager devices:"
  (nmcli device status 2>&1 || true) | redact_modem_identifiers
  log "ModemManager (last 50 journal lines):"
  (journalctl -u ModemManager -n 50 --no-pager 2>&1 || true) | redact_modem_identifiers
}

has_quectel_usb_id() {
  local vendor_file product_file

  for vendor_file in /sys/bus/usb/devices/*/idVendor; do
    [[ -r "${vendor_file}" ]] || continue
    product_file="${vendor_file%/idVendor}/idProduct"
    if [[ -r "${product_file}" ]] && [[ "$(<"${vendor_file}")" == "2c7c" ]] &&
      [[ "$(<"${product_file}")" == "0125" ]]; then
      return 0
    fi
  done
  return 1
}

fail() {
  local exit_code="$1"
  shift
  log "ERROR: $*"
  exit "${exit_code}"
}

usage() {
  cat <<'EOF'
Usage: ./scripts/setup-soracom-onyx.sh [--apn APN]

Default APN: soracom.io (ordinary SORACOM Air SIM)
Use --apn du.soracom.io only for a plan-DU SIM.
EOF
}

while (($# > 0)); do
  case "$1" in
    --apn)
      (($# >= 2)) || fail "${EXIT_GENERAL}" "--apn requires a value."
      APN="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "${EXIT_GENERAL}" "Unknown option: $1"
      ;;
  esac
done

[[ -n "${APN}" ]] || fail "${EXIT_GENERAL}" "APN must not be empty."

if [[ "$(uname -s)" != "Linux" ]]; then
  fail "${EXIT_GENERAL}" "This script supports Linux only."
fi

ARCH="$(uname -m)"
if [[ "${ARCH}" != "aarch64" && "${ARCH}" != "arm64" ]]; then
  fail "${EXIT_GENERAL}" "Raspberry Pi or ARM64 Linux is required (detected: ${ARCH})."
fi

if ! command -v apt-get >/dev/null 2>&1; then
  fail "${EXIT_GENERAL}" "apt-get is required (Raspberry Pi OS/Debian is expected)."
fi
if ! command -v systemctl >/dev/null 2>&1; then
  fail "${EXIT_GENERAL}" "systemctl is required to manage NetworkManager and ModemManager."
fi
if ((EUID == 0)); then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || fail "${EXIT_GENERAL}" "sudo is required when not running as root."
  SUDO=(sudo)
  "${SUDO[@]}" -v
fi

if [[ -z "${RUN_HOME}" || ! -d "${RUN_HOME}" ]]; then
  fail "${EXIT_GENERAL}" "Could not determine a home directory for ${RUN_USER}."
fi
LOG_DIR="${RUN_HOME}/.local/state/omk/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/soracom-onyx-$(date '+%Y%m%d-%H%M%S').log"
exec > >(tee -a "${LOG_FILE}") 2>&1

log "SORACOM Onyx setup started."
log "Repository root: ${OMK_ROOT}"
log "OS: $(. /etc/os-release 2>/dev/null && printf '%s %s' "${PRETTY_NAME:-unknown}" "${VERSION_ID:-}")"
log "Architecture: ${ARCH}"
log "Requested APN: ${APN}"
log "Log file: ${LOG_FILE}"

USB_INFO="$(lsusb 2>&1 || true)"
if grep -Eqi '2c7c:0125|Quectel|EC25|EG25' <<<"${USB_INFO}" || \
  [[ -e /dev/cdc-wdm0 || -e /sys/class/net/wwan0 ]] || \
  has_quectel_usb_id; then
  log "A possible Onyx/Quectel modem was detected."
  printf '%s\n' "${USB_INFO}" | grep -Ei '2c7c:0125|Quectel|EC25|EG25' || true
else
  log "No SORACOM Onyx/Quectel modem was detected. Connect the Onyx, wait for USB enumeration, then rerun."
  show_diagnostics
  exit "${EXIT_ONYX_NOT_FOUND}"
fi

PACKAGES=(network-manager modemmanager usb-modeswitch usbutils curl ca-certificates)
log "Updating package indexes and installing: ${PACKAGES[*]}"
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y "${PACKAGES[@]}"

command -v nmcli >/dev/null 2>&1 || fail "${EXIT_GENERAL}" "nmcli is unavailable after installing network-manager."
command -v mmcli >/dev/null 2>&1 || fail "${EXIT_GENERAL}" "mmcli is unavailable after installing modemmanager."

log "Enabling and starting NetworkManager and ModemManager."
"${SUDO[@]}" systemctl enable --now NetworkManager
"${SUDO[@]}" systemctl enable --now ModemManager
systemctl is-active --quiet NetworkManager || fail "${EXIT_GENERAL}" "NetworkManager is not active."
systemctl is-active --quiet ModemManager || fail "${EXIT_GENERAL}" "ModemManager is not active."
log "NetworkManager: $(systemctl is-active NetworkManager); ModemManager: $(systemctl is-active ModemManager)"
log "ModemManager enabled state: $(systemctl is-enabled ModemManager 2>&1 || true)"

log "Waiting up to ${MODEM_WAIT_SECONDS}s for ModemManager to recognize the modem."
deadline=$((SECONDS + MODEM_WAIT_SECONDS))
while ((SECONDS < deadline)); do
  if MODEM_LIST="$(mmcli -L 2>&1)" && grep -q '/Modem/' <<<"${MODEM_LIST}"; then
    break
  fi
  sleep 3
done
if ! grep -q '/Modem/' <<<"${MODEM_LIST:-}"; then
  log "ModemManager did not recognize a modem within ${MODEM_WAIT_SECONDS}s."
  show_diagnostics
  exit "${EXIT_MODEM_NOT_FOUND}"
fi
log "Recognized modem: $(printf '%s\n' "${MODEM_LIST}" | redact_modem_identifiers)"
MODEM_DETAILS="$(mmcli -m any 2>&1 || true)"
log "Initial modem status:"
printf '%s\n' "${MODEM_DETAILS}" | redact_modem_identifiers

log "Downloading SORACOM's official setup_eg25.sh to a temporary directory."
TEMP_DIR="$(mktemp -d)"
cleanup() { rm -rf -- "${TEMP_DIR}"; }
trap cleanup EXIT
OFFICIAL_SCRIPT="${TEMP_DIR}/setup_eg25.sh"
curl --fail --location --silent --show-error \
  'https://soracom-files.s3.amazonaws.com/connect/setup_eg25.sh' \
  --output "${OFFICIAL_SCRIPT}" || fail "${EXIT_GENERAL}" "Could not download SORACOM's official setup script."
[[ -s "${OFFICIAL_SCRIPT}" ]] || fail "${EXIT_GENERAL}" "Downloaded SORACOM setup script is empty."
chmod 0700 "${OFFICIAL_SCRIPT}"

log "Running SORACOM's official setup script."
# The official script's documented ordinary-SIM invocation has no APN argument.
# Custom APNs are applied to the resulting NetworkManager profile below.
OFFICIAL_OUTPUT="$("${SUDO[@]}" "${OFFICIAL_SCRIPT}" 2>&1)" || OFFICIAL_STATUS=$?
OFFICIAL_STATUS="${OFFICIAL_STATUS:-0}"
printf '%s\n' "${OFFICIAL_OUTPUT}" | redact_modem_identifiers
log "Official script exit status: ${OFFICIAL_STATUS}; independent validation will determine the result."

if ! nmcli connection show soracom >/dev/null 2>&1; then
  fail "${EXIT_GENERAL}" "The official script did not create the required NetworkManager profile: soracom."
fi
CURRENT_APN="$(nmcli -g gsm.apn connection show soracom 2>/dev/null || true)"
if [[ "${CURRENT_APN}" != "${APN}" ]]; then
  log "Updating soracom profile APN from '${CURRENT_APN:-unset}' to '${APN}'."
  "${SUDO[@]}" nmcli connection modify soracom gsm.apn "${APN}"
else
  log "The existing soracom profile already has the requested APN."
fi
CURRENT_AUTOCONNECT="$(nmcli -g connection.autoconnect connection show soracom 2>/dev/null || true)"
if [[ "${CURRENT_AUTOCONNECT}" != "yes" ]]; then
  log "Enabling automatic connection for the soracom profile (was: ${CURRENT_AUTOCONNECT:-unset})."
  "${SUDO[@]}" nmcli connection modify soracom connection.autoconnect yes
fi
if [[ -e /etc/NetworkManager/dispatcher.d/90.soracom_route ]]; then
  log "Existing SORACOM route dispatcher rule is present; preserving it."
fi

log "Activating the soracom connection (NetworkManager may continue connecting after its command timeout)."
if ! timeout "${CONNECTION_WAIT_SECONDS}" "${SUDO[@]}" nmcli connection up soracom; then
  log "nmcli connection up did not complete within ${CONNECTION_WAIT_SECONDS}s or returned an error; checking the final state."
fi
sleep 5
ACTIVE_CONNECTIONS="$(nmcli connection show --active 2>&1 || true)"
DEVICE_STATUS="$(nmcli device status 2>&1 || true)"
MODEM_DETAILS="$(mmcli -m any 2>&1 || true)"
log "Active NetworkManager connections:"
printf '%s\n' "${ACTIVE_CONNECTIONS}" | redact_modem_identifiers
log "NetworkManager devices:"
printf '%s\n' "${DEVICE_STATUS}" | redact_modem_identifiers
log "ModemManager status:"
printf '%s\n' "${MODEM_DETAILS}" | redact_modem_identifiers

if ! grep -Eq '^soracom([[:space:]]|$)' <<<"${ACTIVE_CONNECTIONS}" || \
  ! grep -Eq '^(cdc-wdm0|wwan0)[[:space:]].*connected' <<<"${DEVICE_STATUS}"; then
  if ! grep -Eqi 'SIM[[:space:]]*\|.*(/SIM/|sim)' <<<"${MODEM_DETAILS}"; then
    fail "${EXIT_SIM_NOT_FOUND}" "No SIM was detected. Check SIM insertion and use 'mmcli -i 0' for local diagnostics."
  fi
  if grep -Eqi "registration:[[:space:]]*'?((searching)|(denied))'?|packet service state:[[:space:]]*'?detached'?" <<<"${MODEM_DETAILS}"; then
    log "The modem is not registered/attached. Confirm the SIM is activated, not suspended or terminated, and that the APN matches its plan. For plan-DU use --apn du.soracom.io."
    log "Run 'mmcli -i 0' locally if further SIM diagnostics are required; do not copy ICCID/IMSI values into tickets or logs."
    exit "${EXIT_NETWORK_NOT_REGISTERED}"
  fi
  fail "${EXIT_GENERAL}" "The soracom profile or modem device is not connected."
fi

if ! grep -Eqi "registration:[[:space:]]*'?(home|roaming)'?" <<<"${MODEM_DETAILS}" || \
  ! grep -Eqi "packet service state:[[:space:]]*'?attached'?" <<<"${MODEM_DETAILS}" || \
  ! grep -Eqi "state:[[:space:]]*'?connected'?" <<<"${MODEM_DETAILS}"; then
  fail "${EXIT_NETWORK_NOT_REGISTERED}" "The modem is connected but cellular registration or packet attachment is incomplete."
fi

IPV4_STATUS="$(ip -4 address show wwan0 2>&1 || true)"
log "wwan0 IPv4 status:"
printf '%s\n' "${IPV4_STATUS}" | redact_modem_identifiers
if ! grep -Eq 'inet[[:space:]]+[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' <<<"${IPV4_STATUS}"; then
  fail "${EXIT_NO_IPV4}" "wwan0 has no IPv4 address."
fi

if ! ping -c 4 pong.soracom.io; then
  fail "${EXIT_SORACOM_UNREACHABLE}" "SORACOM reachability check (pong.soracom.io) failed."
fi
log "SORACOM reachability check succeeded."
if ping -I wwan0 -c 4 8.8.8.8; then
  log "Optional external IPv4 reachability check succeeded."
else
  log "WARNING: Optional external IPv4 reachability check failed; this does not change the successful SORACOM result."
fi
if curl --interface wwan0 -4 --max-time 20 --fail --silent https://ifconfig.me >/dev/null; then
  log "Optional external DNS/HTTPS reachability check succeeded."
else
  log "WARNING: Optional external DNS/HTTPS reachability check failed; this does not change the successful SORACOM result."
fi

log "SUCCESS: SORACOM Onyx setup and primary communication checks completed."
log "The script did not change the default route or disconnect Wi-Fi/Ethernet. Reboot or reconnect the Onyx only if a later hardware issue requires it."
