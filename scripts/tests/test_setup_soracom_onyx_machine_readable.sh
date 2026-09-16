#!/usr/bin/env bash
set -euo pipefail

SCRIPT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-soracom-onyx.sh"

# Load production parsing functions without entering the host-mutating main flow.
source <(sed -n '/^mmcli_kv_value()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^modem_paths_from_list()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^modem_is_connected()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^redact_modem_identifiers()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^quectel_usb_syspaths()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^replay_quectel_udev_events()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^repair_dispatcher_routes_without_reconnect()/,/^}$/p' "${SCRIPT}")
source "$(dirname "${SCRIPT}")/lib/soracom-preservation-policy.sh"

valid_kv() {
  cat <<EOF
modem.generic.state: "connected"
modem.generic.sim: /org/freedesktop/ModemManager1/SIM/0
modem.3gpp.registration-state: $1
modem.3gpp.packet-service-state: $2
EOF
}

modem_is_connected "$(valid_kv home attached)"
modem_is_connected "$(valid_kv roaming attached)"
! modem_is_connected "$(valid_kv searching attached)"
! modem_is_connected "$(valid_kv denied attached)"
! modem_is_connected "$(valid_kv home detached)"
! modem_is_connected $'modem.generic.state: connected\nmodem.generic.sim: /\nmodem.3gpp.registration-state: home\nmodem.3gpp.packet-service-state: attached'

# Human-readable tables are diagnostics only; success checks use -K fields.
grep -Fq -- '--output-keyvalue' "${SCRIPT}"
grep -Fq 'ping -I "${CELLULAR_INTERFACE}" -c 4 pong.soracom.io' "${SCRIPT}"
if grep -Fq -- '--path' "${SCRIPT}"; then
  echo 'Invalid udevadm --path option remains.' >&2
  exit 1
fi
grep -Fq -- '--parent-match="${syspath}"' "${SCRIPT}"
grep -Fq 'nmcli -g NAME connection show --active' "${SCRIPT}"
grep -Fq 'GENERAL.DEVICES connection show soracom' "${SCRIPT}"
grep -Fq 'single_soracom_nm_device' "${SCRIPT}"
grep -Fq 'modem_owns_nm_device' "${SCRIPT}"
grep -Fq 'select_unique_onyx_modem' "${SCRIPT}"
grep -Fq 'ensure_current_soracom_dispatcher || fail' "${SCRIPT}"
grep -Fq 'repair_dispatcher_routes_without_reconnect yes yes' "${SCRIPT}"
grep -Fq 'already active; preserving the current cellular connection' "${SCRIPT}"
redacted="$(printf '%s\n' 'device id: 123456' 'Numbers | own: +819012345678' '|                     own: +819012345678' 'imei: 123' 'operator name: SORACOM' 'state UNKNOWN' 'wwan0' 'UNKNOWN' | redact_modem_identifiers)"
grep -Fq 'device id: [REDACTED]' <<<"${redacted}"
grep -Fq 'own: [REDACTED]' <<<"${redacted}"
grep -Fq 'imei: [REDACTED]' <<<"${redacted}"
grep -Fq 'operator name: SORACOM' <<<"${redacted}"
grep -Fxq 'state UNKNOWN' <<<"${redacted}"
grep -Fxq 'wwan0' <<<"${redacted}"
grep -Fxq 'UNKNOWN' <<<"${redacted}"

# Resolve the bus symlink to its real syspath, revalidate the USB ID there,
# and retrigger exactly that parent subtree with Trixie's supported option.
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT
mkdir -p "${TEMP_DIR}/bus" "${TEMP_DIR}/devices/usb1/1-1" "${TEMP_DIR}/bin"
printf '2c7c\n' >"${TEMP_DIR}/devices/usb1/1-1/idVendor"
printf '0125\n' >"${TEMP_DIR}/devices/usb1/1-1/idProduct"
ln -s "${TEMP_DIR}/devices/usb1/1-1" "${TEMP_DIR}/bus/1-1"
cat >"${TEMP_DIR}/bin/udevadm" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${UDEV_CALLS}"
EOF
chmod +x "${TEMP_DIR}/bin/udevadm"
USB_SYSFS_ROOT="${TEMP_DIR}/bus"
USB_SYSFS_ALLOWED_ROOT="${TEMP_DIR}/devices"
SUDO=()
UDEV_CALLS="${TEMP_DIR}/calls" PATH="${TEMP_DIR}/bin:${PATH}" replay_quectel_udev_events
grep -Fxq "trigger --type=devices --action=add --parent-match=${TEMP_DIR}/devices/usb1/1-1 --settle" "${TEMP_DIR}/calls"
if grep -Fq -- '--path' "${TEMP_DIR}/calls"; then
  echo 'Coldplug used an invalid udevadm option.' >&2
  exit 1
fi

# A dispatcher omission never makes an existing, active profile eligible for
# the official script (which cycles the cellular interface).
[[ "$(soracom_official_setup_required yes yes no)" == no ]]
[[ "$(soracom_official_setup_required yes no no)" == no ]]
[[ "$(soracom_official_setup_required no no no)" == yes ]]

cat >"${TEMP_DIR}/dispatcher" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${DISPATCHER_CALLS}"
EOF
cat >"${TEMP_DIR}/bin/nmcli" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  '-g GENERAL.DEVICES connection show soracom') printf 'cdc-wdm0\n' ;;
  '-g GENERAL.IP-IFACE device show cdc-wdm0') printf 'wwan0\n' ;;
  *) echo 'Forbidden cellular operation.' >&2; exit 99 ;;
esac
EOF
chmod +x "${TEMP_DIR}/dispatcher" "${TEMP_DIR}/bin/nmcli"
SORACOM_DISPATCHER_PATH="${TEMP_DIR}/dispatcher"
export DISPATCHER_CALLS="${TEMP_DIR}/dispatcher.calls"
SUDO=()
log() { :; }
PATH="${TEMP_DIR}/bin:${PATH}" repair_dispatcher_routes_without_reconnect yes yes
grep -Fxq 'wwan0 up' "${TEMP_DIR}/dispatcher.calls"

echo 'PASS: SORACOM Onyx state validation is machine-readable and interface-bound.'

# Execute the actual final NM state check with localized display labels.
state_check="$(sed -n '/^nmcli -g GENERAL.STATE device show /p' "${SCRIPT}")"
[[ -n "${state_check}" ]]
NM_DEVICE=fixture EXIT_GENERAL=1
fail() { return 1; }
nmcli() { printf '%s\n' "${TEST_NM_STATE}"; }
for TEST_NM_STATE in '100 (connected)' '100 (接続済み)' '100 (verbunden)' '100'; do
  eval "${state_check}"
done
for TEST_NM_STATE in '30 (disconnected)' '1000' ''; do
  if eval "${state_check}"; then echo 'Invalid NM state accepted.' >&2; exit 1; fi
done
echo 'PASS: localized NetworkManager state checks use the numeric state.'
