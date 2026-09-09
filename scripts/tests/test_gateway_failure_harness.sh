#!/usr/bin/env bash
set -euo pipefail
GATEWAY_HARNESS_SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
source "${GATEWAY_HARNESS_SOURCE_ROOT}/scripts/tests/lib/gateway_harness.sh"
gateway_harness_create
trap gateway_harness_destroy EXIT

# A: Compose config error aborts before AP activation.
if GW_FAIL_COMMAND=docker GW_FAIL_SUBCOMMAND='config --quiet' gateway_harness_run >"${GATEWAY_HARNESS_ROOT}/a.log" 2>&1; then exit 1; fi
if grep -Fq 'setup-wifi-access-point.sh' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi

# B: two apt lock errors are observed, then the same standard command succeeds.
: >"${GW_CALL_LOG}"; rm -f "${GW_STATE_DIR}"/count-apt-get
if ! GW_FAIL_COMMAND=apt-get GW_FAIL_MODE=lock GW_FAIL_COUNT=2 OMK_APT_LOCK_RETRY_SECONDS=1 gateway_harness_run --with-base >"${GATEWAY_HARNESS_ROOT}/b.log" 2>&1; then cat "${GATEWAY_HARNESS_ROOT}/b.log" >&2; exit 1; fi
[[ "$(<"${GW_STATE_DIR}/count-apt-get")" -ge 2 ]]

# C: pre-AP interruption preserves persistent identity state across rerun.
: >"${GW_CALL_LOG}"; rm -f "${GW_STATE_DIR}/checkpoint-pre-ap"
if GW_CHECKPOINT=pre-ap gateway_harness_run >"${GATEWAY_HARNESS_ROOT}/c.log" 2>&1; then exit 1; fi
before="$(cat "${GW_STATE_DIR}/site_uuid" "${GW_STATE_DIR}/token")"
gateway_harness_run >/dev/null
after="$(cat "${GW_STATE_DIR}/site_uuid" "${GW_STATE_DIR}/token")"
[[ "$before" == "$after" ]] && [[ -f "${GW_STATE_DIR}/ap_psk" ]]
echo 'PASS: Gateway failure/checkpoint harness supports config failure, lock calls, and persistent rerun state.'
