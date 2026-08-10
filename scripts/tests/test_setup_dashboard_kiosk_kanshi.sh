#!/usr/bin/env bash

# Verify that the kiosk setup changes only the DSI-1 output line in kanshi.
set -euo pipefail

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-dashboard-kiosk.sh"
TEST_DIRECTORY="$(mktemp -d)"
trap 'rm -rf -- "${TEST_DIRECTORY}"' EXIT

# Load only the pure renderer; do not run the kiosk setup's main flow.
# shellcheck disable=SC1090
source <(sed -n '/^render_kanshi_config()/,/^}$/p' "${SCRIPT_PATH}")

KANSHI_DSI_OUTPUT='output DSI-1 enable scale 1.000000 mode 720x1280@60.038 position 0,0 transform 90'
SOURCE_CONFIG="${TEST_DIRECTORY}/config"
CANDIDATE_CONFIG="${TEST_DIRECTORY}/candidate"
BACKUP_INIT="${TEST_DIRECTORY}/config.init"
BACKUP_BAK="${TEST_DIRECTORY}/config.bak"

cat >"${SOURCE_CONFIG}" <<'EOF'
output HDMI-A-1 enable mode 1920x1080@60.000 position 0,0 transform normal
output DSI-1 enable scale 1.000000 mode 720x1280@60.038 position 0,0 transform 270
EOF
printf 'preserve init backup\n' >"${BACKUP_INIT}"
printf 'preserve backup\n' >"${BACKUP_BAK}"
INIT_SUM="$(sha256sum "${BACKUP_INIT}")"
BAK_SUM="$(sha256sum "${BACKUP_BAK}")"

render_kanshi_config "${SOURCE_CONFIG}" "${CANDIDATE_CONFIG}"

grep -Fxq "${KANSHI_DSI_OUTPUT}" "${CANDIDATE_CONFIG}"
grep -Fxq 'output HDMI-A-1 enable mode 1920x1080@60.000 position 0,0 transform normal' "${CANDIDATE_CONFIG}"
if grep -Fq 'transform 270' "${CANDIDATE_CONFIG}"; then
  echo 'DSI-1 rotation was not updated.' >&2
  exit 1
fi
[[ "$(sha256sum "${BACKUP_INIT}")" == "${INIT_SUM}" ]]
[[ "$(sha256sum "${BACKUP_BAK}")" == "${BAK_SUM}" ]]

echo 'PASS: kanshi renderer updates DSI-1 to transform 90 and preserves other outputs/backups.'
