#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT}/scripts/setup-broute-meter.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT

# shellcheck source=/dev/null
source "${SETUP}"

OMK_ROOT="${TEMP_DIR}/omk"
SETTINGS_PATH="${OMK_ROOT}/services/broute-meter/config/settings.yaml"
SETTINGS_EXAMPLE="${ROOT}/services/broute-meter/config/settings.example.yaml"
SERIAL_BY_ID_DIRECTORY="${TEMP_DIR}/by-id"
TARGET_USER="$(id -un)"
TARGET_GROUP="$(id -gn)"
SUDO=()
mkdir -p "$(dirname -- "${SETTINGS_PATH}")" "${SERIAL_BY_ID_DIRECTORY}"

ftdi="${SERIAL_BY_ID_DIRECTORY}/usb-FTDI_FT230X_Basic_UART_DM006AOS-if00-port0"
onyx="${SERIAL_BY_ID_DIRECTORY}/usb-Quectel_EG25-G-if00-port0"
ln -s /dev/null "${ftdi}"
ln -s /dev/null "${onyx}"

# A sole generic FT230X is saved only after the operator explicitly confirms
# that this physical adapter is the RS-WSUHA-P. Quectel is never a candidate.
printf 'y\n' | initialize_settings_for_adapter
grep -Fxq "  port: \"${ftdi}\"" "${SETTINGS_PATH}"
grep -Fxq '  enabled: true' "${SETTINGS_PATH}"
grep -Fxq '  host: "127.0.0.1"' "${SETTINGS_PATH}"
if grep -Fq "${onyx}" "${SETTINGS_PATH}"; then
  echo 'Onyx serial port was selected as a B-route adapter.' >&2
  exit 1
fi

# Trust enrollment is requested only after the confirmed by-id setting exists;
# the production CLI performs the real protocol and before/after USB checks.
mkdir -p "${TEMP_DIR}/bin"
cat >"${TEMP_DIR}/bin/python" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${TRUST_CALLS}"
EOF
chmod +x "${TEMP_DIR}/bin/python"
VENV_PYTHON="${TEMP_DIR}/bin/python"
TRUST_SERVICE_ACTIVE=false
TRUST_SYSTEMCTL_CALLS=()
systemctl() {
  TRUST_SYSTEMCTL_CALLS+=("$*")
  case "$1" in
    is-active) "${TRUST_SERVICE_ACTIVE}" ;;
    stop) return 0 ;;
    *) return 0 ;;
  esac
}
TRUST_CALLS="${TEMP_DIR}/trust.calls" verify_and_trust_adapter
grep -Fxq -- '-m broute_meter setup-adapter --trust-usb-recovery' "${TEMP_DIR}/trust.calls"
[[ " ${TRUST_SYSTEMCTL_CALLS[*]} " != *" stop ${SERVICE} "* ]]

# A re-run first releases an already running worker so its serial handle does
# not compete with the administrator's protocol verification.
TRUST_SERVICE_ACTIVE=true
TRUST_SYSTEMCTL_CALLS=()
TRUST_CALLS="${TEMP_DIR}/trust.calls" verify_and_trust_adapter
[[ " ${TRUST_SYSTEMCTL_CALLS[*]} " == *" stop ${SERVICE} "* ]]

# The physical-replug wait requires disappearance before accepting the same
# stable by-id path again, and its timeout is non-destructive.
replug_step=0
sleep() {
  replug_step=$((replug_step + 1))
  case "${replug_step}" in
    1) rm -f -- "${ftdi}" ;;
    2) ln -s /dev/null "${ftdi}" ;;
  esac
}
ADAPTER_REPLUG_TIMEOUT_SECONDS=5
wait_for_adapter_replug "${ftdi}"
ADAPTER_REPLUG_TIMEOUT_SECONDS=0
if wait_for_adapter_replug "${ftdi}"; then
  echo 'Adapter replug wait accepted a path that did not disappear.' >&2
  exit 1
fi
unset -f sleep

# Existing configuration is authoritative and is never overwritten.
printf 'serial:\n  port: "/dev/serial/by-id/existing"\n' >"${SETTINGS_PATH}"
printf 'y\n' | initialize_settings_for_adapter
grep -Fxq '  port: "/dev/serial/by-id/existing"' "${SETTINGS_PATH}"

# The prior setup-generated standard MQTT block migrates only its disabled
# flag. A custom broker remains untouched.
cat >"${SETTINGS_PATH}" <<EOF
serial:
  port: "${ftdi}"
mqtt:
  enabled: false
  host: "127.0.0.1"
  port: 1883
  device_id: "broute-001"
  topic_prefix: "omk"
  client_id: "omk-broute-001"
EOF
migrate_legacy_omk_mqtt
grep -Fxq '  enabled: true' "${SETTINGS_PATH}"
grep -Fxq "  port: \"${ftdi}\"" "${SETTINGS_PATH}"
sed -i 's/127.0.0.1/mqtt.example.invalid/' "${SETTINGS_PATH}"
sed -i 's/enabled: true/enabled: false/' "${SETTINGS_PATH}"
migrate_legacy_omk_mqtt
grep -Fxq '  enabled: false' "${SETTINGS_PATH}"

# Multiple generic FT230X devices remain unselected even if input is supplied.
rm -f -- "${SETTINGS_PATH}"
ln -s /dev/null "${SERIAL_BY_ID_DIRECTORY}/usb-FTDI_FT230X_Basic_UART_other-if00-port0"
printf 'y\n' | initialize_settings_for_adapter
grep -Fxq '  port: null' "${SETTINGS_PATH}"

echo 'PASS: B-route setup creates a confirmed stable serial setting without selecting Onyx or ambiguous FT230X devices.'
