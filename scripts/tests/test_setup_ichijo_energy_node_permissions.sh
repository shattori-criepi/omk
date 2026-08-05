#!/usr/bin/env bash

# Exercise permission decisions without running the setup script's main flow.
set -euo pipefail

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-ichijo-energy-node.sh"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "${TEST_ROOT}"' EXIT

# shellcheck disable=SC1090
source <(sed -n '/^target_can_write_directory()/,/^}$/p' "${SCRIPT_PATH}")
# shellcheck disable=SC1090
source <(sed -n '/^environment_mode_has_group_or_world_permissions()/,/^}$/p' "${SCRIPT_PATH}")
# shellcheck disable=SC1090
source <(sed -n '/^plan_log_directory()/,/^}$/p' "${SCRIPT_PATH}")
# shellcheck disable=SC1090
source <(sed -n '/^ensure_log_directory()/,/^}$/p' "${SCRIPT_PATH}")

TARGET_USER="$(id -un)"
TARGET_GROUP="$(id -gn)"
AS_TARGET=()
log() { printf '%s\n' "$*"; }
fail() { return 1; }
directory_state() { stat -c 'owner=%U:%G uid=%u gid=%g mode=%a' "$1"; }

if environment_mode_has_group_or_world_permissions 600; then
  echo 'mode 600 was incorrectly considered group/world-readable.' >&2
  exit 1
fi
for mode in 640 644 660 666; do
  environment_mode_has_group_or_world_permissions "${mode}"
done

LOG_DIR="${TEST_ROOT}/writable"
mkdir "${LOG_DIR}"
chmod 700 "${LOG_DIR}"
before_state="$(stat -c '%u:%g:%a' "${LOG_DIR}")"
plan_log_directory >/dev/null
after_state="$(stat -c '%u:%g:%a' "${LOG_DIR}")"
[[ "${before_state}" == "${after_state}" ]]

LOG_DIR="${TEST_ROOT}/requires-repair"
mkdir "${LOG_DIR}"
chmod 500 "${LOG_DIR}"
target_can_write_directory() { return 1; }
plan_log_directory | grep -Fq 'Would repair only setup log directory ownership/owner permissions'

parent_dir="${TEST_ROOT}/root-owned-parent"
mkdir "${parent_dir}"
chmod 500 "${parent_dir}"
parent_state_before="$(stat -c '%u:%g:%a' "${parent_dir}")"
LOG_DIR="${parent_dir}/setup"
capture_file="${TEST_ROOT}/install-command"
SUDO=(mock_sudo)
mock_sudo() { printf '%q ' "$@" > "${capture_file}"; }
target_can_write_directory() { return 0; }
ensure_log_directory
parent_state_after="$(stat -c '%u:%g:%a' "${parent_dir}")"
[[ "${parent_state_before}" == "${parent_state_after}" ]]
grep -Fq "install -d -o ${TARGET_USER} -g ${TARGET_GROUP} -m 0775 ${LOG_DIR}" "${capture_file}"
[[ ! -e "${LOG_DIR}" ]]
chmod 700 "${parent_dir}"

LOG_DIR="${TEST_ROOT}/not-a-directory"
: > "${LOG_DIR}"
if plan_log_directory; then
  echo 'Non-directory log path was accepted.' >&2
  exit 1
fi

echo 'PASS: mode checks and setup-log directory plans are safe.'
