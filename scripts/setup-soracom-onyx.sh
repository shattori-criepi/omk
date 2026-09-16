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
readonly EXIT_MODEM_AMBIGUOUS=8
readonly DEFAULT_APN="soracom.io"
readonly MODEM_WAIT_SECONDS=60
readonly CONNECTION_WAIT_SECONDS=30
readonly LEGACY_SORACOM_DISPATCHER_SHA256="fcb91353b7e55d644a0b22e032c0409521d65b18f4e4205306e562db080ba7d0"

APN="${SORACOM_APN:-${DEFAULT_APN}}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
# shellcheck source=lib/apt-helpers.sh
source "${SCRIPT_DIR}/lib/apt-helpers.sh"
# shellcheck source=lib/soracom-preservation-policy.sh
source "${SCRIPT_DIR}/lib/soracom-preservation-policy.sh"
RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6 || true)"
LOG_FILE=""
MODEM_DETAILS=""
MODEM_PATH=""
ONYX_USB_SYSPATH=""
CELLULAR_INTERFACE=""
USB_SYSFS_ROOT="${SORACOM_USB_SYSFS_ROOT:-/sys/bus/usb/devices}"
USB_SYSFS_ALLOWED_ROOT="${SORACOM_USB_SYSFS_ALLOWED_ROOT:-/sys/devices}"
SORACOM_DISPATCHER_PATH="${SORACOM_DISPATCHER_PATH:-/etc/NetworkManager/dispatcher.d/90.soracom_route}"
SORACOM_LEGACY_BACKUP_PATH="${SORACOM_LEGACY_BACKUP_PATH:-/etc/omk/soracom-route-legacy-${LEGACY_SORACOM_DISPATCHER_SHA256}.sh}"

log() {
  printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"
}

# Never copy modem identifiers into the persistent setup log.
redact_modem_identifiers() {
  sed -E \
    -e '/(imei|imsi|iccid)/I s/(:[[:space:]]*).*/\1[REDACTED]/' \
    -e '/(equipment id|subscriber identity|device id)/I s/(:[[:space:]]*).*/\1[REDACTED]/' \
    -e '/^[[:space:]]*((Numbers[[:space:]]*)?\|[[:space:]]*)?own[[:space:]]*:/I s/(:[[:space:]]*).*/\1[REDACTED]/'
}

show_diagnostics() {
  log "Diagnostic information follows. Modem identifiers are redacted."
  log "lsusb:"
  (lsusb 2>&1 || true) | redact_modem_identifiers
  log "/dev/ttyUSB*:"
  (ls -l /dev/ttyUSB* 2>/dev/null || true) | redact_modem_identifiers
  log "/dev/cdc-wdm*:"
  (ls -l /dev/cdc-wdm* 2>/dev/null || true) | redact_modem_identifiers
  log "WWAN links:"
  (ip -o link show 2>/dev/null | grep -E '(^|: )(wwan|wwp)' || true) | redact_modem_identifiers
  log "NetworkManager devices:"
  (nmcli device status 2>&1 || true) | redact_modem_identifiers
  log "ModemManager (last 50 journal lines):"
  (journalctl -u ModemManager -n 50 --no-pager 2>&1 || true) | redact_modem_identifiers
}

has_quectel_usb_id() {
  local vendor_file product_file

  for vendor_file in "${USB_SYSFS_ROOT}"/*/idVendor; do
    [[ -r "${vendor_file}" ]] || continue
    product_file="${vendor_file%/idVendor}/idProduct"
    if [[ -r "${product_file}" ]] && [[ "$(<"${vendor_file}")" == "2c7c" ]] &&
      [[ "$(<"${product_file}")" == "0125" ]]; then
      return 0
    fi
  done
  return 1
}

quectel_usb_syspaths() {
  local vendor_file product_file candidate resolved
  for vendor_file in "${USB_SYSFS_ROOT}"/*/idVendor; do
    [[ -r "${vendor_file}" ]] || continue
    product_file="${vendor_file%/idVendor}/idProduct"
    if [[ -r "${product_file}" ]] && [[ "$(<"${vendor_file}")" == 2c7c ]] && [[ "$(<"${product_file}")" == 0125 ]]; then
      candidate="${vendor_file%/idVendor}"
      resolved="$(readlink -e -- "${candidate}" 2>/dev/null || true)"
      [[ -n "${resolved}" && ( "${resolved}" == "${USB_SYSFS_ALLOWED_ROOT}" || "${resolved}" == "${USB_SYSFS_ALLOWED_ROOT}"/* ) ]] || continue
      [[ -r "${resolved}/idVendor" && -r "${resolved}/idProduct" ]] || continue
      [[ "$(<"${resolved}/idVendor")" == 2c7c && "$(<"${resolved}/idProduct")" == 0125 ]] || continue
      printf '%s\n' "${resolved}"
    fi
  done
}

modem_paths_from_list() {
  sed -n 's|.*\(/org/freedesktop/ModemManager1/Modem/[0-9][0-9]*\).*|\1|p' | sort -u
}

mmcli_kv_value() {
  local key="$1" value
  value="$(sed -n "s/^${key}[[:space:]]*[:=][[:space:]]*//p" | head -n 1)"
  value="${value#\"}"; value="${value%\"}"
  value="${value#\'}"; value="${value%\'}"
  printf '%s\n' "${value}"
}

path_is_within() {
  local path="$1" parent="$2"
  [[ "${path}" == "${parent}" || "${path}" == "${parent}"/* ]]
}

modem_matches_onyx_usb() {
  local modem_path="$1" usb_syspath="$2" kv device_syspath
  kv="$(mmcli -m "${modem_path}" --output-keyvalue 2>/dev/null || mmcli -m "${modem_path}" -K 2>/dev/null || true)"
  device_syspath="$(printf '%s\n' "${kv}" | mmcli_kv_value modem.generic.device)"
  [[ "${device_syspath}" == /* ]] || return 1
  device_syspath="$(readlink -e -- "${device_syspath}" 2>/dev/null || true)"
  [[ -n "${device_syspath}" ]] && path_is_within "${device_syspath}" "${usb_syspath}"
}

# A ModemManager object number, ttyUSB number, VID/PID, and list order are not
# identities. Select only the unique MM object whose reported sysfs device is
# under the unique supported USB device. Status 2 means it is not ready yet;
# status 3 means that automatic selection would be unsafe.
select_unique_onyx_modem() {
  local modem_list="$1" modem_path
  local -a usb_candidates modem_paths
  local -a matches=()
  mapfile -t usb_candidates < <(quectel_usb_syspaths)
  case "${#usb_candidates[@]}" in
    0) return 2 ;;
    1) ONYX_USB_SYSPATH="${usb_candidates[0]}" ;;
    *) return 3 ;;
  esac
  mapfile -t modem_paths < <(printf '%s\n' "${modem_list}" | modem_paths_from_list)
  case "${#modem_paths[@]}" in
    0) return 2 ;;
    1) ;;
    *) return 3 ;;
  esac
  for modem_path in "${modem_paths[@]}"; do
    modem_matches_onyx_usb "${modem_path}" "${ONYX_USB_SYSPATH}" && matches+=("${modem_path}")
  done
  [[ "${#matches[@]}" == 1 ]] || return 2
  MODEM_PATH="${matches[0]}"
}

single_soracom_nm_device() {
  local -a devices
  mapfile -t devices < <(nmcli -g GENERAL.DEVICES connection show soracom 2>/dev/null | sed '/^$/d' | sort -u)
  [[ "${#devices[@]}" == 1 && "${devices[0]}" != -- && "${devices[0]}" != *,* ]] || return 1
  printf '%s\n' "${devices[0]}"
}

modem_owns_nm_device() {
  local kv="$1" device="$2" ports pattern
  [[ "${device}" =~ ^[A-Za-z0-9_.-]+$ ]] || return 1
  ports="$(printf '%s\n' "${kv}" | mmcli_kv_value modem.generic.ports)"
  pattern="(^|[[:space:],])${device}([[:space:],(]|$)"
  [[ "${ports}" =~ ${pattern} ]]
}

modem_is_connected() {
  local kv state sim registration packet
  kv="$1"
  state="$(printf '%s\n' "${kv}" | mmcli_kv_value modem.generic.state)"
  sim="$(printf '%s\n' "${kv}" | mmcli_kv_value modem.generic.sim)"
  registration="$(printf '%s\n' "${kv}" | mmcli_kv_value modem.3gpp.registration-state)"
  packet="$(printf '%s\n' "${kv}" | mmcli_kv_value modem.3gpp.packet-service-state)"
  [[ "${state}" == connected && "${sim}" == /org/freedesktop/ModemManager1/SIM/* && ( "${registration}" == home || "${registration}" == roaming ) && "${packet}" == attached ]]
}

replay_quectel_udev_events() {
  local syspath
  command -v udevadm >/dev/null 2>&1 || return 0
  while IFS= read -r syspath; do
    [[ -n "${syspath}" ]] || continue
    timeout 30 "${SUDO[@]}" udevadm trigger --type=devices --action=add --parent-match="${syspath}" --settle
  done < <(quectel_usb_syspaths)
}

dispatcher_metadata_is_safe() {
  local path="$1" metadata
  [[ -f "${path}" && ! -L "${path}" && -x "${path}" ]] || return 1
  metadata="$(stat -c '%u:%g:%a' "${path}" 2>/dev/null || true)"
  [[ "${metadata}" == 0:0:755 ]]
}

file_has_legacy_hash_as_user() {
  local path="$1" digest
  digest="$(sha256sum "${path}" 2>/dev/null | awk '{print $1}')"
  [[ "${digest}" == "${LEGACY_SORACOM_DISPATCHER_SHA256}" ]]
}

file_has_legacy_hash_as_root() {
  local path="$1" digest
  digest="$("${SUDO[@]}" sha256sum "${path}" 2>/dev/null | awk '{print $1}')"
  [[ "${digest}" == "${LEGACY_SORACOM_DISPATCHER_SHA256}" ]]
}

dispatcher_has_legacy_hash() {
  local path="$1"
  dispatcher_metadata_is_safe "${path}" && file_has_legacy_hash_as_user "${path}"
}

dispatcher_is_current_omk() {
  local path="$1"
  dispatcher_metadata_is_safe "${path}" && cmp -s "${SCRIPT_DIR}/soracom-route-dispatcher" "${path}"
}

install_soracom_dispatcher() {
  local dispatcher_dir source="${SCRIPT_DIR}/soracom-route-dispatcher" metadata temporary
  [[ -f "${source}" ]] || return 1
  if [[ -e "${SORACOM_DISPATCHER_PATH}" ]]; then
    log 'Existing SORACOM route dispatcher is present; preserving it.'
    return 0
  fi
  dispatcher_dir="${SORACOM_DISPATCHER_PATH%/*}"
  "${SUDO[@]}" install -d -o root -g root -m 0755 "${dispatcher_dir}" || return 1
  temporary="$("${SUDO[@]}" mktemp "${dispatcher_dir}/.omk-soracom.XXXXXX")" || return 1
  if ! "${SUDO[@]}" install -o root -g root -m 0755 "${source}" "${temporary}"; then
    "${SUDO[@]}" rm -f -- "${temporary}" || true
    return 1
  fi
  metadata="$(stat -c '%u:%g:%a' "${temporary}")" || metadata=''
  if [[ ! -f "${temporary}" || -L "${temporary}" || ! -x "${temporary}" || "${metadata}" != 0:0:755 ]] || ! cmp -s "${source}" "${temporary}"; then
    "${SUDO[@]}" rm -f -- "${temporary}" || true
    return 1
  fi
  # Publish only a fully validated file; never overwrite a concurrently installed hook.
  if ! "${SUDO[@]}" mv -T --no-clobber "${temporary}" "${SORACOM_DISPATCHER_PATH}" || [[ -e "${temporary}" ]]; then
    "${SUDO[@]}" rm -f -- "${temporary}" || true
    return 1
  fi
  [[ -f "${SORACOM_DISPATCHER_PATH}" && -x "${SORACOM_DISPATCHER_PATH}" ]] || return 1
  log 'Installed and verified the SORACOM route dispatcher without cycling cellular.'
}

migrate_legacy_soracom_dispatcher() {
  local dispatcher_dir backup_dir source temporary backup_temporary
  source="${SCRIPT_DIR}/soracom-route-dispatcher"
  dispatcher_has_legacy_hash "${SORACOM_DISPATCHER_PATH}" || {
    log 'SORACOM dispatcher migration: legacy source no longer passed verification.'
    return 1
  }
  dispatcher_dir="${SORACOM_DISPATCHER_PATH%/*}"
  backup_dir="${SORACOM_LEGACY_BACKUP_PATH%/*}"
  "${SUDO[@]}" install -d -o root -g root -m 0755 "${backup_dir}" || {
    log 'SORACOM dispatcher migration: could not prepare the backup directory.'
    return 1
  }

  # The hash identifies known non-secret vendor content. Preserve it once for
  # upgrade auditability; never copy an unrecognized dispatcher into /etc/omk.
  if [[ ! -e "${SORACOM_LEGACY_BACKUP_PATH}" ]]; then
    backup_temporary="$("${SUDO[@]}" mktemp "${backup_dir}/.omk-soracom-legacy.XXXXXX")" || {
      log 'SORACOM dispatcher migration: could not stage the legacy backup.'
      return 1
    }
    if ! "${SUDO[@]}" install -o root -g root -m 0600 "${SORACOM_DISPATCHER_PATH}" "${backup_temporary}" ||
       ! file_has_legacy_hash_as_root "${backup_temporary}" ||
       ! "${SUDO[@]}" mv -T --no-clobber "${backup_temporary}" "${SORACOM_LEGACY_BACKUP_PATH}"; then
      "${SUDO[@]}" rm -f -- "${backup_temporary}" || true
      log 'SORACOM dispatcher migration: legacy backup staging or verification failed.'
      return 1
    fi
  fi
  [[ -f "${SORACOM_LEGACY_BACKUP_PATH}" && ! -L "${SORACOM_LEGACY_BACKUP_PATH}" &&
     "$("${SUDO[@]}" stat -c '%u:%g:%a' "${SORACOM_LEGACY_BACKUP_PATH}" 2>/dev/null || true)" == 0:0:600 ]] || {
    log 'SORACOM dispatcher migration: existing legacy backup does not meet the root-only contract.'
    return 1
  }
  file_has_legacy_hash_as_root "${SORACOM_LEGACY_BACKUP_PATH}" || {
    log 'SORACOM dispatcher migration: existing legacy backup hash did not match.'
    return 1
  }

  temporary="$("${SUDO[@]}" mktemp "${dispatcher_dir}/.omk-soracom.XXXXXX")" || {
    log 'SORACOM dispatcher migration: could not stage the replacement dispatcher.'
    return 1
  }
  if ! "${SUDO[@]}" install -o root -g root -m 0755 "${source}" "${temporary}" ||
     ! dispatcher_is_current_omk "${temporary}" ||
     ! dispatcher_has_legacy_hash "${SORACOM_DISPATCHER_PATH}" ||
     ! "${SUDO[@]}" mv -T -f "${temporary}" "${SORACOM_DISPATCHER_PATH}"; then
    "${SUDO[@]}" rm -f -- "${temporary}" || true
    log 'SORACOM dispatcher migration: replacement staging, source recheck, or atomic replacement failed.'
    return 1
  fi
  dispatcher_is_current_omk "${SORACOM_DISPATCHER_PATH}" || {
    log 'SORACOM dispatcher migration: replacement dispatcher verification failed.'
    return 1
  }
  log 'Migrated the verified legacy SORACOM dispatcher without cycling cellular.'
}

ensure_current_soracom_dispatcher() {
  if [[ ! -e "${SORACOM_DISPATCHER_PATH}" ]]; then
    [[ "${APN}" == *soracom.io ]] || return 1
    install_soracom_dispatcher
  elif dispatcher_is_current_omk "${SORACOM_DISPATCHER_PATH}"; then
    return 0
  elif dispatcher_has_legacy_hash "${SORACOM_DISPATCHER_PATH}"; then
    migrate_legacy_soracom_dispatcher
  else
    return 1
  fi
}

repair_dispatcher_routes_without_reconnect() {
  local cellular_active="$1" dispatcher_installed="$2" device interface
  [[ "${cellular_active}" == yes && "${dispatcher_installed}" == yes ]] || return 0
  device="$(nmcli -g GENERAL.DEVICES connection show soracom)" || return 1
  [[ -n "${device}" && "${device}" != -- && "${device}" != *,* ]] || return 1
  interface="$(nmcli -g GENERAL.IP-IFACE device show "${device}")" || return 1
  [[ -n "${interface}" && "${interface}" != -- ]] || return 1
  "${SUDO[@]}" "${SORACOM_DISPATCHER_PATH}" "${interface}" up || return 1
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

# Dispatcher-only repair must precede package installation and every service operation.
# If NM exists but cannot be queried, do not risk updating a live transport.
if command -v nmcli >/dev/null 2>&1; then
  ACTIVE_CONNECTIONS="$(nmcli -g NAME connection show --active)" ||
    fail "${EXIT_GENERAL}" 'Cannot establish active connections before package changes.'
  if grep -Fxq soracom <<<"${ACTIVE_CONNECTIONS}"; then
    ensure_current_soracom_dispatcher || fail "${EXIT_GENERAL}" 'Existing dispatcher is not verified OMK-managed code; preserved without execution. Review it before repairing routes.'
    repair_dispatcher_routes_without_reconnect yes yes || fail "${EXIT_GENERAL}" 'Dispatcher route repair failed; existing routes were not replaced.'
    log 'Active soracom preserved: no package, service, profile or reconnect operations performed.'
    exit 0
  fi
fi

mapfile -t ONYX_USB_CANDIDATES < <(quectel_usb_syspaths)
if [[ "${#ONYX_USB_CANDIDATES[@]}" == 0 ]]; then
  log "No SORACOM Onyx/Quectel modem was detected. Connect the Onyx, wait for USB enumeration, then rerun."
  show_diagnostics
  exit "${EXIT_ONYX_NOT_FOUND}"
fi
if [[ "${#ONYX_USB_CANDIDATES[@]}" != 1 ]]; then
  log 'Multiple supported Quectel USB candidates were detected; refusing order-dependent modem selection.'
  show_diagnostics
  exit "${EXIT_MODEM_AMBIGUOUS}"
fi
ONYX_USB_SYSPATH="${ONYX_USB_CANDIDATES[0]}"
log 'One supported Quectel USB candidate was detected; waiting for its ModemManager object.'

# The vendor's first-install script calls ifconfig; our dispatcher uses Python.
PACKAGES=(network-manager modemmanager usb-modeswitch usbutils curl ca-certificates net-tools python3)
log "Updating package indexes and installing: ${PACKAGES[*]}"
omk_apt "${SUDO[@]}" apt-get update
omk_apt "${SUDO[@]}" apt-get install -y "${PACKAGES[@]}"

command -v nmcli >/dev/null 2>&1 || fail "${EXIT_GENERAL}" "nmcli is unavailable after installing network-manager."
command -v mmcli >/dev/null 2>&1 || fail "${EXIT_GENERAL}" "mmcli is unavailable after installing modemmanager."

log "Enabling and starting NetworkManager and ModemManager."
"${SUDO[@]}" systemctl enable --now NetworkManager
"${SUDO[@]}" systemctl enable --now ModemManager
systemctl is-active --quiet NetworkManager || fail "${EXIT_GENERAL}" "NetworkManager is not active."
systemctl is-active --quiet ModemManager || fail "${EXIT_GENERAL}" "ModemManager is not active."
log "NetworkManager: $(systemctl is-active NetworkManager); ModemManager: $(systemctl is-active ModemManager)"
log "ModemManager enabled state: $(systemctl is-enabled ModemManager 2>&1 || true)"

MODEM_LIST="$(mmcli -L 2>&1 || true)"
if ! grep -q '/Modem/' <<<"${MODEM_LIST}" && has_quectel_usb_id; then
  log 'Quectel modem is already attached but not yet known to ModemManager.'
  log 'Replaying udev events for the detected Onyx device.'
  replay_quectel_udev_events
fi
log "Waiting up to ${MODEM_WAIT_SECONDS}s for ModemManager to recognize the modem."
deadline=$((SECONDS + MODEM_WAIT_SECONDS))
while ((SECONDS < deadline)); do
  MODEM_LIST="$(mmcli -L 2>&1 || true)"
  if select_unique_onyx_modem "${MODEM_LIST}"; then
    break
  else
    selection_status=$?
  fi
  if [[ "${selection_status}" == 3 ]]; then
    log 'Multiple ModemManager candidates were detected; refusing order-dependent modem selection.'
    show_diagnostics
    exit "${EXIT_MODEM_AMBIGUOUS}"
  fi
  sleep 3
done
if [[ -z "${MODEM_PATH:-}" ]]; then
  log "ModemManager did not recognize a modem within ${MODEM_WAIT_SECONDS}s."
  show_diagnostics
  exit "${EXIT_MODEM_NOT_FOUND}"
fi
log "Recognized modem: $(printf '%s\n' "${MODEM_LIST}" | redact_modem_identifiers)"
MODEM_DETAILS="$(mmcli -m "${MODEM_PATH}" 2>&1 || true)"
log "Initial modem status:"
printf '%s\n' "${MODEM_DETAILS}" | redact_modem_identifiers

SORACOM_PROFILE_EXISTS=no
SORACOM_ACTIVE=no
nmcli connection show soracom >/dev/null 2>&1 && SORACOM_PROFILE_EXISTS=yes
nmcli -g NAME connection show --active 2>/dev/null | grep -Fxq soracom && SORACOM_ACTIVE=yes || true
DISPATCHER_INSTALLED=no
if [[ "${APN}" == *soracom.io ]]; then
  ensure_current_soracom_dispatcher || fail "${EXIT_GENERAL}" 'Dispatcher installation failed or existing dispatcher is unverified.'
  DISPATCHER_INSTALLED=yes
fi
repair_dispatcher_routes_without_reconnect "${SORACOM_ACTIVE}" "${DISPATCHER_INSTALLED}" || fail "${EXIT_GENERAL}" 'Dispatcher route repair failed.'

DISPATCHER_EXISTS=no
[[ -e "${SORACOM_DISPATCHER_PATH}" ]] && DISPATCHER_EXISTS=yes
OFFICIAL_REQUIRED="$(soracom_official_setup_required "${SORACOM_PROFILE_EXISTS}" "${SORACOM_ACTIVE}" "${DISPATCHER_EXISTS}")" ||
  fail "${EXIT_GENERAL}" 'Inconsistent active cellular state: active soracom has no profile.'
if [[ "${OFFICIAL_REQUIRED}" == no ]]; then
  log 'Existing soracom profile is available; skipping the official setup script to preserve the current cellular connection.'
else
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
  OFFICIAL_OUTPUT="$("${SUDO[@]}" "${OFFICIAL_SCRIPT}" 2>&1)" || OFFICIAL_STATUS=$?
  OFFICIAL_STATUS="${OFFICIAL_STATUS:-0}"
  printf '%s\n' "${OFFICIAL_OUTPUT}" | redact_modem_identifiers
  log "Official script exit status: ${OFFICIAL_STATUS}; independent validation will determine the result."
fi

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
[[ ! -e "${SORACOM_DISPATCHER_PATH}" ]] || log 'Verified OMK SORACOM route dispatcher rule is present.'

if nmcli -g NAME connection show --active | grep -Fxq soracom; then
  log 'The soracom connection is already active; preserving the current cellular connection.'
else
  log "Activating the inactive soracom connection (NetworkManager may continue connecting after its command timeout)."
  if ! timeout "${CONNECTION_WAIT_SECONDS}" "${SUDO[@]}" nmcli connection up soracom; then
    log "nmcli connection up did not complete within ${CONNECTION_WAIT_SECONDS}s or returned an error; checking the final state."
  fi
fi
sleep 5
ACTIVE_CONNECTIONS="$(nmcli connection show --active 2>&1 || true)"
DEVICE_STATUS="$(nmcli device status 2>&1 || true)"
MODEM_DETAILS="$(mmcli -m "${MODEM_PATH}" 2>&1 || true)"
log "Active NetworkManager connections:"
printf '%s\n' "${ACTIVE_CONNECTIONS}" | redact_modem_identifiers
log "NetworkManager devices:"
printf '%s\n' "${DEVICE_STATUS}" | redact_modem_identifiers
log "ModemManager status:"
printf '%s\n' "${MODEM_DETAILS}" | redact_modem_identifiers

MODEM_KV="$(mmcli -m "${MODEM_PATH}" --output-keyvalue 2>/dev/null || mmcli -m "${MODEM_PATH}" -K 2>/dev/null || true)"
SIM_PATH="$(printf '%s\n' "${MODEM_KV}" | mmcli_kv_value modem.generic.sim)"
[[ "${SIM_PATH}" == /org/freedesktop/ModemManager1/SIM/* ]] || fail "${EXIT_SIM_NOT_FOUND}" 'No SIM object was detected.'
modem_is_connected "${MODEM_KV}" || fail "${EXIT_NETWORK_NOT_REGISTERED}" 'Cellular state is not connected/registered/attached.'
nmcli -g NAME connection show --active | grep -Fxq soracom || fail "${EXIT_GENERAL}" 'The soracom profile is not active.'
NM_DEVICE="$(single_soracom_nm_device)" || fail "${EXIT_GENERAL}" 'The active soracom connection does not have exactly one NetworkManager device.'
[[ -n "${NM_DEVICE}" && "${NM_DEVICE}" != -- ]] || fail "${EXIT_GENERAL}" 'The active soracom connection has no NetworkManager device.'
nmcli -g GENERAL.STATE device show "${NM_DEVICE}" 2>/dev/null | grep -Eq '^100([[:space:]]|$)' || fail "${EXIT_GENERAL}" "The soracom NetworkManager device is not connected: ${NM_DEVICE}."
modem_owns_nm_device "${MODEM_KV}" "${NM_DEVICE}" || fail "${EXIT_GENERAL}" 'The active soracom connection is not owned by the selected Onyx modem.'
CELLULAR_INTERFACE="$(nmcli -g GENERAL.IP-IFACE device show "${NM_DEVICE}" 2>/dev/null)"
[[ -n "${CELLULAR_INTERFACE}" && "${CELLULAR_INTERFACE}" != -- && "${CELLULAR_INTERFACE}" != *,* ]] || fail "${EXIT_GENERAL}" 'The selected Onyx modem has no unique IP interface.'

IPV4_STATUS="$(ip -4 address show "${CELLULAR_INTERFACE}" 2>&1 || true)"
log "Cellular IPv4 status:"
printf '%s\n' "${IPV4_STATUS}" | redact_modem_identifiers
if ! grep -Eq 'inet[[:space:]]+[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' <<<"${IPV4_STATUS}"; then
  fail "${EXIT_NO_IPV4}" 'The selected Onyx IP interface has no IPv4 address.'
fi

if ! ping -I "${CELLULAR_INTERFACE}" -c 4 pong.soracom.io; then
  fail "${EXIT_SORACOM_UNREACHABLE}" "SORACOM reachability check (pong.soracom.io) failed."
fi
log "SORACOM reachability check succeeded."
if ping -I "${CELLULAR_INTERFACE}" -c 4 8.8.8.8; then
  log "Optional external IPv4 reachability check succeeded."
else
  log "WARNING: Optional external IPv4 reachability check failed; this does not change the successful SORACOM result."
fi
if curl --interface "${CELLULAR_INTERFACE}" -4 --max-time 20 --fail --silent https://ifconfig.me >/dev/null; then
  log "Optional external DNS/HTTPS reachability check succeeded."
else
  log "WARNING: Optional external DNS/HTTPS reachability check failed; this does not change the successful SORACOM result."
fi

log "SUCCESS: SORACOM Onyx setup and primary communication checks completed."
log "The script did not change the default route or disconnect Wi-Fi/Ethernet. Reboot or reconnect the Onyx only if a later hardware issue requires it."
