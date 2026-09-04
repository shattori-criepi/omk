#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-dashboard-kiosk.sh"
TEST_DIRECTORY="$(mktemp -d)"
trap 'rm -rf -- "${TEST_DIRECTORY}"' EXIT

# Load the pure XML renderer without entering kiosk setup's host-side flow.
# shellcheck disable=SC1090
source <(awk '/^render_labwc_config\(\)/ {printing=1} printing {print} printing && /^}$/ {exit}' "${SCRIPT_PATH}")

verify_rendered_config() {
  local path="$1" expect_xinclude="$2"
  python3 - "${path}" "${expect_xinclude}" <<'PY'
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

path = Path(sys.argv[1])
expect_xinclude = sys.argv[2] == "yes"
openbox = "http://openbox.org/3.4/rc"
xinclude = "http://www.w3.org/2001/XInclude"
root = ET.parse(path).getroot()
assert root.tag == "labwc_config", root.tag
for element in root.iter():
    if isinstance(element.tag, str):
        assert not element.tag.startswith(f"{{{openbox}}}"), element.tag
keyboard = root.find("keyboard")
assert keyboard is not None
keybind = next(item for item in keyboard.findall("keybind") if item.get("key") == "A-W-h")
actions = keybind.findall("action")
assert sum(action.get("name") == "HideCursor" for action in actions) == 1
assert sum(action.get("name") == "WarpCursor" and action.get("x") == "-1" and action.get("y") == "-1" for action in actions) == 1
assert (root.find(f"{{{xinclude}}}include") is not None) == expect_xinclude
PY
}

render_and_rerun() {
  local name="$1" expect_xinclude="$2"
  local source="${TEST_DIRECTORY}/${name}.xml"
  local rendered="${TEST_DIRECTORY}/${name}.rendered.xml"
  local rerun="${TEST_DIRECTORY}/${name}.rerun.xml"
  render_labwc_config "${source}" "${rendered}"
  verify_rendered_config "${rendered}" "${expect_xinclude}"
  render_labwc_config "${rendered}" "${rerun}"
  cmp -s "${rendered}" "${rerun}"
}

cat >"${TEST_DIRECTORY}/openbox.xml" <<'EOF'
<openbox_config>
  <!-- preserve this comment -->
  <theme><name>keep-theme</name></theme>
</openbox_config>
EOF
render_and_rerun openbox no
grep -Fq '<!-- preserve this comment -->' "${TEST_DIRECTORY}/openbox.rendered.xml"
grep -Fq 'keep-theme' "${TEST_DIRECTORY}/openbox.rendered.xml"

# Raspberry Pi OS 13's observed labwc compatibility file uses this root tag.
cat >"${TEST_DIRECTORY}/openbox-namespaced.xml" <<'EOF'
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <!-- Raspberry Pi OS 13 Openbox compatibility config -->
  <keyboard><keybind key="A-W-h"><action name="Other" /></keybind></keyboard>
  <theme><name>keep-openbox-settings</name></theme>
</openbox_config>
EOF
render_and_rerun openbox-namespaced no
grep -Fq 'Raspberry Pi OS 13 Openbox compatibility config' "${TEST_DIRECTORY}/openbox-namespaced.rendered.xml"
grep -Fq 'name="Other"' "${TEST_DIRECTORY}/openbox-namespaced.rendered.xml"
grep -Fq 'keep-openbox-settings' "${TEST_DIRECTORY}/openbox-namespaced.rendered.xml"

cat >"${TEST_DIRECTORY}/labwc.xml" <<'EOF'
<labwc_config>
  <!-- retain labwc comment -->
  <keyboard><keybind key="A-W-h"><action name="HideCursor" /><action name="WarpCursor" x="-1" y="-1" /></keybind></keyboard>
  <theme><name>existing-labwc</name></theme>
</labwc_config>
EOF
render_and_rerun labwc no
grep -Fq 'retain labwc comment' "${TEST_DIRECTORY}/labwc.rendered.xml"

cat >"${TEST_DIRECTORY}/xinclude.xml" <<'EOF'
<openbox_config xmlns="http://openbox.org/3.4/rc" xmlns:xi="http://www.w3.org/2001/XInclude">
  <xi:include href="extra.xml" />
  <keyboard />
</openbox_config>
EOF
render_and_rerun xinclude yes
grep -Fq 'xi:include' "${TEST_DIRECTORY}/xinclude.rendered.xml"

cat >"${TEST_DIRECTORY}/unknown.xml" <<'EOF'
<unknown_config><keyboard /></unknown_config>
EOF
if render_labwc_config "${TEST_DIRECTORY}/unknown.xml" "${TEST_DIRECTORY}/unknown.rendered.xml" >/dev/null 2>&1; then
  echo 'Unknown root was accepted.' >&2
  exit 1
fi
[[ ! -e "${TEST_DIRECTORY}/unknown.rendered.xml" ]]

# The backup/install branch remains in update_labwc_config and is only reached
# when its candidate differs from the existing file.
grep -Fq 'backup="${labwc_config}.bak.$(date' "${SCRIPT_PATH}"
grep -Fq 'cp -a "${labwc_config}" "${backup}"' "${SCRIPT_PATH}"

echo 'PASS: labwc XML accepts Openbox namespace, preserves foreign namespaces/comments, and is idempotent.'
