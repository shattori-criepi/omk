#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=/dev/null
source "$ROOT/scripts/cycle-gateway-usb-vbus.sh"
UHUBCTL=/bin/true
calls=()
run_uhubctl() {
  calls+=("$*")
  if [[ "$*" == '-l 1-1' ]]; then
    echo 'Current status for hub 1-1 [2109:3431 USB2.0 Hub, USB 2.10, 4 ports, ppps]'
  fi
}

expect_failure() {
  if "$@" >/dev/null 2>&1; then
    echo "Expected failure: $*" >&2
    exit 1
  fi
}

is_root() { return 0; }
read_model() { echo 'Raspberry Pi 4 Model B'; }
main
trap - EXIT INT TERM
[[ "${calls[*]}" == *'-l 1-1 -a cycle -d 5'* ]]

# No argument, root, platform and every topology predicate fail closed.
expect_failure main unexpected
is_root() { return 1; }
expect_failure main
is_root() { return 0; }
read_model() { echo 'Raspberry Pi 5'; }
expect_failure main
read_model() { echo 'Raspberry Pi 4 Model B'; }
run_uhubctl() { [[ "$*" == '-l 1-1' ]] && return 1; }
expect_failure main
run_uhubctl() { [[ "$*" == '-l 1-1' ]] && echo 'Current status for hub 1-1 [2109:9999 USB2.0 Hub, USB 2.10, 4 ports, ppps]'; }
expect_failure main
run_uhubctl() { [[ "$*" == '-l 1-1' ]] && echo 'Current status for hub 1-1 [2109:3431 USB3.0 Hub, USB 3.00, 4 ports, ppps]'; }
expect_failure main
run_uhubctl() { [[ "$*" == '-l 1-1' ]] && echo 'Current status for hub 1-1 [2109:3431 USB2.0 Hub, USB 2.10, 3 ports, ppps]'; }
expect_failure main

# A cycle command failure returns non-zero and its EXIT handler attempts one on.
calls=()
run_uhubctl() {
  calls+=("$*")
  [[ "$*" == '-l 1-1' ]] && { echo 'Current status for hub 1-1 [2109:3431 USB2.0 Hub, USB 2.10, 4 ports, ppps]'; return 0; }
  [[ "$*" == '-l 1-1 -a cycle -d 5' ]] && return 1
  return 0
}
expect_failure main
trap - EXIT INT TERM
[[ "${calls[*]}" == *'-l 1-1 -a cycle -d 5'* ]]

# Signal/exit fail-safe is a best effort only: a failed power-on must not loop.
calls=()
run_uhubctl() { calls+=("$*"); return 1; }
power_on
[[ ${#calls[@]} -eq 1 ]]
calls=()
run_uhubctl() { calls+=("$*"); return 0; }
trap power_on INT TERM
kill -INT $$
kill -TERM $$
trap - INT TERM
[[ "${calls[*]}" == *'-l 1-1 -a on'* ]]

# Check EXIT in an isolated sourced shell so the test itself does not exit early.
trace_file="$(mktemp)"
trap 'rm -f -- "$trace_file"' EXIT
bash -c '
  source "$1"
  trace="$2"
  UHUBCTL=/bin/true
  is_root() { return 0; }
  read_model() { echo "Raspberry Pi 4 Model B"; }
  run_uhubctl() {
    printf "%s\\n" "$*" >> "$trace"
    if [[ "$*" == "-l 1-1" ]]; then
      echo "Current status for hub 1-1 [2109:3431 USB2.0 Hub, USB 2.10, 4 ports, ppps]"
    fi
  }
  main
' bash "$ROOT/scripts/cycle-gateway-usb-vbus.sh" "$trace_file"
grep -Fxq -- '-l 1-1 -a cycle -d 5' "$trace_file"
grep -Fxq -- '-l 1-1 -a on' "$trace_file"

# A non-zero cycle exits the helper and still runs the EXIT fail-safe once.
set +e
bash -c '
  source "$1"
  trace="$2"
  UHUBCTL=/bin/true
  is_root() { return 0; }
  read_model() { echo "Raspberry Pi 4 Model B"; }
  run_uhubctl() {
    printf "%s\\n" "$*" >> "$trace"
    if [[ "$*" == "-l 1-1" ]]; then
      echo "Current status for hub 1-1 [2109:3431 USB2.0 Hub, USB 2.10, 4 ports, ppps]"
    else
      [[ "$*" == "-l 1-1 -a cycle -d 5" ]] && return 1
    fi
  }
  main
' bash "$ROOT/scripts/cycle-gateway-usb-vbus.sh" "$trace_file" >/dev/null 2>&1
failure_status=$?
set -e
[[ $failure_status -ne 0 ]]
grep -Fxq -- '-l 1-1 -a cycle -d 5' "$trace_file"
grep -Fxq -- '-l 1-1 -a on' "$trace_file"
echo 'PASS: VBUS helper mock safety checks.'
