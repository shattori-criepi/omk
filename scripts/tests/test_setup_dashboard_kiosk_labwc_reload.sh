#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-dashboard-kiosk.sh"
TEST_DIRECTORY="$(mktemp -d)"
trap 'rm -rf -- "${TEST_DIRECTORY}"' EXIT

source <(sed -n '/^labwc_process_path()/,/^}$/p' "${SCRIPT_PATH}")
source <(sed -n '/^reconfigure_labwc()/,/^}$/p' "${SCRIPT_PATH}")

SAFE_PID="$$"
TARGET_UID="$(id -u)"
TARGET_USER="$(id -un)"
touch "${TEST_DIRECTORY}/labwc"
chmod +x "${TEST_DIRECTORY}/labwc"

run_case() (
  local name="$1"
  shift
  LABWC_PID="${LABWC_PID:-}"
  MANAGER_ENV="${MANAGER_ENV:-}"
  PROCESS_OWNER="${PROCESS_OWNER:-target}"
  KILL_RESULT="${KILL_RESULT:-0}"
  CALLS="${TEST_DIRECTORY}/${name}.calls"
  LOG="${TEST_DIRECTORY}/${name}.log"
  : >"${CALLS}"
  : >"${LOG}"
  log() { printf '%s\n' "$*" >>"${LOG}"; }
  user_systemctl() { [[ "${1:-}" == show-environment ]] && printf '%s\n' "${MANAGER_ENV}"; }
  stat() {
    if [[ "${PROCESS_OWNER}" == other ]]; then
      printf '%s\n' 999999
    else
      command stat "$@"
    fi
  }
  readlink() {
    if [[ "$*" == *"/proc/${SAFE_PID}/exe"* ]]; then
      printf '%s\n' "${TEST_DIRECTORY}/labwc"
    else
      command readlink "$@"
    fi
  }
  kill() { printf '%s\n' "$*" >>"${CALLS}"; return "${KILL_RESULT}"; }
  "$@"
)

# An SSH shell has no LABWC_PID, but the target user's manager does.
MANAGER_ENV="LABWC_PID=${SAFE_PID}" run_case manager reconfigure_labwc
grep -Fxq -- "-HUP ${SAFE_PID}" "${TEST_DIRECTORY}/manager.calls"
grep -Fxq 'Requested labwc configuration reload.' "${TEST_DIRECTORY}/manager.log"

# An explicit value wins over a conflicting value from the user manager.
LABWC_PID="${SAFE_PID}" MANAGER_ENV='LABWC_PID=999999' run_case override reconfigure_labwc
grep -Fxq -- "-HUP ${SAFE_PID}" "${TEST_DIRECTORY}/override.calls"

# No manager PID means no pidless labwc --reconfigure attempt.
MANAGER_ENV='' run_case missing reconfigure_labwc
[[ ! -s "${TEST_DIRECTORY}/missing.calls" ]]
grep -Fq 'No verified labwc PID is available' "${TEST_DIRECTORY}/missing.log"

# A nonexistent PID and a process owned by another user are rejected.
MANAGER_ENV='LABWC_PID=999999' run_case nonexistent reconfigure_labwc
[[ ! -s "${TEST_DIRECTORY}/nonexistent.calls" ]]
grep -Fq 'skipping labwc reload' "${TEST_DIRECTORY}/nonexistent.log"

MANAGER_ENV="LABWC_PID=${SAFE_PID}" PROCESS_OWNER=other run_case other_user reconfigure_labwc
[[ ! -s "${TEST_DIRECTORY}/other_user.calls" ]]
grep -Fq 'skipping labwc reload' "${TEST_DIRECTORY}/other_user.log"

# A real reload failure remains non-fatal but leaves an OMK warning.
MANAGER_ENV="LABWC_PID=${SAFE_PID}" KILL_RESULT=1 run_case reload_failed reconfigure_labwc
grep -Fxq -- "-HUP ${SAFE_PID}" "${TEST_DIRECTORY}/reload_failed.calls"
grep -Fq 'WARN: labwc reload failed' "${TEST_DIRECTORY}/reload_failed.log"

echo 'PASS: labwc reload uses only a verified target-user PID.'
