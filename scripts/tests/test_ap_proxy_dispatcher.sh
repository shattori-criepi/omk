#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT
mkdir -p "${TEMP_DIR}/bin"
cp "${ROOT_DIR}/scripts/omk-ap-proxy-dispatcher" "${TEMP_DIR}/dispatcher"
chmod +x "${TEMP_DIR}/dispatcher"

cat >"${TEMP_DIR}/bin/nmcli" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == -g && "${2:-}" == GENERAL.CONNECTION && "${3:-}" == device && "${4:-}" == show && "${5:-}" == wlan0 ]]
printf '%s\n' "${TEST_CONNECTION:-}"
EOF
cat >"${TEMP_DIR}/bin/ip" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == -4 && "${2:-}" == -o && "${3:-}" == address && "${4:-}" == show && "${5:-}" == dev && "${6:-}" == wlan0 && "${7:-}" == scope && "${8:-}" == global ]]
[[ "${TEST_ADDRESS:-no}" == yes ]] && printf '3: wlan0    inet 192.168.50.1/24 scope global wlan0\n'
EOF
cat >"${TEMP_DIR}/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${TEST_CALLS}"
EOF
chmod +x "${TEMP_DIR}/bin/"*

run_dispatcher() {
  local connection="$1" address="$2" interface="$3" action="$4"
  : >"${TEMP_DIR}/calls"
  env PATH="${TEMP_DIR}/bin:${PATH}" TEST_CALLS="${TEMP_DIR}/calls" \
    TEST_CONNECTION="${connection}" TEST_ADDRESS="${address}" \
    "${TEMP_DIR}/dispatcher" "${interface}" "${action}"
}

# Link/down events, a different active profile, and an AP without its address
# must not bind FreeBind sockets during recovery.
run_dispatcher omk-ap yes wlan0 down
[[ ! -s "${TEMP_DIR}/calls" ]]
run_dispatcher omk-ap yes eth0 up
[[ ! -s "${TEMP_DIR}/calls" ]]
run_dispatcher other yes wlan0 up
[[ ! -s "${TEMP_DIR}/calls" ]]
run_dispatcher omk-ap no wlan0 up
[[ ! -s "${TEMP_DIR}/calls" ]]

run_dispatcher omk-ap yes wlan0 up
grep -Fxq 'start omk-mqtt-ap-proxy.socket omk-dashboard-ap-proxy.socket' "${TEMP_DIR}/calls"
[[ "$(wc -l <"${TEMP_DIR}/calls")" == 1 ]]

echo 'PASS: AP proxy dispatcher starts listeners only after verified omk-ap activation.'
