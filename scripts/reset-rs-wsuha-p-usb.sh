#!/usr/bin/env bash

# Root-owned no-argument helper: only an administrator-established adapter.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH LC_ALL=C
SYS_ROOT=/sys
DEV_ROOT=/dev
IDENTITY_FILE=/etc/omk/broute-usb-recovery.conf
STATE_DIR=/run/omk-broute-usb
HUB=1-1

is_root() { [[ $EUID -eq 0 ]]; }
pause() { sleep "$1"; }
error() { echo "$*" >&2; return 1; }

read_trusted_serial() {
  local directory="${IDENTITY_FILE%/*}" parent lines size
  parent="${directory%/*}"
  for directory in "$parent" "${IDENTITY_FILE%/*}"; do
    [[ ! -L "$directory" && -d "$directory" ]] || return 1
    [[ "$(stat -c '%u:%g:%a' "$directory")" == 0:0:755 ]] || return 1
  done
  [[ ! -L "$IDENTITY_FILE" && -f "$IDENTITY_FILE" ]] || return 1
  [[ "$(stat -c '%u:%g:%a:%h' "$IDENTITY_FILE")" == 0:0:644:1 ]] || return 1
  size="$(stat -c '%s' "$IDENTITY_FILE")" || return 1
  [[ "$size" -ge 2 && "$size" -le 65 ]] || return 1
  mapfile -t lines < "$IDENTITY_FILE"
  [[ ${#lines[@]} -eq 1 && "${lines[0]}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$ ]] || return 1
  [[ "$size" -eq $((${#lines[0]} + 1)) ]] || return 1
  printf '%s\n' "${lines[0]}"
}

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
  local device="$1" serial="$2" name vendor product actual_serial bus_path
  [[ "$device" == "$SYS_ROOT/devices/"* ]] || return 1
  name="${device##*/}"
  [[ "$name" =~ ^[0-9]+-[0-9]+(\.[0-9]+)*$ ]] || return 1
  bus_path="$(readlink -e "$SYS_ROOT/bus/usb/devices/$name")" || return 1
  [[ "$bus_path" == "$device" ]] || return 1
  vendor="$(cat "$device/idVendor")" || return 1
  product="$(cat "$device/idProduct")" || return 1
  actual_serial="$(cat "$device/serial")" || return 1
  # Observed FTDI transport guard, NOT an official model VID/PID mapping.
  [[ "${vendor,,}:${product,,}" == '0403:6015' && "$actual_serial" == "$serial" ]]
}

# Count USB parents without depending on whether their tty driver is bound.
matching_parent() {
  local serial="$1" entry candidate target='' count=0
  for entry in "$SYS_ROOT"/bus/usb/devices/*; do
    [[ "${entry##*/}" =~ ^[0-9]+-[0-9]+(\.[0-9]+)*$ ]] || continue
    [[ -f "$entry/serial" ]] || continue
    [[ "$(cat "$entry/serial")" == "$serial" ]] || continue
    candidate="$(readlink -e "$entry")" || return 1
    verify_usb_identity "$candidate" "$serial" || return 1
    target="$candidate"; count=$((count + 1))
  done
  [[ $count -le 1 ]] || return 1
  [[ $count -eq 1 ]] || return 3
  printf '%s\n' "$target"
}

resolve_target() {
  local serial="$1" entry tty candidate target count=0
  target="$(matching_parent "$serial")" || return 1
  for entry in "$SYS_ROOT"/class/tty/ttyUSB*; do
    [[ -e "$entry" ]] || continue
    tty="${entry##*/}"
    [[ "$tty" =~ ^ttyUSB[0-9]+$ ]] || continue
    candidate="$(readlink -e "$entry/device")" || return 1
    [[ "$candidate" == "$SYS_ROOT/devices/"* ]] || return 1
    while [[ "$candidate" == "$SYS_ROOT/devices/"* ]]; do
      if [[ -f "$candidate/idVendor" && -f "$candidate/idProduct" ]]; then
        if [[ "$candidate" == "$target" ]]; then
          verify_tty_node "$tty" || return 1
          count=$((count + 1))
        fi
        break
      fi
      candidate="${candidate%/*}"
    done
  done
  [[ $count -eq 1 ]] || return 1
  printf '%s\n' "$target"
}

uptime_seconds() { local value rest; read -r value rest </proc/uptime; printf '%s\n' "${value%%.*}"; }

lock_recovery_state() {
  local parent="${STATE_DIR%/*}"
  [[ ! -L "$parent" && -d "$parent" && "$(stat -c '%u:%g:%a' "$parent")" == 0:0:755 ]] || return 1
  if [[ ! -e "$STATE_DIR" && ! -L "$STATE_DIR" ]]; then
    mkdir -m 0700 "$STATE_DIR" || return 1
  fi
  [[ ! -L "$STATE_DIR" && -d "$STATE_DIR" && "$(stat -c '%u:%g:%a' "$STATE_DIR")" == 0:0:700 ]] || return 1
  # Lock the protected directory inode, avoiding a caller-controlled lock file.
  exec 9<"$STATE_DIR"
  flock -n 9
}

safe_state_file() {
  [[ ! -L "$1" && -f "$1" && "$(stat -c '%u:%g:%a:%h' "$1")" == 0:0:600:1 ]]
}

gateway_hub() {
  local hub
  hub="$(readlink -e "$SYS_ROOT/bus/usb/devices/$HUB")" || return 1
  [[ "$hub" == "$SYS_ROOT/devices/"* && "${hub##*/}" == "$HUB" ]] || return 1
  [[ "$(cat "$hub/idVendor")":"$(cat "$hub/idProduct")" == 2109:3431 ]] || return 1
  printf '%s\n' "$hub"
}

on_gateway_hub() {
  local hub
  hub="$(gateway_hub)" || return 1
  [[ "$1" == "$hub/"* ]]
}

arm_vbus_recovery() {
  local serial="$1" target="$2"
  rm -f -- "$STATE_DIR/pending"
  # Logical reset also works on other hosts; only the Pi 4 hub earns a ticket.
  on_gateway_hub "$target" || return 0
  (umask 077; printf '%s\n%s\n%s\n' "$serial" "${target##*/}" "$(uptime_seconds)" > "$STATE_DIR/pending")
}

# Called under the same lock by the VBUS boundary. No application-owned state.
authorize_vbus_recovery() {
  local serial="$1" now parent rc age last
  local -a ticket=() cooldown=()
  gateway_hub >/dev/null || return 1
  safe_state_file "$STATE_DIR/pending" || return 1
  [[ "$(stat -c '%s' "$STATE_DIR/pending")" -le 160 ]] || return 1
  mapfile -t ticket < "$STATE_DIR/pending"
  [[ ${#ticket[@]} -eq 3 && "${ticket[0]}" == "$serial" && "${ticket[1]}" =~ ^1-1\.[0-9]+(\.[0-9]+)*$ && "${ticket[2]}" =~ ^[0-9]{1,12}$ ]] || return 1
  now="$(uptime_seconds)"
  age=$((now - 10#${ticket[2]}))
  [[ $age -ge 0 && $age -le 600 ]] || return 1
  if [[ -e "$STATE_DIR/last-cycle" || -L "$STATE_DIR/last-cycle" ]]; then
    safe_state_file "$STATE_DIR/last-cycle" || return 1
    [[ "$(stat -c '%s' "$STATE_DIR/last-cycle")" -le 13 ]] || return 1
    mapfile -t cooldown < "$STATE_DIR/last-cycle"
    [[ ${#cooldown[@]} -eq 1 && "${cooldown[0]}" =~ ^[0-9]{1,12}$ ]] || return 1
    last=$((10#${cooldown[0]}))
    [[ $now -ge $((last + 3600)) ]] || return 1
  fi
  if parent="$(matching_parent "$serial")"; then
    [[ "${parent##*/}" == "${ticket[1]}" ]] || return 1
    on_gateway_hub "$parent" || return 1
    # Existing tty mappings must be sound. An unbound USB parent alone is a
    # valid immediate post-reset state, but malformed/ambiguous ttys are not.
    if compgen -G "$SYS_ROOT/class/tty/ttyUSB*" >/dev/null; then
      local entry resolved count=0
      for entry in "$SYS_ROOT"/class/tty/ttyUSB*; do
        resolved="$(readlink -e "$entry/device")" || return 1
        if [[ "$resolved" == "$parent/"* ]]; then
          verify_tty_node "${entry##*/}" || return 1
          count=$((count + 1))
        fi
      done
      [[ $count -le 1 ]] || return 1
    fi
  else
    rc=$?
    [[ $rc -eq 3 ]] || return 1
    # Complete disappearance is allowed only with the fresh root-issued ticket.
    # A replacement at the old physical slot is a contradiction, not recovery.
    [[ ! -e "$SYS_ROOT/bus/usb/devices/${ticket[1]}" && ! -L "$SYS_ROOT/bus/usb/devices/${ticket[1]}" ]] || return 1
  fi
  # Consume BEFORE any power command, including failed attempts. Runtime
  # cooldown survives service restarts, but expires with this boot's /run.
  rm -- "$STATE_DIR/pending" || return 1
  (umask 077; printf '%s\n' "$now" > "$STATE_DIR/last-cycle")
}

write_driver() { printf '%s' "$2" > "$SYS_ROOT/bus/usb/drivers/usb/$1"; }

reset_main() {
  [[ $# -eq 0 ]] || {
    echo 'This helper accepts no arguments.' >&2
    return 2
  }
  is_root || { error 'Root privileges are required.'; return 1; }
  local serial target checked name driver="$SYS_ROOT/bus/usb/drivers/usb"
  serial="$(read_trusted_serial)" || { error 'Trusted USB identity is missing or unsafe; reset disabled.'; return 1; }
  target="$(resolve_target "$serial")" || { error 'USB recovery identity is absent, ambiguous or unverified.'; return 1; }
  [[ -w "$driver/unbind" && -w "$driver/bind" ]] || { error 'USB driver files are unavailable.'; return 1; }
  checked="$(resolve_target "$serial")" || return 1
  [[ "$checked" == "$target" ]] || { error 'USB identity changed before reset.'; return 1; }
  name="${target##*/}"
  lock_recovery_state || { error 'Unsafe or busy root recovery state.'; return 1; }
  checked="$(resolve_target "$serial")" || return 1
  [[ "$checked" == "$target" ]] || return 1
  arm_vbus_recovery "$serial" "$target" || return 1
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

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then reset_main "$@"; fi
