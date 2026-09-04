#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-dashboard-kiosk.sh"
TEST_DIRECTORY="$(mktemp -d)"
trap 'rm -rf -- "${TEST_DIRECTORY}"' EXIT
KANSHI_AUTOSTART_MARKER='# OMK: start kanshi for DSI-1 kiosk rotation'
KANSHI_AUTOSTART_COMMAND='kanshi >/dev/null 2>&1 &'

# Load pure autostart rendering and runtime helpers without kiosk main flow.
# shellcheck disable=SC1090
source <(awk '/^render_labwc_autostart\(\)/ {printing=1} printing {print} printing && /^}$/ {exit}' "${SCRIPT_PATH}")
# shellcheck disable=SC1090
source <(awk '/^kanshi_is_running\(\)/ {printing=1} /^render_labwc_config\(\)/ {exit} printing {print}' "${SCRIPT_PATH}")

render_autostart_twice() {
  local source="$1"
  local rendered="${source}.rendered"
  local rerun="${source}.rerun"
  render_labwc_autostart "${source}" "${rendered}"
  render_labwc_autostart "${rendered}" "${rerun}"
  cmp -s "${rendered}" "${rerun}"
}

# No autostart creates a backgrounded OMK command and exactly one marker.
render_autostart_twice "${TEST_DIRECTORY}/missing-autostart"
grep -Fxq "${KANSHI_AUTOSTART_MARKER}" "${TEST_DIRECTORY}/missing-autostart.rendered"
grep -Fxq "${KANSHI_AUTOSTART_COMMAND}" "${TEST_DIRECTORY}/missing-autostart.rendered"
[[ "$(grep -Fc "${KANSHI_AUTOSTART_MARKER}" "${TEST_DIRECTORY}/missing-autostart.rendered")" == 1 ]]

# Existing unrelated autostart entries remain byte-for-byte present.
cat >"${TEST_DIRECTORY}/existing-autostart" <<'EOF'
# local startup
exec swayidle -w
custom-command --keep
EOF
render_autostart_twice "${TEST_DIRECTORY}/existing-autostart"
grep -Fxq 'exec swayidle -w' "${TEST_DIRECTORY}/existing-autostart.rendered"
grep -Fxq 'custom-command --keep' "${TEST_DIRECTORY}/existing-autostart.rendered"
grep -Fxq "${KANSHI_AUTOSTART_COMMAND}" "${TEST_DIRECTORY}/existing-autostart.rendered"

# A legacy OMK foreground entry upgrades in place.  A user-supplied launcher,
# including common background and wrapper forms, is preserved without an OMK
# duplicate.
printf '%s\nkanshi\n' "${KANSHI_AUTOSTART_MARKER}" >"${TEST_DIRECTORY}/omk-autostart"
render_autostart_twice "${TEST_DIRECTORY}/omk-autostart"
[[ "$(grep -Fc "${KANSHI_AUTOSTART_MARKER}" "${TEST_DIRECTORY}/omk-autostart.rendered")" == 1 ]]
grep -Fxq "${KANSHI_AUTOSTART_COMMAND}" "${TEST_DIRECTORY}/omk-autostart.rendered"
for launcher in \
  'kanshi &' \
  'kanshi >/dev/null 2>&1 &' \
  'exec kanshi' \
  '/usr/bin/kanshi &' \
  'env XDG_CURRENT_DESKTOP=labwc kanshi &' \
  'nohup kanshi >/dev/null 2>&1 &'; do
  source_path="${TEST_DIRECTORY}/user-kanshi-${RANDOM}-autostart"
  printf '%s\n' "${launcher}" >"${source_path}"
  render_autostart_twice "${source_path}"
  cmp -s "${source_path}" "${source_path}.rendered"
done

# labwc evaluates autostart as a shell script.  The OMK command must not hold
# the shell in the foreground and prevent following user commands from running.
mkdir -p "${TEST_DIRECTORY}/autostart-bin"
cat >"${TEST_DIRECTORY}/autostart-bin/kanshi" <<'EOF'
#!/bin/sh
touch "${KANSHI_STARTED}"
sleep 1
EOF
chmod +x "${TEST_DIRECTORY}/autostart-bin/kanshi"
AUTOSTART_FOLLOWUP="${TEST_DIRECTORY}/autostart-followup"
KANSHI_STARTED="${TEST_DIRECTORY}/kanshi-started"
export AUTOSTART_FOLLOWUP KANSHI_STARTED
{
  printf '%s\n' "${KANSHI_AUTOSTART_MARKER}"
  printf '%s\n' 'kanshi'
  printf 'printf "%%s\\n" after-kanshi > "%s"\n' "${AUTOSTART_FOLLOWUP}"
} >"${TEST_DIRECTORY}/flow-autostart"
render_autostart_twice "${TEST_DIRECTORY}/flow-autostart"
PATH="${TEST_DIRECTORY}/autostart-bin:${PATH}" sh "${TEST_DIRECTORY}/flow-autostart.rendered"
grep -Fxq 'after-kanshi' "${AUTOSTART_FOLLOWUP}"
grep -Fxq "${KANSHI_AUTOSTART_COMMAND}" "${TEST_DIRECTORY}/flow-autostart.rendered"

PROCESS_STATE="${TEST_DIRECTORY}/kanshi-running"
START_COUNT_FILE="${TEST_DIRECTORY}/start-count"
TARGET_USER='omkdev'
AS_TARGET=()
USER_SYSTEMD_ENV=()
WAYLAND_DISPLAY='wayland-0'
KANSHI_PATH="${TEST_DIRECTORY}/kanshi"
WLR_RANDR_PATH="${TEST_DIRECTORY}/wlr-randr"
KANSHI_STARTUP_LOG="${TEST_DIRECTORY}/kanshi-startup.log"
touch "${KANSHI_PATH}" "${WLR_RANDR_PATH}"
chmod +x "${KANSHI_PATH}" "${WLR_RANDR_PATH}"
log() { :; }
sleep() { :; }
pgrep() {
  if [[ -e "${PROCESS_STATE}" ]]; then
    printf '%s\n' 4242
    return 0
  fi
  return 1
}
run_in_wayland_session() {
  if [[ "$1" == sh ]]; then
    touch "${PROCESS_STATE}"
    count=0; [[ -f "${START_COUNT_FILE}" ]] && count="$(<"${START_COUNT_FILE}")"
    printf '%s' "$((count + 1))" >"${START_COUNT_FILE}"
    return 0
  fi
  printf '%s\n' 'DSI-1' '  Transform: 90'
}

# A running kanshi is reused, not duplicated, and Transform: 90 is accepted.
touch "${PROCESS_STATE}"
KANSHI_CONFIG_CHANGED=false
ensure_kanshi_running
[[ ! -e "${START_COUNT_FILE}" ]]
verify_kanshi_transform

# No existing kanshi starts one process and then accepts the applied transform.
rm -f "${PROCESS_STATE}" "${START_COUNT_FILE}"
ensure_kanshi_running
[[ "$(<"${START_COUNT_FILE}")" == 1 ]]
verify_kanshi_transform

# A normal transform must not be treated as success.
run_in_wayland_session() { printf '%s\n' 'DSI-1' '  Transform: normal'; }
if verify_kanshi_transform; then
  echo 'Transform normal was incorrectly accepted.' >&2
  exit 1
fi

# A stale Transform: 90 report is not enough when kanshi itself has exited.
run_in_wayland_session() { printf '%s\n' 'DSI-1' '  Transform: 90'; }
rm -f "${PROCESS_STATE}"
if verify_kanshi_transform; then
  echo 'Transform 90 was accepted after kanshi exited.' >&2
  exit 1
fi

# A process that exits immediately exposes its captured stderr instead of
# silently discarding a kanshi parser error.
rm -f "${PROCESS_STATE}" "${KANSHI_STARTUP_LOG}"
RUNTIME_LOG="${TEST_DIRECTORY}/runtime.log"
log() { printf '%s\n' "$*" >>"${RUNTIME_LOG}"; }
fail() { return 1; }
run_in_wayland_session() {
  if [[ "$1" == sh ]]; then
    printf '%s\n' "unknown directive 'output'" 'failed to parse config file' >"$6"
    return 0
  fi
  printf '%s\n' 'DSI-1' '  Transform: 90'
}
if ensure_kanshi_running; then
  echo 'A kanshi process that exits immediately was incorrectly accepted.' >&2
  exit 1
fi
grep -Fq "unknown directive 'output'" "${RUNTIME_LOG}"

echo 'PASS: kanshi labwc autostart and active-session rotation are non-duplicating and verified.'
