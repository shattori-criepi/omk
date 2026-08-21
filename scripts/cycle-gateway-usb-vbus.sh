#!/usr/bin/env bash
# Root-owned, no-argument Pi 4 USB VBUS recovery helper.
set -euo pipefail

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
  local topology
  topology="$(run_uhubctl -l "$HUB" 2>&1)" || { echo 'Expected Pi 4 USB hub topology is unavailable.' >&2; return 1; }
  grep -Eq "hub ${HUB} \[2109:3431 USB2\.0 Hub, USB 2\.[0-9]+, 4 ports," <<<"$topology" || { echo 'Expected Pi 4 USB2 hub topology is ambiguous.' >&2; return 1; }
  trap power_on EXIT INT TERM
  run_uhubctl -l "$HUB" -a cycle -d 5
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
