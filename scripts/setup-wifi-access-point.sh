#!/usr/bin/env bash

# Create or safely update an NetworkManager Wi-Fi access-point profile for OMK.
# The generated SSID suffix is the first six uppercase hexadecimal characters of
# SHA-256(/etc/machine-id), so it is stable per host without exposing the raw ID.
set -euo pipefail

readonly DEFAULT_CONNECTION_NAME="omk-ap"
readonly DEFAULT_INTERFACE="wlan0"
readonly DEFAULT_IPV4_ADDRESS="192.168.50.1/24"
readonly FIREWALL_CONFIG_DIR="/etc/omk"
readonly FIREWALL_CONFIG_PATH="${FIREWALL_CONFIG_DIR}/omk-ap-isolation.nft"
readonly FIREWALL_UNIT_PATH="/etc/systemd/system/omk-ap-isolation.service"
readonly DNSMASQ_SHARED_DIR="/etc/NetworkManager/dnsmasq-shared.d"
readonly DNSMASQ_ISOLATION_PATH="${DNSMASQ_SHARED_DIR}/omk-ap-isolation.conf"

CONNECTION_NAME="${OMK_AP_CONNECTION_NAME:-${DEFAULT_CONNECTION_NAME}}"
INTERFACE="${OMK_AP_INTERFACE:-${DEFAULT_INTERFACE}}"
IPV4_ADDRESS="${OMK_AP_IPV4_ADDRESS:-${DEFAULT_IPV4_ADDRESS}}"
SSID="${OMK_AP_SSID:-}"
PSK="${OMK_AP_PSK:-}"
ACTIVATE="${OMK_AP_ACTIVATE:-no}"
DRY_RUN=no
PRINT_CONFIG=no
ASSUME_YES="${OMK_AP_CONFIRM:-no}"
EXISTING=no
NEEDS_PSK=no
LOG_FILE=""

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
RUN_USER="${SUDO_USER:-$(id -un)}"
SUDO=()

usage() {
  cat <<'EOF'
Usage: ./scripts/setup-wifi-access-point.sh [OPTIONS]

Create or safely update a NetworkManager Wi-Fi AP profile. By default this does
not activate the AP, so an SSH connection is not switched unexpectedly.

Options:
  --dry-run       Show the proposed configuration without changing anything.
  --print-config  Print the resolved configuration (the PSK is always masked).
  --activate      After confirmation, activate the AP profile.
  --yes           Confirm profile changes and --activate non-interactively.
  --help, -h      Show this help.

Configuration can be supplied through environment variables:
  OMK_AP_CONNECTION_NAME  (default: omk-ap)
  OMK_AP_INTERFACE        (default: wlan0)
  OMK_AP_IPV4_ADDRESS     (default: 192.168.50.1/24)
  OMK_AP_SSID             (optional)
  OMK_AP_PSK              (optional; never put this in shell history or Git)
  OMK_AP_ACTIVATE=yes     (same as --activate)
  OMK_AP_CONFIRM=yes      (same as --yes)

When a PSK is needed and OMK_AP_PSK is unset, it is requested with hidden input.
EOF
}

log() {
  printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

is_yes() {
  [[ "$1" == "yes" || "$1" == "true" || "$1" == "1" ]]
}

require_safe_text() {
  local value="$1" label="$2"
  [[ -n "${value}" ]] || fail "${label} must not be empty."
  [[ "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || fail "${label} must not contain a newline."
}

run_privileged() {
  if [[ "${DRY_RUN}" == yes ]]; then
    log "DRY-RUN: would run NetworkManager change."
    return 0
  fi
  "${SUDO[@]}" "$@"
}

install_ap_isolation_firewall() {
  # NetworkManager's `shared` mode supplies DHCP/DNS, but may also add NAT and
  # forwarding rules. Docker publishes host ports by DNATing them to a bridge,
  # so admit only conntrack-DNAT traffic before dropping all other forwarding
  # from the AP. This permits MQTT/Dashboard host-port publishing without
  # depending on Docker's generated bridge name.
  local temporary_config temporary_unit
  if [[ "${DRY_RUN}" != yes ]]; then
    command -v nft >/dev/null 2>&1 || fail "nft is required to isolate OMK AP clients. Install the nftables package and re-run this script."
  fi
  temporary_config="$(mktemp)"
  temporary_unit="$(mktemp)"
  trap 'rm -f -- "${temporary_config}" "${temporary_unit}"' RETURN
  cat > "${temporary_config}" <<EOF
table inet omk_ap_isolation {
  chain forward {
    type filter hook forward priority -100; policy accept;
    iifname "${INTERFACE}" ct status dnat counter accept comment "Allow Gateway Docker published ports after DNAT"
    iifname "${INTERFACE}" oifname != "${INTERFACE}" counter drop comment "OMK AP clients must not route outside the AP"
  }
}
EOF
  cat > "${temporary_unit}" <<EOF
[Unit]
Description=OMK AP client Internet isolation
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStartPre=-/usr/sbin/nft delete table inet omk_ap_isolation
ExecStart=/usr/sbin/nft -f ${FIREWALL_CONFIG_PATH}
ExecReload=-/usr/sbin/nft delete table inet omk_ap_isolation
ExecReload=/usr/sbin/nft -f ${FIREWALL_CONFIG_PATH}
ExecStop=-/usr/sbin/nft delete table inet omk_ap_isolation
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  if [[ "${DRY_RUN}" == yes ]]; then
    log "DRY-RUN: would install nftables AP isolation (forward ${INTERFACE} -> any non-${INTERFACE} interface: drop)."
  else
    "${SUDO[@]}" install -d -o root -g root -m 0755 "${FIREWALL_CONFIG_DIR}"
    "${SUDO[@]}" install -o root -g root -m 0644 "${temporary_config}" "${FIREWALL_CONFIG_PATH}"
    "${SUDO[@]}" install -o root -g root -m 0644 "${temporary_unit}" "${FIREWALL_UNIT_PATH}"
    "${SUDO[@]}" systemctl daemon-reload
    # Restart even when already active. This both applies the revised rules and
    # replaces any operator-added temporary rule in this dedicated table.
    "${SUDO[@]}" systemctl enable omk-ap-isolation.service
    "${SUDO[@]}" systemctl restart omk-ap-isolation.service
    "${SUDO[@]}" nft list table inet omk_ap_isolation >/dev/null
    log "Installed nftables AP isolation: Docker-published DNAT traffic is allowed; other forwarded traffic from ${INTERFACE} is dropped."
  fi
  rm -f -- "${temporary_config}" "${temporary_unit}"
  trap - RETURN
}

install_ap_dns_isolation() {
  # `ipv4.method shared` starts NetworkManager's dnsmasq for DHCP. Without
  # this, an AP client could use that local resolver to make upstream DNS
  # queries even though its routed IP packets are blocked.
  if [[ "${DRY_RUN}" == yes ]]; then
    log "DRY-RUN: would disable upstream DNS resolution for NetworkManager shared connections."
    return
  fi
  "${SUDO[@]}" install -d -o root -g root -m 0755 "${DNSMASQ_SHARED_DIR}"
  printf '%s\n' '# OMK AP is a local-only network: retain DHCP, never proxy DNS upstream.' 'no-resolv' \
    | "${SUDO[@]}" tee "${DNSMASQ_ISOLATION_PATH}" >/dev/null
  "${SUDO[@]}" chmod 0644 "${DNSMASQ_ISOLATION_PATH}"
  log "Disabled upstream DNS resolution for NetworkManager shared connections. It applies when the AP is next activated."
}

nm_value() {
  nmcli -g "$1" connection show "${CONNECTION_NAME}" 2>/dev/null | head -n 1 || true
}

generate_ssid() {
  local source digest
  if [[ -r /etc/machine-id ]]; then
    source="$(tr -d '[:space:]' < /etc/machine-id)"
  elif [[ -r "/sys/class/net/${INTERFACE}/address" ]]; then
    source="$(tr -d '[:space:]' < "/sys/class/net/${INTERFACE}/address")"
  else
    fail "Cannot generate an SSID: neither /etc/machine-id nor ${INTERFACE}'s permanent address is available. Set OMK_AP_SSID."
  fi
  digest="$(printf '%s' "${source}" | sha256sum | awk '{print toupper(substr($1, 1, 6))}')"
  SSID="OMK-${digest}"
}

print_config() {
  printf 'Connection profile: %s\nSSID: %s\nInterface: %s\nIPv4 address: %s\nAutoconnect: yes\nActivate AP: %s\nPSK: %s\n' \
    "${CONNECTION_NAME}" "${SSID}" "${INTERFACE}" "${IPV4_ADDRESS}" "${ACTIVATE}" \
    "$([[ -n "${PSK}" ]] && printf '[MASKED]' || printf '[UNSET]')"
}

confirm() {
  local prompt="$1" answer
  if is_yes "${ASSUME_YES}"; then
    return 0
  fi
  [[ -t 0 ]] || fail "${prompt} requires an interactive terminal. Re-run locally or set OMK_AP_CONFIRM=yes after reviewing the change."
  read -r -p "${prompt} [y/N] " answer
  [[ "${answer}" =~ ^[Yy]([Ee][Ss])?$ ]]
}

read_psk() {
  [[ -n "${PSK}" ]] && return 0
  [[ -t 0 ]] || fail "A WPA2-PSK is required. Set OMK_AP_PSK securely or run from an interactive terminal."
  read -r -s -p 'WPA2-PSK (input is hidden): ' PSK
  printf '\n'
  [[ -n "${PSK}" ]] || fail "WPA2-PSK must not be empty."
}

configure_wpa2_psk() {
  # nmcli's editor accepts commands on standard input.  Keep the PSK out of
  # argv (including sudo's argv), suppress editor output so it cannot enter
  # this script's tee-backed log, and discard the in-memory secret afterwards.
  if ! {
    # Supplying `set` without a value makes the editor read the value on its
    # next stdin line, rather than parsing it as an editor command.
    printf '%s\n' 'set 802-11-wireless-security.psk'
    printf '%s\n' "${PSK}"
    printf '%s\n' save quit
  } | "${SUDO[@]}" nmcli connection edit "${CONNECTION_NAME}" >/dev/null 2>&1; then
    unset PSK
    fail "NetworkManager could not save the WPA2-PSK through its standard-input editor. The profile may need local inspection."
  fi
  unset PSK
}

network_manager_ready() {
  command -v nmcli >/dev/null 2>&1 || fail "nmcli was not found. Install and enable NetworkManager first."
  nmcli general status >/dev/null 2>&1 || fail "NetworkManager is unavailable. Check: sudo systemctl status NetworkManager --no-pager"
  nmcli device show "${INTERFACE}" >/dev/null 2>&1 || fail "Wi-Fi interface ${INTERFACE} was not found by NetworkManager."
}

check_ap_support() {
  local ap_capability=""
  ap_capability="$(nmcli -g WIFI-PROPERTIES.AP device show "${INTERFACE}" 2>/dev/null | head -n 1 || true)"
  if [[ "${ap_capability}" == "no" ]]; then
    fail "${INTERFACE} reports no AP-mode support. Check the Wi-Fi adapter/driver and 'iw list' on the Raspberry Pi."
  fi
  if command -v iw >/dev/null 2>&1 && ! iw list 2>/dev/null | grep -Eq '^[[:space:]]*\* AP$'; then
    fail "iw reports that ${INTERFACE}'s Wi-Fi hardware/driver does not support AP mode."
  fi
}

show_ssh_risk() {
  local ssh_peer route current_connection
  current_connection="$(nmcli -g GENERAL.CONNECTION device show "${INTERFACE}" 2>/dev/null | head -n 1 || true)"
  log "Current NetworkManager connection on ${INTERFACE}: ${current_connection:-none}"
  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    ssh_peer="${SSH_CONNECTION%% *}"
    route="$(ip route get "${ssh_peer}" 2>/dev/null || true)"
    log "Route to SSH peer: ${route:-unavailable}"
    if [[ "${route}" == *" dev ${INTERFACE} "* ]]; then
      log "WARNING: this SSH session appears to route through ${INTERFACE}. Activating the AP can disconnect this session."
    fi
  fi
}

show_wan_reference() {
  log "Active NetworkManager connections (reference before AP activation):"
  nmcli -f NAME,DEVICE,TYPE connection show --active 2>&1 || true
  log "Current default routes (reference):"
  ip route show default 2>&1 || true
}

snapshot_existing() {
  local snapshot
  snapshot="${OMK_ROOT}/logs/setup/wifi-ap-before-${CONNECTION_NAME//[^A-Za-z0-9_.-]/_}-$(date '+%Y%m%d-%H%M%S').txt"
  umask 077
  {
    printf '# NetworkManager profile snapshot; no secrets requested or shown.\n'
    for property in connection.id connection.interface-name connection.autoconnect 802-11-wireless.mode 802-11-wireless.ssid 802-11-wireless-security.key-mgmt ipv4.method ipv4.addresses ipv6.method; do
      printf '%s=%s\n' "${property}" "$(nm_value "${property}")"
    done
  } > "${snapshot}"
  log "Saved non-secret pre-change profile snapshot: ${snapshot}"
}

is_active() {
  nmcli -g NAME connection show --active 2>/dev/null | grep -Fxq "${CONNECTION_NAME}"
}

while (($# > 0)); do
  case "$1" in
    --dry-run) DRY_RUN=yes ;;
    --print-config) PRINT_CONFIG=yes ;;
    --activate) ACTIVATE=yes ;;
    --yes) ASSUME_YES=yes ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; fail "Unknown option: $1" ;;
  esac
  shift
done

is_yes "${ACTIVATE}" || [[ "${ACTIVATE}" == "no" || "${ACTIVATE}" == "false" || "${ACTIVATE}" == "0" ]] || fail "OMK_AP_ACTIVATE must be yes or no."
require_safe_text "${CONNECTION_NAME}" "Connection name"
require_safe_text "${INTERFACE}" "Interface"
require_safe_text "${IPV4_ADDRESS}" "IPv4 address"
[[ "${IPV4_ADDRESS}" == */* ]] || fail "IPv4 address must include a prefix length, for example 192.168.50.1/24."
[[ -z "${SSID}" ]] || require_safe_text "${SSID}" "SSID"

mkdir -p "${OMK_ROOT}/logs/setup"
LOG_FILE="${OMK_ROOT}/logs/setup/wifi-ap-$(date '+%Y%m%d-%H%M%S').log"
umask 077
exec > >(tee -a "${LOG_FILE}") 2>&1
log "Wi-Fi AP setup started (repository root: ${OMK_ROOT}; target user: ${RUN_USER})."
log "Log file: ${LOG_FILE}"

if [[ "${PRINT_CONFIG}" == yes ]]; then
  # Configuration display must also work on a development host without nmcli.
  if [[ -z "${SSID}" ]] && command -v nmcli >/dev/null 2>&1 && nmcli connection show "${CONNECTION_NAME}" >/dev/null 2>&1; then
    SSID="$(nm_value 802-11-wireless.ssid)"
  fi
  [[ -n "${SSID}" ]] || generate_ssid
  require_safe_text "${SSID}" "SSID"
  print_config
  exit 0
fi

if [[ "${DRY_RUN}" != yes ]]; then
  if ((EUID == 0)); then
    SUDO=()
  else
    command -v sudo >/dev/null 2>&1 || fail "sudo is required when this script is not run as root."
    SUDO=(sudo)
  fi
  network_manager_ready
  if nmcli connection show "${CONNECTION_NAME}" >/dev/null 2>&1; then
    EXISTING=yes
    [[ -n "${SSID}" ]] || SSID="$(nm_value 802-11-wireless.ssid)"
  fi
else
  # Dry-run intentionally does not require NetworkManager, a Wi-Fi device, or a PSK.
  EXISTING="$(command -v nmcli >/dev/null 2>&1 && nmcli connection show "${CONNECTION_NAME}" >/dev/null 2>&1 && printf yes || printf no)"
  if [[ "${EXISTING}" == yes && -z "${SSID}" ]]; then SSID="$(nm_value 802-11-wireless.ssid)"; fi
fi

[[ -n "${SSID}" ]] || generate_ssid
require_safe_text "${SSID}" "SSID"

if [[ "${DRY_RUN}" == yes ]]; then
  if [[ "${EXISTING}" == yes ]]; then
    log "DRY-RUN: existing profile ${CONNECTION_NAME} would be inspected; no NetworkManager change or AP activation will occur."
  else
    log "DRY-RUN: profile ${CONNECTION_NAME} would be created if confirmed; no NetworkManager change or AP activation will occur."
  fi
  print_config
  exit 0
fi

check_ap_support
show_ssh_risk

CURRENT_ID="$(nm_value connection.id)"
CURRENT_INTERFACE="$(nm_value connection.interface-name)"
CURRENT_AUTOCONNECT="$(nm_value connection.autoconnect)"
CURRENT_MODE="$(nm_value 802-11-wireless.mode)"
CURRENT_SSID="$(nm_value 802-11-wireless.ssid)"
CURRENT_KEY_MGMT="$(nm_value 802-11-wireless-security.key-mgmt)"
CURRENT_IPV4_METHOD="$(nm_value ipv4.method)"
CURRENT_IPV4_ADDRESS="$(nm_value ipv4.addresses)"
CURRENT_IPV6_METHOD="$(nm_value ipv6.method)"

CHANGES=()
if [[ "${EXISTING}" == no ]]; then
  CHANGES=("create connection profile" "connection.id=${CONNECTION_NAME}" "connection.interface-name=${INTERFACE}" "connection.autoconnect=yes" "802-11-wireless.mode=ap" "802-11-wireless.ssid=${SSID}" "802-11-wireless-security.key-mgmt=wpa-psk" "ipv4.method=shared" "ipv4.addresses=${IPV4_ADDRESS}" "ipv6.method=disabled")
  NEEDS_PSK=yes
else
  [[ "${CURRENT_ID}" == "${CONNECTION_NAME}" ]] || CHANGES+=("connection.id: ${CURRENT_ID:-unset} -> ${CONNECTION_NAME}")
  [[ "${CURRENT_INTERFACE}" == "${INTERFACE}" ]] || CHANGES+=("connection.interface-name: ${CURRENT_INTERFACE:-unset} -> ${INTERFACE}")
  [[ "${CURRENT_AUTOCONNECT}" == yes ]] || CHANGES+=("connection.autoconnect: ${CURRENT_AUTOCONNECT:-unset} -> yes")
  [[ "${CURRENT_MODE}" == ap ]] || CHANGES+=("802-11-wireless.mode: ${CURRENT_MODE:-unset} -> ap")
  [[ "${CURRENT_SSID}" == "${SSID}" ]] || CHANGES+=("802-11-wireless.ssid: ${CURRENT_SSID:-unset} -> ${SSID}")
  if [[ "${CURRENT_KEY_MGMT}" != wpa-psk ]]; then CHANGES+=("802-11-wireless-security.key-mgmt: ${CURRENT_KEY_MGMT:-unset} -> wpa-psk"); NEEDS_PSK=yes; fi
  [[ "${CURRENT_IPV4_METHOD}" == shared ]] || CHANGES+=("ipv4.method: ${CURRENT_IPV4_METHOD:-unset} -> shared")
  [[ "${CURRENT_IPV4_ADDRESS}" == "${IPV4_ADDRESS}" ]] || CHANGES+=("ipv4.addresses: ${CURRENT_IPV4_ADDRESS:-unset} -> ${IPV4_ADDRESS}")
  [[ "${CURRENT_IPV6_METHOD}" == disabled ]] || CHANGES+=("ipv6.method: ${CURRENT_IPV6_METHOD:-unset} -> disabled")
fi

if ((${#CHANGES[@]} > 0)); then
  log "Planned profile changes (PSK is never displayed):"
  printf '  - %s\n' "${CHANGES[@]}"
  confirm "Apply these NetworkManager profile changes?" || { log "No changes applied."; exit 0; }
  [[ "${EXISTING}" == no ]] || snapshot_existing
  [[ "${NEEDS_PSK}" == yes ]] && read_psk
  if [[ "${EXISTING}" == no ]]; then
    run_privileged nmcli connection add type wifi ifname "${INTERFACE}" con-name "${CONNECTION_NAME}" autoconnect yes ssid "${SSID}"
  fi
  run_privileged nmcli connection modify "${CONNECTION_NAME}" connection.interface-name "${INTERFACE}" connection.autoconnect yes 802-11-wireless.mode ap 802-11-wireless.ssid "${SSID}" 802-11-wireless.band bg ipv4.method shared ipv4.addresses "${IPV4_ADDRESS}" ipv6.method disabled
  if [[ "${NEEDS_PSK}" == yes ]]; then
    run_privileged nmcli connection modify "${CONNECTION_NAME}" 802-11-wireless-security.key-mgmt wpa-psk
    configure_wpa2_psk
  fi
  if [[ "${EXISTING}" == yes ]]; then log "NetworkManager profile was updated; AP-side IPv6 is disabled."; else log "NetworkManager profile was created; AP-side IPv6 is disabled."; fi
else
  log "Existing profile already matches the requested AP settings; no profile change was made."
fi

install_ap_isolation_firewall
install_ap_dns_isolation

if is_yes "${ACTIVATE}"; then
  show_wan_reference
  confirm "Activate ${CONNECTION_NAME} now? This can disconnect SSH" || { log "Profile is saved but was not activated."; ACTIVATE=no; }
  if is_yes "${ACTIVATE}"; then
    if is_active; then
      log "Restarting the active AP so its DHCP/DNS isolation configuration is reloaded."
      run_privileged nmcli connection down "${CONNECTION_NAME}"
    fi
    run_privileged nmcli connection up "${CONNECTION_NAME}"
    log "AP activation requested."
  fi
fi

ACTIVE=no
is_active && ACTIVE=yes || true
log "Result (no secrets):"
printf '  Connection profile: %s\n  SSID: %s\n  Interface: %s\n  IPv4 address: %s\n  Autoconnect: yes\n  Active now: %s\n' "${CONNECTION_NAME}" "${SSID}" "${INTERFACE}" "${IPV4_ADDRESS}" "${ACTIVE}"
printf '  Enable AP: sudo nmcli connection up %q\n' "${CONNECTION_NAME}"
printf '  Status:    nmcli connection show --active; nmcli device status\n'
printf '  Disable:   sudo nmcli connection down %q\n' "${CONNECTION_NAME}"
printf '  Delete:    sudo nmcli connection delete %q  # displayed only; not run automatically\n' "${CONNECTION_NAME}"
