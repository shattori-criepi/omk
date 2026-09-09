#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATEWAY_HARNESS_SOURCE_ROOT="${ROOT}"
source "${ROOT}/scripts/tests/lib/gateway_harness.sh"

seed_identity() {
  printf '%s\n' existing-site-uuid >"${GW_STATE_DIR}/site_uuid"
  printf '%s\n' existing-token >"${GW_STATE_DIR}/token"
  chmod 600 "${GW_STATE_DIR}/site_uuid" "${GW_STATE_DIR}/token"
  printf '%s\n' 'state/site_uuid|omk|omk|600' 'state/token|omk|omk|600' >>"${GW_STATE_DIR}/owner-mode"
}

assert_single_line() {
  local file="$1" value="$2"
  [[ "$(grep -Fxc "${value}" "${GW_STATE_DIR}/${file}")" == 1 ]]
}

run_checkpoint() {
  local cp="$1" point="$2" identity_mode="$3" status uuid_before token_before
  gateway_harness_create
  [[ "${identity_mode}" == seeded ]] && seed_identity
  export GW_CHECKPOINT="${point}"

  if gateway_harness_run --with-base >"${GATEWAY_HARNESS_ROOT}/${cp}-first.log" 2>&1; then
    echo "FAIL: ${cp} did not interrupt." >&2
    exit 1
  else
    status=$?
  fi
  [[ "${status}" == 99 ]]
  if grep -Fq 'nmcli connection up omk-ap' "${GW_CALL_LOG}"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
  cp "${GW_CALL_LOG}" "${GATEWAY_HARNESS_ROOT}/${cp}-first.calls"
  gateway_harness_artifact_snapshot >"${GATEWAY_HARNESS_ROOT}/${cp}-before.snapshot"
  uuid_before=''; token_before=''
  [[ ! -e "${GW_STATE_DIR}/site_uuid" ]] || uuid_before="$(<"${GW_STATE_DIR}/site_uuid")"
  [[ ! -e "${GW_STATE_DIR}/token" ]] || token_before="$(<"${GW_STATE_DIR}/token")"

  gateway_harness_run --with-base >"${GATEWAY_HARNESS_ROOT}/${cp}-rerun.log" 2>&1
  grep -Fq 'apt-get update' "${GW_CALL_LOG}"
  [[ "$(grep -Fc 'apt-get update' "${GW_CALL_LOG}")" -ge 2 ]]
  [[ "$(grep -Fc 'dpkg --print-architecture' "${GW_CALL_LOG}")" -ge 2 ]]
  [[ "$(grep -Fc 'nmcli connection up omk-ap' "${GW_CALL_LOG}")" == 1 ]]
  awk '/setup-data-collection.sh --prepare/ {prepare=NR} /nmcli connection up omk-ap/ {ap=NR} END {exit !(prepare < ap && ap > 0)}' "${GW_CALL_LOG}"
  grep -Fq 'docker compose up -d' "${GW_CALL_LOG}"
  grep -Fq 'curl --fail http://127.0.0.1:8000/health' "${GW_CALL_LOG}"

  gateway_harness_artifact_snapshot >"${GATEWAY_HARNESS_ROOT}/${cp}-after.snapshot"
  while IFS= read -r line; do grep -Fxq "${line}" "${GATEWAY_HARNESS_ROOT}/${cp}-after.snapshot"; done <"${GATEWAY_HARNESS_ROOT}/${cp}-before.snapshot"
  [[ -z "${uuid_before}" || "${uuid_before}" == "$(<"${GW_STATE_DIR}/site_uuid")" ]]
  [[ -z "${token_before}" || "${token_before}" == "$(<"${GW_STATE_DIR}/token")" ]]
  gateway_harness_assert_artifact_contract
  ! sort "${GW_STATE_DIR}/owner-mode" | uniq -d | grep -q .
  assert_single_line site_uuid "$(<"${GW_STATE_DIR}/site_uuid")"
  assert_single_line token "$(<"${GW_STATE_DIR}/token")"
  assert_single_line token-env token-env
  assert_single_line unit unit
  assert_single_line sudoers sudoers
  assert_single_line network-profile omk-ap

  case "${cp}" in
    CP9) ! grep -Fq 'systemctl daemon-reload' "${GATEWAY_HARNESS_ROOT}/${cp}-first.calls" ;;
    CP10) grep -Fq 'systemctl enable service' "${GATEWAY_HARNESS_ROOT}/${cp}-first.calls"; ! grep -Fq 'visudo -cf rule' "${GATEWAY_HARNESS_ROOT}/${cp}-first.calls" ;;
    CP11) ! grep -Fq 'docker compose up -d' "${GATEWAY_HARNESS_ROOT}/${cp}-first.calls" ;;
  esac
  unset GW_CHECKPOINT
  gateway_harness_destroy
}

# Pre-base and base stages have a pre-existing local identity; later stages
# exercise identity generated before the checkpoint and preserved on rerun.
run_checkpoint CP1 apt-update seeded
run_checkpoint CP2 os-upgrade seeded
run_checkpoint CP3 package-install seeded
run_checkpoint CP4 docker-install seeded
run_checkpoint CP5 docker-pull seeded
run_checkpoint CP6 docker-build seeded
run_checkpoint CP7 venv generated
run_checkpoint CP8 pip generated
run_checkpoint CP9 unit generated
run_checkpoint CP10 enable generated
run_checkpoint CP11 pre-ap generated

echo 'PASS: CP1-CP11 interrupt once, rerun safely, preserve artifacts, and activate AP only after preparation.'
