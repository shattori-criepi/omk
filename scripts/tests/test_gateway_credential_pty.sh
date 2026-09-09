#!/usr/bin/env bash
set -euo pipefail

command -v script >/dev/null 2>&1 || { echo 'SKIP: script(1) is unavailable for PTY credential test.'; exit 0; }
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT_DIR}/scripts/setup-omk-gateway.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT
mkdir -p "${TEMP_DIR}/bin"

{
  sed -n '/^ssh_uses_ap_interface()/,/^}$/p' "${SETUP}"
  sed -n '/^show_ap_credentials_before_wlan0_handoff()/,/^}$/p' "${SETUP}"
} >"${TEMP_DIR}/functions.sh"
cat >"${TEMP_DIR}/bin/ip" <<'EOF'
#!/usr/bin/env bash
printf '198.51.100.10 via 192.0.2.1 dev wlan0 src 192.0.2.2\n'
EOF
cat >"${TEMP_DIR}/bin/nmcli" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *802-11-wireless.ssid* ]]; then printf 'OMK-PTY\n'; else printf 'PtySecretValue1234567890\n'; fi
EOF
cat >"${TEMP_DIR}/bin/sudo" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == -n ]] && shift
exec "$@"
EOF
chmod +x "${TEMP_DIR}/bin/"*
cat >"${TEMP_DIR}/run.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source "${PTY_FUNCTIONS}"
log() { printf 'LOG:%s\n' "$*"; }
fail() { printf 'FAIL:%s\n' "$*" >&2; exit 1; }
show_ap_credentials_before_wlan0_handoff >"${PTY_REGULAR_LOG}" 2>&1
EOF
chmod +x "${TEMP_DIR}/run.sh"

printf 'y\n' | env PATH="${TEMP_DIR}/bin:${PATH}" \
  PTY_FUNCTIONS="${TEMP_DIR}/functions.sh" PTY_REGULAR_LOG="${TEMP_DIR}/regular.log" \
  SSH_CONNECTION='198.51.100.10 40000 192.0.2.2 22' \
  script -qec "${TEMP_DIR}/run.sh" "${TEMP_DIR}/terminal.log" >/dev/null
grep -Fq 'SSID: OMK-PTY' "${TEMP_DIR}/terminal.log"
grep -Fq 'OMK AP password: PtySecretValue1234567890' "${TEMP_DIR}/terminal.log"
if grep -Fq 'PtySecretValue1234567890' "${TEMP_DIR}/regular.log"; then
  echo 'Forbidden secret text leaked from the controlling PTY to regular output.' >&2
  exit 1
fi

echo 'PASS: credential handoff uses a real PTY and does not leak its secret to regular output.'
