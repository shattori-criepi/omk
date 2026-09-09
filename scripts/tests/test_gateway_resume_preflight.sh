#!/usr/bin/env bash
set -euo pipefail

GATEWAY_HARNESS_SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
source "${GATEWAY_HARNESS_SOURCE_ROOT}/scripts/tests/lib/gateway_harness.sh"
gateway_harness_create
trap gateway_harness_destroy EXIT

assert_no_setup_step() {
  if grep -Eq '^setup-.*\.sh ' "${GW_CALL_LOG}"; then
    echo 'Resume preflight allowed a setup mutation.' >&2
    exit 1
  fi
}

touch "${GATEWAY_HARNESS_ROOT}/reboot-required"
if OMK_REBOOT_REQUIRED_PATH="${GATEWAY_HARNESS_ROOT}/reboot-required" gateway_harness_run >"${GATEWAY_HARNESS_ROOT}/reboot.log" 2>&1; then
  echo 'Resume continued while reboot was required.' >&2
  exit 1
fi
assert_no_setup_step

: >"${GW_CALL_LOG}"
rm -f -- "${GATEWAY_HARNESS_ROOT}/reboot-required"
if GW_FAIL_COMMAND=docker GW_FAIL_SUBCOMMAND='info' gateway_harness_run >"${GATEWAY_HARNESS_ROOT}/docker.log" 2>&1; then
  echo 'Resume continued without direct Docker daemon access.' >&2
  exit 1
fi
assert_no_setup_step

: >"${GW_CALL_LOG}"
if GW_AP_AUTOCONNECT=yes gateway_harness_run >"${GATEWAY_HARNESS_ROOT}/autoconnect.log" 2>&1; then
  echo 'Gateway accepted autoconnect=yes on an inactive prepared AP.' >&2
  exit 1
fi
grep -Fq 'connection.autoconnect is not no' "${GATEWAY_HARNESS_ROOT}/autoconnect.log"
if grep -Fq 'setup-wifi-access-point.sh --activate' "${GW_CALL_LOG}"; then
  echo 'Gateway activated an AP that failed prepared-profile validation.' >&2
  exit 1
fi

echo 'PASS: resume prerequisites fail before the first setup mutation.'
