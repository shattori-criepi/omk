#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT_DIR}/scripts/setup-wifi-access-point.sh"
TEMP_DIR="$(mktemp -d)"
trap '[[ "${KEEP_TEST_TEMP:-no}" == yes ]] || rm -rf -- "${TEMP_DIR}"' EXIT

mkdir -p "${TEMP_DIR}/scripts/lib" "${TEMP_DIR}/systemd" "${TEMP_DIR}/units" "${TEMP_DIR}/libexec" "${TEMP_DIR}/bin"
cp "${SETUP}" "${TEMP_DIR}/scripts/setup-wifi-access-point.sh"
cp "${ROOT_DIR}/scripts/omk-activate-access-point" "${TEMP_DIR}/scripts/"
cp "${ROOT_DIR}/scripts/omk-ap-proxy-dispatcher" "${TEMP_DIR}/scripts/"
cp "${ROOT_DIR}/scripts/lib/apt-helpers.sh" "${TEMP_DIR}/scripts/lib/apt-helpers.sh"
cp "${ROOT_DIR}/scripts/lib/validate-ap-socket-units.sh" "${TEMP_DIR}/scripts/lib/"
cp "${ROOT_DIR}/scripts/lib/validate-ap-socket-units.py" "${TEMP_DIR}/scripts/lib/"
cp "${ROOT_DIR}"/systemd/omk-*-ap-proxy.*.in "${TEMP_DIR}/systemd/"
cp "${ROOT_DIR}/systemd/omk-ap-activation.service.in" "${TEMP_DIR}/systemd/"
chmod +x "${TEMP_DIR}/scripts/setup-wifi-access-point.sh"

cat > "${TEMP_DIR}/bin/dpkg-query" <<'EOF'
#!/usr/bin/env bash
printf 'ii '
EOF
cat > "${TEMP_DIR}/bin/iw" <<'EOF'
#!/usr/bin/env bash
printf '  * AP\n'
EOF
for command in systemctl nft chmod; do
  cat > "${TEMP_DIR}/bin/${command}" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
done
cat > "${TEMP_DIR}/bin/install" <<'EOF'
#!/usr/bin/env bash
for argument in "$@"; do
  if [[ "${argument}" == "${OMK_SYSTEMD_UNIT_DIR}"* || "${argument}" == "${OMK_LIBEXEC_DIR}"* || "${argument}" == "${OMK_NM_DISPATCHER_DIR}"* ]]; then
    args=()
    skip=no
    for value in "$@"; do
      if [[ "${skip}" == yes ]]; then skip=no; continue; fi
      if [[ "${value}" == -o || "${value}" == -g ]]; then skip=yes; continue; fi
      args+=("${value}")
    done
    exec /usr/bin/install "${args[@]}"
  fi
done
exit 0
EOF
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
    NAME) [[ "${PROFILE_ACTIVE:-no}" == yes ]] && printf 'omk-ap\n' ;;
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
cp "${ROOT_DIR}/scripts/tests/lib/proxy-systemctl" "${TEMP_DIR}/bin/systemctl"
cp "${ROOT_DIR}/scripts/tests/lib/proxy-ss" "${TEMP_DIR}/bin/ss"
chmod +x "${TEMP_DIR}/bin/"*

run_setup() {
  local name="$1"
  shift
  : > "${TEMP_DIR}/${name}.nmcli-args"
  : > "${TEMP_DIR}/${name}.psk"
  env PATH="${TEMP_DIR}/bin:${PATH}" \
    FAKE_NMCLI_ARGS="${TEMP_DIR}/${name}.nmcli-args" \
    FAKE_CAPTURED_PSK="${TEMP_DIR}/${name}.psk" \
    OMK_SYSTEMD_UNIT_DIR="${TEMP_DIR}/units" OMK_LIBEXEC_DIR="${TEMP_DIR}/libexec" OMK_NM_DISPATCHER_DIR="${TEMP_DIR}/dispatcher" \
    OMK_AP_CONFIRM=yes OMK_AP_SSID=OMK-TEST "$@" \
    bash "${TEMP_DIR}/scripts/setup-wifi-access-point.sh" > "${TEMP_DIR}/${name}.output" 2>&1 < /dev/null
}

assert_absent() {
  local forbidden="$1" file="$2"
  if grep -Fq "${forbidden}" "${file}"; then
    printf 'Forbidden secret text found in %s.\n' "${file}" >&2
    exit 1
  fi
}

# A new profile needs neither a terminal nor a supplied PSK. It is WPA2 and
# the generated secret reaches nmcli only via editor stdin.
run_setup generated PROFILE_EXISTS=no PROFILE_PSK=''
generated_psk="$(<"${TEMP_DIR}/generated.psk")"
[[ "${generated_psk}" =~ ^[A-Za-z0-9]{24}$ ]]
[[ -x "${TEMP_DIR}/dispatcher/90-omk-ap-proxy-sockets" ]]
cmp -s "${ROOT_DIR}/scripts/omk-ap-proxy-dispatcher" "${TEMP_DIR}/dispatcher/90-omk-ap-proxy-sockets"
grep -Fq '802-11-wireless-security.key-mgmt wpa-psk' "${TEMP_DIR}/generated.nmcli-args"
assert_absent "${generated_psk}" "${TEMP_DIR}/generated.output"
assert_absent "${generated_psk}" "${TEMP_DIR}/generated.nmcli-args"
if grep -Fq 'WPA2-PSK (input is hidden)' "${TEMP_DIR}/generated.output"; then echo "Forbidden text or operation detected." >&2; exit 1; fi

# Explicit prepare overrides a hostile activation environment and creates an
# inactive/autoconnect=no profile. Any early connection-up call is a hard fail.
: >"${TEMP_DIR}/early.nmcli-args"
: >"${TEMP_DIR}/early.psk"
env PATH="${TEMP_DIR}/bin:${PATH}" FAKE_NMCLI_ARGS="${TEMP_DIR}/early.nmcli-args" \
  FAKE_CAPTURED_PSK="${TEMP_DIR}/early.psk" PROFILE_EXISTS=no PROFILE_PSK='' \
  FORBID_ACTIVATION=yes TEST_SYSTEMCTL_CALLS="${TEMP_DIR}/early.systemctl" OMK_AP_CONFIRM=yes OMK_AP_SSID=OMK-TEST OMK_AP_ACTIVATE=yes \
  OMK_SYSTEMD_UNIT_DIR="${TEMP_DIR}/units" OMK_LIBEXEC_DIR="${TEMP_DIR}/libexec" OMK_NM_DISPATCHER_DIR="${TEMP_DIR}/dispatcher" \
  bash "${TEMP_DIR}/scripts/setup-wifi-access-point.sh" --prepare >"${TEMP_DIR}/early.output" 2>&1 </dev/null
if grep -Fq 'connection up' "${TEMP_DIR}/early.nmcli-args"; then
  echo 'Prepare performed an early AP activation.' >&2
  exit 1
fi
if grep -Fq "omk-ap-activation.service" "${TEMP_DIR}/early.systemctl"; then exit 1; fi
grep -Fq 'autoconnect no' "${TEMP_DIR}/early.nmcli-args"
if grep -Eq 'autoconnect (yes|true|1)' "${TEMP_DIR}/early.nmcli-args"; then
  echo 'Prepare enabled autoconnect before handoff.' >&2; exit 1
fi

# An already active OMK AP is not cycled during a rerun and keeps autoconnect.
run_setup active PROFILE_EXISTS=yes PROFILE_ACTIVE=yes PROFILE_PSK='ExistingPskValue1234567890'
if grep -Eq 'connection (add|delete|down|up)' "${TEMP_DIR}/active.nmcli-args"; then
  echo 'Active OMK AP was cycled during preparation.' >&2
  exit 1
fi

# A saved secret wins over OMK_AP_PSK and causes no PSK editor invocation.
existing_psk='ExistingPskValue1234567890'
run_setup existing PROFILE_EXISTS=yes PROFILE_PSK="${existing_psk}" OMK_AP_PSK='OverridePskValue1234567890'
[[ ! -s "${TEMP_DIR}/existing.psk" ]]
if grep -Fq 'connection edit' "${TEMP_DIR}/existing.nmcli-args"; then echo "Forbidden text or operation detected." >&2; exit 1; fi

# A stored PSK remains untouched when only key management is normalized. The
# profile is made WPA2-PSK without opening the PSK editor or passing it in argv.
run_setup normalize_key_mgmt PROFILE_EXISTS=yes PROFILE_PSK="${existing_psk}" PROFILE_KEY_MGMT=none
grep -Fq '802-11-wireless-security.key-mgmt wpa-psk' "${TEMP_DIR}/normalize_key_mgmt.nmcli-args"
[[ ! -s "${TEMP_DIR}/normalize_key_mgmt.psk" ]]
if grep -Fq 'connection edit' "${TEMP_DIR}/normalize_key_mgmt.nmcli-args"; then echo "Forbidden text or operation detected." >&2; exit 1; fi
assert_absent "${existing_psk}" "${TEMP_DIR}/normalize_key_mgmt.output"
assert_absent "${existing_psk}" "${TEMP_DIR}/normalize_key_mgmt.nmcli-args"

# A malformed existing profile without a secret is repaired without prompting
# or exposing the replacement secret.
recovered_psk='RecoveredPskValue1234567890'
run_setup recovered PROFILE_EXISTS=yes PROFILE_PSK='' OMK_AP_PSK="${recovered_psk}"
[[ "$(<"${TEMP_DIR}/recovered.psk")" == "${recovered_psk}" ]]
grep -Fq 'Existing profile PSK was missing; setting one without displaying it.' "${TEMP_DIR}/recovered.output"
assert_absent "${recovered_psk}" "${TEMP_DIR}/recovered.output"
assert_absent "${recovered_psk}" "${TEMP_DIR}/recovered.nmcli-args"

# OMK_AP_PSK remains available for explicitly creating a profile, but is not
# emitted to the setup log or passed to nmcli as an argument.
explicit_psk='ExplicitPskValue1234567890'
run_setup explicit PROFILE_EXISTS=no PROFILE_PSK='' OMK_AP_PSK="${explicit_psk}"
[[ "$(<"${TEMP_DIR}/explicit.psk")" == "${explicit_psk}" ]]
assert_absent "${explicit_psk}" "${TEMP_DIR}/explicit.output"
assert_absent "${explicit_psk}" "${TEMP_DIR}/explicit.nmcli-args"

print_output="$(OMK_AP_SSID=OMK-TEST OMK_AP_PSK="${explicit_psk}" bash "${TEMP_DIR}/scripts/setup-wifi-access-point.sh" --print-config 2>&1)"
grep -Fq 'PSK: [MASKED]' <<<"${print_output}"
if grep -Fq "${explicit_psk}" <<<"${print_output}"; then
  echo 'Forbidden secret text found in print-config output.' >&2
  exit 1
fi

bash -n "${SETUP}"
echo 'PASS: AP PSKs are generated safely, preserved, and never displayed or passed in nmcli argv.'
