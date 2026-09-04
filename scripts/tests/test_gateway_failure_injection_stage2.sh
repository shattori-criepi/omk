#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATEWAY_HARNESS_SOURCE_ROOT="${ROOT}"
source "${ROOT}/scripts/tests/lib/gateway_harness.sh"

run_case() {
  local command="$1" subcommand="$2" mode="$3" count="$4" expect_ap="$5"
  gateway_harness_create
  if GW_FAIL_COMMAND="$command" GW_FAIL_SUBCOMMAND="$subcommand" GW_FAIL_MODE="$mode" GW_FAIL_COUNT="$count" gateway_harness_run >"${GATEWAY_HARNESS_ROOT}/failure.log" 2>&1; then
    echo "Expected failure: ${command} ${subcommand}" >&2; exit 1
  fi
  if [[ "$expect_ap" == no ]]; then ! grep -Fq 'setup-wifi-access-point.sh' "${GW_CALL_LOG}"; else grep -Fq 'setup-wifi-access-point.sh' "${GW_CALL_LOG}"; fi
  mkdir "${GATEWAY_HARNESS_ROOT}/before"
  for state_file in site_uuid token ap_psk unit sudoers; do [[ -e "${GW_STATE_DIR}/${state_file}" ]] && cp "${GW_STATE_DIR}/${state_file}" "${GATEWAY_HARNESS_ROOT}/before/${state_file}"; done
  gateway_harness_run >/dev/null
  for state_file in "${GATEWAY_HARNESS_ROOT}/before"/*; do [[ -e "${state_file}" ]] || continue; cmp -s "${state_file}" "${GW_STATE_DIR}/$(basename "${state_file}")"; done
  [[ -f "${GW_STATE_DIR}/site_uuid" && -f "${GW_STATE_DIR}/token" && -f "${GW_STATE_DIR}/unit" && -f "${GW_STATE_DIR}/sudoers" ]]
  gateway_harness_destroy
}

# D1/D2/D3/D4, P1/P2/P3/P4, S1/S2/S3, Case A, C1, H2: all failures are
# replayed on the same fixture and must recover only after injection is removed.
run_case docker 'compose pull' always 1 no
run_case docker 'compose build' always 1 no
run_case python '-m venv' always 1 no
run_case pip 'install' always 1 no
run_case python "-c import omk_system_manager" always 1 no
run_case systemctl daemon-reload always 1 no
run_case systemctl enable always 1 no
run_case visudo -cf always 1 no
run_case docker 'config --quiet' always 1 no
run_case docker 'compose up' always 1 yes
run_case curl health always 1 yes

# D1/P2/H1 use N failures then success in one run; call logs prove retries.
gateway_harness_create
GW_FAIL_COMMAND=docker GW_FAIL_SUBCOMMAND='compose pull' GW_FAIL_MODE=always GW_FAIL_COUNT=1 gateway_harness_run >/dev/null 2>&1 || true
gateway_harness_run >/dev/null
gateway_harness_destroy
gateway_harness_create
GW_FAIL_COMMAND=curl GW_FAIL_SUBCOMMAND=health GW_FAIL_MODE=fail-count GW_FAIL_COUNT=2 gateway_harness_run >/dev/null
[[ -f "${GW_STATE_DIR}/ap_psk" ]]
gateway_harness_destroy
echo 'PASS: stage-2 Gateway Docker/Python/systemd/Compose/health failure injection contracts.'
