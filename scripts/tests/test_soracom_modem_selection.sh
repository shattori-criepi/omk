#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SCRIPT="${ROOT_DIR}/scripts/setup-soracom-onyx.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT

source <(sed -n '/^quectel_usb_syspaths()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^modem_paths_from_list()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^mmcli_kv_value()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^path_is_within()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^modem_matches_onyx_usb()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^select_unique_onyx_modem()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^single_soracom_nm_device()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^modem_owns_nm_device()/,/^}$/p' "${SCRIPT}")

mkdir -p "${TEMP_DIR}/bus" "${TEMP_DIR}/devices/usb1" "${TEMP_DIR}/bin"
make_usb() {
  local name="$1"
  mkdir -p "${TEMP_DIR}/devices/usb1/${name}/${name}:1.4/usbmisc"
  printf '2c7c\n' >"${TEMP_DIR}/devices/usb1/${name}/idVendor"
  printf '0125\n' >"${TEMP_DIR}/devices/usb1/${name}/idProduct"
  : >"${TEMP_DIR}/devices/usb1/${name}/${name}:1.4/usbmisc/cdc-wdm-test"
  ln -s "${TEMP_DIR}/devices/usb1/${name}" "${TEMP_DIR}/bus/${name}"
}
make_usb 1-1

cat >"${TEMP_DIR}/bin/mmcli" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == -m && ( "${3:-}" == --output-keyvalue || "${3:-}" == -K ) ]]; then
  case "${2}" in
    /org/freedesktop/ModemManager1/Modem/7|/org/freedesktop/ModemManager1/Modem/42)
      printf 'modem.generic.device: %s\nmodem.generic.ports: cdc-wdm7 (qmi), ttyUSB9 (at)\n' "${TEST_MODEM_DEVICE}"
      ;;
    *) printf 'modem.generic.device: %s\nmodem.generic.ports: cdc-wdm8 (qmi)\n' "${TEST_OTHER_DEVICE}" ;;
  esac
  exit 0
fi
printf 'unexpected mmcli invocation: %s\n' "$*" >&2
exit 99
EOF
cat >"${TEMP_DIR}/bin/nmcli" <<'EOF'
#!/usr/bin/env bash
[[ "$*" == '-g GENERAL.DEVICES connection show soracom' ]] || exit 99
printf '%s\n' "${TEST_NM_DEVICES}"
EOF
chmod +x "${TEMP_DIR}/bin/"*

USB_SYSFS_ROOT="${TEMP_DIR}/bus"
USB_SYSFS_ALLOWED_ROOT="${TEMP_DIR}/devices"
export TEST_MODEM_DEVICE="${TEMP_DIR}/devices/usb1/1-1/1-1:1.4/usbmisc/cdc-wdm-test"
export TEST_OTHER_DEVICE="${TEMP_DIR}/devices/usb1/not-onyx/device"
export PATH="${TEMP_DIR}/bin:${PATH}"

# Object numbers and list order do not matter: the sysfs ancestry decides.
MODEM_PATH=''
select_unique_onyx_modem $' /org/freedesktop/ModemManager1/Modem/7 [Quectel]\n'
[[ "${MODEM_PATH}" == /org/freedesktop/ModemManager1/Modem/7 ]]

# Re-enumeration may allocate a new ModemManager object number. The mapping is
# recomputed from sysfs rather than reusing the former object or a tty number.
MODEM_PATH=''
select_unique_onyx_modem $' /org/freedesktop/ModemManager1/Modem/42 [Quectel]\n'
[[ "${MODEM_PATH}" == /org/freedesktop/ModemManager1/Modem/42 ]]

# A nonmatching object and an additional object both refuse automatic setup.
MODEM_PATH=''
! select_unique_onyx_modem $' /org/freedesktop/ModemManager1/Modem/8 [other]\n'
[[ "$?" == 0 ]] # Negation is only used to keep set -e; inspect status below.
if select_unique_onyx_modem $' /org/freedesktop/ModemManager1/Modem/8 [other]\n'; then exit 1; else [[ "$?" == 2 ]]; fi
if select_unique_onyx_modem $' /org/freedesktop/ModemManager1/Modem/7 [Quectel]\n /org/freedesktop/ModemManager1/Modem/8 [other]\n'; then exit 1; else [[ "$?" == 3 ]]; fi

# A second supported USB device is ambiguous even when its MM object has not
# appeared yet; VID/PID is a candidate class, not an individual identity.
make_usb 1-2
if select_unique_onyx_modem $' /org/freedesktop/ModemManager1/Modem/7 [Quectel]\n'; then exit 1; else [[ "$?" == 3 ]]; fi
rm "${TEMP_DIR}/bus/1-2"

export TEST_NM_DEVICES='cdc-wdm7'
[[ "$(single_soracom_nm_device)" == cdc-wdm7 ]]
export TEST_NM_DEVICES=$'cdc-wdm7\ncdc-wdm8'
! single_soracom_nm_device
modem_owns_nm_device $'modem.generic.ports: cdc-wdm7 (qmi), ttyUSB9 (at)' cdc-wdm7
! modem_owns_nm_device $'modem.generic.ports: cdc-wdm8 (qmi)' cdc-wdm7

echo 'PASS: Onyx selection requires one USB device, one mapped ModemManager modem, and one NetworkManager device.'
