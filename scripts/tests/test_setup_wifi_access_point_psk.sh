#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT_DIR}/scripts/setup-wifi-access-point.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT

mkdir -p "${TEMP_DIR}/scripts/lib" "${TEMP_DIR}/bin"
cp "${SETUP}" "${TEMP_DIR}/scripts/setup-wifi-access-point.sh"
cp "${ROOT_DIR}/scripts/lib/apt-helpers.sh" "${TEMP_DIR}/scripts/lib/apt-helpers.sh"
chmod +x "${TEMP_DIR}/scripts/setup-wifi-access-point.sh"

cat > "${TEMP_DIR}/bin/dpkg-query" <<'EOF'
#!/usr/bin/env bash
printf 'ii '
EOF
cat > "${TEMP_DIR}/bin/iw" <<'EOF'
#!/usr/bin/env bash
printf '  * AP\n'
EOF
for command in install systemctl nft chmod; do
  cat > "${TEMP_DIR}/bin/${command}" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
done
cat > "${TEMP_DIR}/bin/sudo" <<'EOF'
#!/usr/bin/env bash
exec "$@"
EOF
cat > "${TEMP_DIR}/bin/tee" <<'EOF'
#!/usr/bin/env bash
cat
EOF
cat > "${TEMP_DIR}/bin/nmcli" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" >> "${FAKE_NMCLI_ARGS}"
if [[ "${1:-}" == '--show-secrets' ]]; then
  [[ "${PROFILE_EXISTS}" == yes ]] && printf '%s\n' "${PROFILE_PSK}"
  exit 0
fi
if [[ "${1:-}" == general ]]; then exit 0; fi
if [[ "${1:-}" == device ]]; then exit 0; fi
if [[ "${1:-}" == -g ]]; then
  case "${2}" in
    WIFI-PROPERTIES.AP) printf 'yes\n' ;;
    connection.id) [[ "${PROFILE_EXISTS}" == yes ]] && printf 'omk-ap\n' ;;
    connection.interface-name) [[ "${PROFILE_EXISTS}" == yes ]] && printf 'wlan0\n' ;;
    connection.autoconnect) [[ "${PROFILE_EXISTS}" == yes ]] && printf 'yes\n' ;;
    802-11-wireless.mode) [[ "${PROFILE_EXISTS}" == yes ]] && printf 'ap\n' ;;
    802-11-wireless.ssid) [[ "${PROFILE_EXISTS}" == yes ]] && printf '%s\n' "${OMK_AP_SSID}" ;;
    802-11-wireless-security.key-mgmt) [[ "${PROFILE_EXISTS}" == yes ]] && printf '%s\n' "${PROFILE_KEY_MGMT:-wpa-psk}" ;;
    ipv4.method) [[ "${PROFILE_EXISTS}" == yes ]] && printf 'shared\n' ;;
    ipv4.addresses) [[ "${PROFILE_EXISTS}" == yes ]] && printf '192.168.50.1/24\n' ;;
    ipv6.method) [[ "${PROFILE_EXISTS}" == yes ]] && printf 'disabled\n' ;;
  esac
  exit 0
fi
if [[ "${1:-}" == connection && "${2:-}" == show ]]; then
  [[ "${PROFILE_EXISTS}" == yes ]] && exit 0
  exit 10
fi
if [[ "${1:-}" == connection && "${2:-}" == edit ]]; then
  read -r _setting
  read -r psk
  read -r _save
  printf '%s' "${psk}" > "${FAKE_CAPTURED_PSK}"
fi
EOF
chmod +x "${TEMP_DIR}/bin/"*

run_setup() {
  local name="$1"
  shift
  : > "${TEMP_DIR}/${name}.nmcli-args"
  : > "${TEMP_DIR}/${name}.psk"
  env PATH="${TEMP_DIR}/bin:${PATH}" \
    FAKE_NMCLI_ARGS="${TEMP_DIR}/${name}.nmcli-args" \
    FAKE_CAPTURED_PSK="${TEMP_DIR}/${name}.psk" \
    OMK_AP_CONFIRM=yes OMK_AP_SSID=OMK-TEST "$@" \
    bash "${TEMP_DIR}/scripts/setup-wifi-access-point.sh" > "${TEMP_DIR}/${name}.output" 2>&1 < /dev/null
}

# A new profile needs neither a terminal nor a supplied PSK. It is WPA2 and
# the generated secret reaches nmcli only via editor stdin.
run_setup generated PROFILE_EXISTS=no PROFILE_PSK=''
generated_psk="$(<"${TEMP_DIR}/generated.psk")"
[[ "${generated_psk}" =~ ^[A-Za-z0-9]{24}$ ]]
grep -Fq '802-11-wireless-security.key-mgmt wpa-psk' "${TEMP_DIR}/generated.nmcli-args"
! grep -Fq "${generated_psk}" "${TEMP_DIR}/generated.output"
! grep -Fq "${generated_psk}" "${TEMP_DIR}/generated.nmcli-args"
! grep -Fq 'WPA2-PSK (input is hidden)' "${TEMP_DIR}/generated.output"

# A saved secret wins over OMK_AP_PSK and causes no PSK editor invocation.
existing_psk='ExistingPskValue1234567890'
run_setup existing PROFILE_EXISTS=yes PROFILE_PSK="${existing_psk}" OMK_AP_PSK='OverridePskValue1234567890'
[[ ! -s "${TEMP_DIR}/existing.psk" ]]
! grep -Fq 'connection edit' "${TEMP_DIR}/existing.nmcli-args"

# A stored PSK remains untouched when only key management is normalized. The
# profile is made WPA2-PSK without opening the PSK editor or passing it in argv.
run_setup normalize_key_mgmt PROFILE_EXISTS=yes PROFILE_PSK="${existing_psk}" PROFILE_KEY_MGMT=none
grep -Fq '802-11-wireless-security.key-mgmt wpa-psk' "${TEMP_DIR}/normalize_key_mgmt.nmcli-args"
[[ ! -s "${TEMP_DIR}/normalize_key_mgmt.psk" ]]
! grep -Fq 'connection edit' "${TEMP_DIR}/normalize_key_mgmt.nmcli-args"
! grep -Fq "${existing_psk}" "${TEMP_DIR}/normalize_key_mgmt.output"
! grep -Fq "${existing_psk}" "${TEMP_DIR}/normalize_key_mgmt.nmcli-args"

# A malformed existing profile without a secret is repaired without prompting
# or exposing the replacement secret.
recovered_psk='RecoveredPskValue1234567890'
run_setup recovered PROFILE_EXISTS=yes PROFILE_PSK='' OMK_AP_PSK="${recovered_psk}"
[[ "$(<"${TEMP_DIR}/recovered.psk")" == "${recovered_psk}" ]]
grep -Fq 'Existing profile PSK was missing; setting one without displaying it.' "${TEMP_DIR}/recovered.output"
! grep -Fq "${recovered_psk}" "${TEMP_DIR}/recovered.output"
! grep -Fq "${recovered_psk}" "${TEMP_DIR}/recovered.nmcli-args"

# OMK_AP_PSK remains available for explicitly creating a profile, but is not
# emitted to the setup log or passed to nmcli as an argument.
explicit_psk='ExplicitPskValue1234567890'
run_setup explicit PROFILE_EXISTS=no PROFILE_PSK='' OMK_AP_PSK="${explicit_psk}"
[[ "$(<"${TEMP_DIR}/explicit.psk")" == "${explicit_psk}" ]]
! grep -Fq "${explicit_psk}" "${TEMP_DIR}/explicit.output"
! grep -Fq "${explicit_psk}" "${TEMP_DIR}/explicit.nmcli-args"

print_output="$(OMK_AP_SSID=OMK-TEST OMK_AP_PSK="${explicit_psk}" bash "${TEMP_DIR}/scripts/setup-wifi-access-point.sh" --print-config 2>&1)"
grep -Fq 'PSK: [MASKED]' <<<"${print_output}"
! grep -Fq "${explicit_psk}" <<<"${print_output}"

bash -n "${SETUP}"
echo 'PASS: AP PSKs are generated safely, preserved, and never displayed or passed in nmcli argv.'
