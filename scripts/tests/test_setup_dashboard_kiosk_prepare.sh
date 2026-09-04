#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT}/scripts/setup-dashboard-kiosk.sh"
TEMP="$(mktemp -d)"
trap 'rm -rf -- "${TEMP}"' EXIT

make_executable() {
  local name="$1"
  cat >"${TEMP}/bin/${name}" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "${TEMP}/bin/${name}"
}

mkdir -p "${TEMP}/bin"
cat >"${TEMP}/bin/sudo" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == -v ]] && exit 0
exec "$@"
EOF
cat >"${TEMP}/bin/apt-get" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${KIOSK_APT_LOG}"
if [[ "$*" == *install* ]]; then
  for command in chromium wtype kanshi; do
    printf '%s\n' '#!/usr/bin/env bash' 'exit 0' >"${KIOSK_BIN}/${command}"
    chmod +x "${KIOSK_BIN}/${command}"
  done
fi
EOF
chmod +x "${TEMP}/bin/sudo" "${TEMP}/bin/apt-get"

# Existing Chromium/wtype/kanshi must avoid all apt activity.
for command in chromium wtype kanshi; do make_executable "${command}"; done
KIOSK_APT_LOG="${TEMP}/existing.log" KIOSK_BIN="${TEMP}/bin" PATH="${TEMP}/bin:${PATH}" \
  bash "${SETUP}" --prepare
[[ ! -e "${TEMP}/existing.log" ]]

# With Chromium absent, --prepare installs all standard kiosk packages before
# AP activation and verifies that the chromium executable appeared.
rm -f -- "${TEMP}/bin/chromium" "${TEMP}/bin/wtype" "${TEMP}/bin/kanshi"
KIOSK_APT_LOG="${TEMP}/missing.log" KIOSK_BIN="${TEMP}/bin" PATH="${TEMP}/bin:${PATH}" \
  bash "${SETUP}" --prepare
grep -Fq 'install -y chromium wtype kanshi' "${TEMP}/missing.log"
[[ -x "${TEMP}/bin/chromium" && -x "${TEMP}/bin/wtype" && -x "${TEMP}/bin/kanshi" ]]

echo 'PASS: kiosk prepare avoids apt when Chromium exists and installs Chromium with other kiosk dependencies when absent.'
