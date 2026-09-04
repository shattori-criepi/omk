#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-dashboard-kiosk.sh"
UNIT_TEMPLATE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)/systemd/omk-dashboard-kiosk.service.in"
TEST_DIRECTORY="$(mktemp -d)"
trap 'rm -rf -- "${TEST_DIRECTORY}"' EXIT

# The policy content comes from the production renderer, not a duplicate test
# fixture.  Rendering it twice must be byte-for-byte stable.
# shellcheck disable=SC1090
source <(awk '
  /^render_chromium_policy\(\)/ { printing=1 }
  printing { print }
  printing && /^EOF$/ { heredoc_complete=1 }
  printing && heredoc_complete && /^}$/ { exit }
' "${SCRIPT_PATH}")
render_chromium_policy >"${TEST_DIRECTORY}/first.json"
render_chromium_policy >"${TEST_DIRECTORY}/second.json"
cmp -s "${TEST_DIRECTORY}/first.json" "${TEST_DIRECTORY}/second.json"
cat >"${TEST_DIRECTORY}/expected.json" <<'EOF'
{
  "TranslateEnabled": false
}
EOF
cmp -s "${TEST_DIRECTORY}/expected.json" "${TEST_DIRECTORY}/first.json"

# Exercise the production install/cmp branch using a private fake privilege
# boundary.  It records the requested root mode but writes only under the test
# directory, so no host policy path is touched.
# shellcheck disable=SC1090
source <(awk '/^install_chromium_policy\(\)/ {printing=1} printing {print} printing && /^}$/ {exit}' "${SCRIPT_PATH}")
mkdir -p "${TEST_DIRECTORY}/bin"
cat >"${TEST_DIRECTORY}/bin/sudo" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == '-v' ]]; then
  exit 0
fi
exec "$@"
EOF
cat >"${TEST_DIRECTORY}/bin/install" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${POLICY_INSTALL_LOG}"
directory=false
while (($#)); do
  case "$1" in
    -d) directory=true; shift ;;
    -o|-g|-m) shift 2 ;;
    *) break ;;
  esac
done
if "${directory}"; then
  mkdir -p "$1"
else
  cp "$1" "$2"
fi
EOF
cat >"${TEST_DIRECTORY}/bin/stat" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' '0:0:644'
EOF
chmod +x "${TEST_DIRECTORY}/bin/sudo" "${TEST_DIRECTORY}/bin/install" "${TEST_DIRECTORY}/bin/stat"
POLICY_INSTALL_LOG="${TEST_DIRECTORY}/install.log"
export POLICY_INSTALL_LOG
PATH="${TEST_DIRECTORY}/bin:${PATH}"
CHROMIUM_POLICY_DIRECTORY="${TEST_DIRECTORY}/policy/managed"
CHROMIUM_POLICY_FILE="${CHROMIUM_POLICY_DIRECTORY}/omk-kiosk.json"
SUDO=()
CHROMIUM_POLICY_CHANGED=false
log() { :; }
fail() { echo "unexpected policy install failure: $*" >&2; return 1; }
install_chromium_policy
[[ "${CHROMIUM_POLICY_CHANGED}" == true ]]
cmp -s "${TEST_DIRECTORY}/expected.json" "${CHROMIUM_POLICY_FILE}"
CHROMIUM_POLICY_CHANGED=false
install_chromium_policy
[[ "${CHROMIUM_POLICY_CHANGED}" == false ]]
[[ "$(grep -c -- '-m 0644' "${POLICY_INSTALL_LOG}")" == 1 ]]

# The installation contract keeps policy files outside the user-writable
# profile and makes a rerun a no-op when their contents already match.
grep -Fq "CHROMIUM_POLICY_DIRECTORY='/etc/chromium/policies/managed'" "${SCRIPT_PATH}"
grep -Fq "CHROMIUM_POLICY_FILE='/etc/chromium/policies/managed/omk-kiosk.json'" "${SCRIPT_PATH}"
grep -Fq 'install -d -o root -g root -m 0755 "${CHROMIUM_POLICY_DIRECTORY}"' "${SCRIPT_PATH}"
grep -Fq 'install -o root -g root -m 0644 "${candidate}" "${CHROMIUM_POLICY_FILE}"' "${SCRIPT_PATH}"
grep -Fq 'cmp -s "${candidate}" "${CHROMIUM_POLICY_FILE}"' "${SCRIPT_PATH}"
grep -Fq "stat -c '%u:%g:%a' \"\${CHROMIUM_POLICY_FILE}\"" "${SCRIPT_PATH}"
grep -Fq 'CHROMIUM_POLICY_CHANGED=true' "${SCRIPT_PATH}"
grep -Fq 'install_chromium_policy' "${SCRIPT_PATH}"
grep -Fq 'ensure_kiosk_profile_directory' "${SCRIPT_PATH}"

# Chromium uses an OMK-only profile and kiosk-specific startup options.  The
# managed policy, rather than a command-line translate switch, is authoritative.
grep -Fq -- '--user-data-dir=@KIOSK_PROFILE_DIRECTORY@' "${UNIT_TEMPLATE}"
grep -Fq -- '--no-first-run' "${UNIT_TEMPLATE}"
grep -Fq -- '--no-default-browser-check' "${UNIT_TEMPLATE}"
if grep -Fqi 'translate' "${UNIT_TEMPLATE}"; then
  echo 'Chromium unit must rely on the managed TranslateEnabled policy, not a translate flag.' >&2
  exit 1
fi

# A changed policy restarts an already active user service so Chromium reads it.
grep -Fq '"${CHROMIUM_POLICY_CHANGED}" == true' "${SCRIPT_PATH}"
grep -Fq 'user_systemctl restart "${UNIT_NAME}"' "${SCRIPT_PATH}"

# Persistent configuration must not require a currently running GUI session:
# without a user bus, the production helper creates the normal default.target
# enable link and the main flow exits successfully after static validation.
grep -Fq 'default.target.wants' "${SCRIPT_PATH}"
grep -Fq 'No active Wayland GUI session; kiosk persistent configuration is complete.' "${SCRIPT_PATH}"
grep -Fq 'The kiosk will take effect on the next graphical session/reboot.' "${SCRIPT_PATH}"
grep -Fq 'enable_kiosk_unit_persistently' "${SCRIPT_PATH}"
if grep -Fq 'No user systemd bus for ${TARGET_USER}. Log into the graphical session first.' "${SCRIPT_PATH}"; then
  echo 'Kiosk setup still requires an active user bus before persistent configuration.' >&2
  exit 1
fi

echo 'PASS: Chromium kiosk managed policy, dedicated profile, and rerun contract are defined.'
