#!/usr/bin/env bash

# Root-owned helper: accept a USB serial, never a device or sysfs path.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH LC_ALL=C
SYS_ROOT=/sys
DEV_ROOT=/dev

is_root() { [[ $EUID -eq 0 ]]; }
pause() { sleep "$1"; }
error() { echo "$*" >&2; return 1; }

verify_tty_node() {
  local tty="$1" numbers major minor actual
  [[ -c "$DEV_ROOT/$tty" ]] || return 1
  numbers="$(cat "$SYS_ROOT/class/tty/$tty/dev")" || return 1
  [[ "$numbers" =~ ^([0-9]+):([0-9]+)$ ]] || return 1
  major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"
  actual="$(stat -Lc '%t:%T' "$DEV_ROOT/$tty")" || return 1
  [[ "$actual" == "$(printf '%x:%x' "$major" "$minor")" ]]
}

verify_usb_identity() {
  local device="$1" serial="$2" name vendor product actual_serial label bus_path
  [[ "$device" == "$SYS_ROOT/devices/"* ]] || return 1
  name="${device##*/}"
  [[ "$name" =~ ^[0-9]+-[0-9]+(\.[0-9]+)*$ ]] || return 1
  bus_path="$(readlink -e "$SYS_ROOT/bus/usb/devices/$name")" || return 1
  [[ "$bus_path" == "$device" ]] || return 1
  vendor="$(cat "$device/idVendor")" || return 1
  product="$(cat "$device/idProduct")" || return 1
  actual_serial="$(cat "$device/serial")" || return 1
  label="$(cat "$device/product")" || return 1
  # Observed FTDI transport guard, NOT an official model VID/PID mapping.
  [[ "${vendor,,}:${product,,}" == '0403:6015' && "$actual_serial" == "$serial" && "${label,,}" == *rs-wsuha-p* ]]
}

resolve_target() {
  local serial="$1" entry tty candidate actual_serial target='' count=0
  local -A seen=()
  for entry in "$SYS_ROOT"/class/tty/ttyUSB*; do
    [[ -e "$entry" ]] || continue
    tty="${entry##*/}"
    [[ "$tty" =~ ^ttyUSB[0-9]+$ ]] || continue
    candidate="$(readlink -e "$entry/device")" || return 1
    [[ "$candidate" == "$SYS_ROOT/devices/"* ]] || return 1
    while [[ "$candidate" == "$SYS_ROOT/devices/"* ]]; do
      if [[ -f "$candidate/idVendor" && -f "$candidate/idProduct" ]]; then
        actual_serial="$(cat "$candidate/serial" 2>/dev/null)" || break
        if [[ "$actual_serial" == "$serial" ]]; then
          verify_usb_identity "$candidate" "$serial" || return 1
          verify_tty_node "$tty" || return 1
          if [[ -z "${seen[$candidate]:-}" ]]; then
            seen[$candidate]=1; target="$candidate"; count=$((count + 1))
          fi
        fi
        break
      fi
      candidate="${candidate%/*}"
    done
  done
  [[ $count -eq 1 ]] || return 1
  printf '%s\n' "$target"
}

write_driver() { printf '%s' "$2" > "$SYS_ROOT/bus/usb/drivers/usb/$1"; }

main() {
  [[ $# -eq 1 && "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$ ]] || {
    echo 'Exactly one USB serial (1-64 letters, digits, underscore or hyphen) is required.' >&2
    return 2
  }
  is_root || { error 'Root privileges are required.'; return 1; }
  local serial="$1" target checked name driver="$SYS_ROOT/bus/usb/drivers/usb"
  target="$(resolve_target "$serial")" || { error 'USB recovery identity is absent, ambiguous or unverified.'; return 1; }
  [[ -w "$driver/unbind" && -w "$driver/bind" ]] || { error 'USB driver files are unavailable.'; return 1; }
  checked="$(resolve_target "$serial")" || return 1
  [[ "$checked" == "$target" ]] || { error 'USB identity changed before reset.'; return 1; }
  name="${target##*/}"
  write_driver unbind "$name" || return 1
  pause 2
  # Unbinding removes tty nodes. Recheck the USB parent independently before bind.
  verify_usb_identity "$target" "$serial" || { error 'USB identity changed before bind.'; return 1; }
  write_driver bind "$name" || return 1
  local attempt
  for ((attempt=0; attempt<15; attempt++)); do
    if checked="$(resolve_target "$serial")" && [[ "$checked" == "$target" ]]; then
      echo 'RS-WSUHA-P USB reset succeeded; matching adapter reappeared.'
      return 0
    fi
    pause 1
  done
  error 'Matching RS-WSUHA-P did not reappear after USB reset.'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
