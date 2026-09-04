#!/usr/bin/env bash

# Verify kanshi's Trixie profile syntax and non-destructive OMK profile update.
set -euo pipefail

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-dashboard-kiosk.sh"
TEST_DIRECTORY="$(mktemp -d)"
trap 'rm -rf -- "${TEST_DIRECTORY}"' EXIT

# Load only the pure renderer; do not run the kiosk setup's main flow.
# shellcheck disable=SC1090
source <(sed -n '/^render_kanshi_config()/,/^}$/p' "${SCRIPT_PATH}")

KANSHI_DSI_OUTPUT='output DSI-1 enable scale 1.000000 mode 720x1280@60.038 position 0,0 transform 90'

render_and_rerun() {
  local name="$1"
  local source="${TEST_DIRECTORY}/${name}.config"
  local rendered="${TEST_DIRECTORY}/${name}.rendered"
  local rerun="${TEST_DIRECTORY}/${name}.rerun"
  render_kanshi_config "${source}" "${rendered}"
  render_kanshi_config "${rendered}" "${rerun}"
  cmp -s "${rendered}" "${rerun}"
}

# An absent config receives exactly one syntactically valid OMK profile.
: >"${TEST_DIRECTORY}/missing.config"
render_and_rerun missing
grep -Fxq 'profile omk-kiosk {' "${TEST_DIRECTORY}/missing.rendered"
grep -Fxq "    ${KANSHI_DSI_OUTPUT}" "${TEST_DIRECTORY}/missing.rendered"
[[ "$(grep -Fc 'profile omk-kiosk {' "${TEST_DIRECTORY}/missing.rendered")" == 1 ]]

# The former invalid top-level OMK directive migrates into the profile, without
# changing another top-level output or existing backup files.
cat >"${TEST_DIRECTORY}/legacy.config" <<'EOF'
# retain this comment
output HDMI-A-1 enable mode 1920x1080@60.000 position 0,0 transform normal
output DSI-1 enable scale 1.000000 mode 720x1280@60.038 position 0,0 transform 270
EOF
printf 'preserve init backup\n' >"${TEST_DIRECTORY}/config.init"
printf 'preserve backup\n' >"${TEST_DIRECTORY}/config.bak"
INIT_SUM="$(sha256sum "${TEST_DIRECTORY}/config.init")"
BAK_SUM="$(sha256sum "${TEST_DIRECTORY}/config.bak")"
render_and_rerun legacy
grep -Fxq '# retain this comment' "${TEST_DIRECTORY}/legacy.rendered"
grep -Fxq 'output HDMI-A-1 enable mode 1920x1080@60.000 position 0,0 transform normal' "${TEST_DIRECTORY}/legacy.rendered"
grep -Fxq "    ${KANSHI_DSI_OUTPUT}" "${TEST_DIRECTORY}/legacy.rendered"
if grep -Fq 'transform 270' "${TEST_DIRECTORY}/legacy.rendered"; then
  echo 'Legacy DSI-1 directive was not removed.' >&2
  exit 1
fi
[[ "$(sha256sum "${TEST_DIRECTORY}/config.init")" == "${INIT_SUM}" ]]
[[ "$(sha256sum "${TEST_DIRECTORY}/config.bak")" == "${BAK_SUM}" ]]

# Update only DSI-1 in the existing OMK profile; retain its comment and other
# output while a user profile (including its DSI-1 setting) remains bytewise.
cat >"${TEST_DIRECTORY}/profiles.config" <<'EOF'
# global comment
profile user-presentation {
    # user-owned output must remain unchanged
    output DSI-1 enable mode 800x480@60.000 position 0,0 transform normal
}

profile omk-kiosk {
    # OMK display rotation
    output HDMI-A-1 enable mode 1920x1080@60.000 position 0,0 transform normal
    output DSI-1 enable scale 1.000000 mode 720x1280@60.038 position 0,0 transform 270
}
EOF
render_and_rerun profiles
grep -Fxq '# global comment' "${TEST_DIRECTORY}/profiles.rendered"
grep -Fxq '    # OMK display rotation' "${TEST_DIRECTORY}/profiles.rendered"
grep -Fxq '    output HDMI-A-1 enable mode 1920x1080@60.000 position 0,0 transform normal' "${TEST_DIRECTORY}/profiles.rendered"
grep -Fxq '    output DSI-1 enable mode 800x480@60.000 position 0,0 transform normal' "${TEST_DIRECTORY}/profiles.rendered"
grep -Fxq "    ${KANSHI_DSI_OUTPUT}" "${TEST_DIRECTORY}/profiles.rendered"
if grep -Fq 'output DSI-1 enable scale 1.000000 mode 720x1280@60.038 position 0,0 transform 270' "${TEST_DIRECTORY}/profiles.rendered"; then
  echo 'Existing OMK DSI-1 setting was not updated.' >&2
  exit 1
fi
[[ "$(grep -Fc 'profile omk-kiosk {' "${TEST_DIRECTORY}/profiles.rendered")" == 1 ]]

# An OMK profile without DSI-1 gains only the OMK DSI-1 directive.
cat >"${TEST_DIRECTORY}/omk-no-dsi.config" <<'EOF'
profile omk-kiosk {
    # retain this OMK comment
    output HDMI-A-1 enable transform normal
}
EOF
render_and_rerun omk-no-dsi
grep -Fxq '    # retain this OMK comment' "${TEST_DIRECTORY}/omk-no-dsi.rendered"
grep -Fxq '    output HDMI-A-1 enable transform normal' "${TEST_DIRECTORY}/omk-no-dsi.rendered"
grep -Fxq "    ${KANSHI_DSI_OUTPUT}" "${TEST_DIRECTORY}/omk-no-dsi.rendered"

# Duplicate managed profiles collapse to one. Other profiles stay intact.
cat >"${TEST_DIRECTORY}/duplicate.config" <<'EOF'
profile omk-kiosk {
    output DSI-1 enable transform 270
}
profile user-profile {
    output HDMI-A-1 enable transform normal
}
profile omk-kiosk {
    output DSI-1 enable transform 180
}
EOF
render_and_rerun duplicate
[[ "$(grep -Fc 'profile omk-kiosk {' "${TEST_DIRECTORY}/duplicate.rendered")" == 1 ]]
grep -Fxq 'profile user-profile {' "${TEST_DIRECTORY}/duplicate.rendered"
grep -Fxq "    ${KANSHI_DSI_OUTPUT}" "${TEST_DIRECTORY}/duplicate.rendered"

# If kanshi is available, reject the exact parser errors that caused the
# Raspberry Pi OS 13 failure. A missing Wayland compositor is acceptable here:
# parsing happens before the connection attempt.
if command -v kanshi >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
  parser_output="$(timeout 2 kanshi -c "${TEST_DIRECTORY}/legacy.rendered" 2>&1 || true)"
  if grep -Eqi "unknown directive|failed to parse config" <<<"${parser_output}"; then
    echo "kanshi rejected generated profile syntax: ${parser_output}" >&2
    exit 1
  fi
fi

echo 'PASS: kanshi profile renderer migrates legacy directives, preserves user profiles, and is idempotent.'
