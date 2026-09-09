#!/usr/bin/env bash
# Root-owned, no-argument B-route escalation; Pi 4 power is hub-wide.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH LC_ALL=C
load_usb_guard() { source /usr/local/lib/omk/reset-rs-wsuha-p-usb; }

UHUBCTL=/usr/sbin/uhubctl
HUB=1-1
run_uhubctl() { "$UHUBCTL" "$@"; }
read_model() { tr -d '\0' </proc/device-tree/model 2>/dev/null || true; }
is_root() { [[ $EUID -eq 0 ]]; }
power_on() { run_uhubctl -l "$HUB" -a on >/dev/null 2>&1 || true; }
main() {
  [[ $# -eq 0 ]] || { echo 'This helper accepts no arguments.' >&2; return 2; }
  is_root || { echo 'Root privileges are required.' >&2; return 1; }
  [[ -x $UHUBCTL ]] || { echo 'uhubctl is unavailable.' >&2; return 1; }
  [[ $(read_model) == Raspberry\ Pi\ 4* ]] || { echo 'Unsupported platform; Pi 4 is required.' >&2; return 1; }
  # The installed logical helper is root-owned; never source caller paths.
  load_usb_guard
  local serial topology
  serial="$(read_trusted_serial)" || { echo 'Trusted USB identity is missing or unsafe.' >&2; return 1; }
  lock_recovery_state || { echo 'Unsafe or busy root recovery state.' >&2; return 1; }
  topology="$(run_uhubctl -l "$HUB" 2>&1)" || { echo 'Expected Pi 4 USB hub topology is unavailable.' >&2; return 1; }
  grep -Eq "hub ${HUB} \[2109:3431 USB2\.0 Hub, USB 2\.[0-9]+, 4 ports," <<<"$topology" || { echo 'Expected Pi 4 USB2 hub topology is ambiguous.' >&2; return 1; }
  authorize_vbus_recovery "$serial" || { echo 'No valid B-route recovery authorization.' >&2; return 1; }
  trap power_on EXIT INT TERM
  run_uhubctl -l "$HUB" -a cycle -d 5
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
