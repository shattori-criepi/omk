#!/usr/bin/env bash

# Root-owned, no-argument reset helper for the single approved RS-WSUHA-P.
set -euo pipefail

BY_ID='/dev/serial/by-id/usb-FTDI_FT230X_Basic_UART_DM006AOS-if00-port0'
EXPECTED_VENDOR='0403'
EXPECTED_PRODUCT='6015'
EXPECTED_SERIAL='DM006AOS'

[[ $# -eq 0 ]] || { echo 'This helper accepts no arguments.' >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo 'Root privileges are required.' >&2; exit 1; }
[[ -e "$BY_ID" ]] || { echo "RS-WSUHA-P by-id path is absent: $BY_ID" >&2; exit 1; }

tty_name="$(basename "$(readlink -f "$BY_ID")")"
sysfs_path="$(readlink -f "/sys/class/tty/${tty_name}/device")"
usb_path=''
while [[ "$sysfs_path" != / && -n "$sysfs_path" ]]; do
  if [[ -f "$sysfs_path/idVendor" && -f "$sysfs_path/idProduct" && -f "$sysfs_path/serial" ]]; then
    vendor="$(tr '[:upper:]' '[:lower:]' < "$sysfs_path/idVendor" | tr -d '\n')"
    product="$(tr '[:upper:]' '[:lower:]' < "$sysfs_path/idProduct" | tr -d '\n')"
    serial="$(tr -d '\n' < "$sysfs_path/serial")"
    if [[ "$vendor" != "$EXPECTED_VENDOR" || "$product" != "$EXPECTED_PRODUCT" || "$serial" != "$EXPECTED_SERIAL" ]]; then
      echo 'USB identity mismatch; refusing reset.' >&2
      exit 1
    fi
    usb_path="$sysfs_path"
    break
  fi
  sysfs_path="$(dirname "$sysfs_path")"
done
[[ -n "$usb_path" ]] || { echo 'USB parent was not found.' >&2; exit 1; }

device_name="$(basename "$usb_path")"
driver='/sys/bus/usb/drivers/usb'
[[ -w "$driver/unbind" && -w "$driver/bind" ]] || { echo 'USB driver bind files are unavailable.' >&2; exit 1; }
printf '%s' "$device_name" > "$driver/unbind"
sleep 2
printf '%s' "$device_name" > "$driver/bind"

for _ in $(seq 1 15); do
  [[ -e "$BY_ID" ]] && { echo "RS-WSUHA-P USB reset succeeded sysfs=$usb_path"; exit 0; }
  sleep 1
done
echo 'RS-WSUHA-P by-id path did not reappear after USB reset.' >&2
exit 1
