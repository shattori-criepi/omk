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
VENV_PYTHON="$(command -v python3)"
mkdir -p "$(dirname -- "${SETTINGS_PATH}")" "${SERIAL_BY_ID_DIRECTORY}"
printf 'test-only credential sentinel\n' >"$(dirname -- "${SETTINGS_PATH}")/credentials.yaml"
cp "$(dirname -- "${SETTINGS_PATH}")/credentials.yaml" "${TEMP_DIR}/credentials.before"

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

# Declining a fresh setup creates an unconfigured file. A confirmed re-run
# changes only serial.port, preserving custom MQTT and measurement settings.
rm -- "${SETTINGS_PATH}"
initialize_settings_for_adapter </dev/null
grep -Fxq '  port: null' "${SETTINGS_PATH}"
sed -i 's/127.0.0.1/mqtt.example.invalid/; s/instantaneous_interval_seconds: 10/instantaneous_interval_seconds: 20/' "${SETTINGS_PATH}"
cp "${SETTINGS_PATH}" "${TEMP_DIR}/settings.before"
printf 'N\n' | initialize_settings_for_adapter
cmp "${TEMP_DIR}/settings.before" "${SETTINGS_PATH}"
printf 'y\n' | initialize_settings_for_adapter
python3 - "${TEMP_DIR}/settings.before" "${SETTINGS_PATH}" "${ftdi}" <<'PY'
import sys
import yaml
with open(sys.argv[1]) as stream:
    expected = yaml.safe_load(stream)
with open(sys.argv[2]) as stream:
    actual = yaml.safe_load(stream)
expected['serial']['port'] = sys.argv[3]
assert actual == expected
PY
[[ "$(stat -c '%a' "${SETTINGS_PATH}")" == 600 ]]
cp "${SETTINGS_PATH}" "${TEMP_DIR}/configured.before"
initialize_settings_for_adapter </dev/null
cmp "${TEMP_DIR}/configured.before" "${SETTINGS_PATH}"

# MQTT's port must never be mistaken for an absent serial.port, regardless of
# section order. YAML null/empty and missing serial settings can all recover.
for serial in 'serial: {port: null, baudrate: 9600}' 'serial: {port: ~}' 'serial: {port: ""}' 'serial: {}' 'serial: null' ''; do
  printf 'mqtt: {port: 1883, enabled: true}\n%s\n' "${serial}" >"${SETTINGS_PATH}"
  printf 'y\n' | initialize_settings_for_adapter
  [[ "$(configured_serial_port)" == "${ftdi}" ]]
done

# No matching adapter or multiple matches keep an existing file byte-for-byte.
rm -- "${ftdi}" "${SETTINGS_PATH}"
initialize_settings_for_adapter </dev/null
grep -Fxq '  port: null' "${SETTINGS_PATH}"
cp "${SETTINGS_PATH}" "${TEMP_DIR}/unconfigured.before"
initialize_settings_for_adapter </dev/null
cmp "${TEMP_DIR}/unconfigured.before" "${SETTINGS_PATH}"
ln -s /dev/null "${ftdi}"
other="${SERIAL_BY_ID_DIRECTORY}/usb-FTDI_FT230X_Basic_UART_other-if00-port0"
ln -s /dev/null "${other}"
printf 'y\n' | initialize_settings_for_adapter
cmp "${TEMP_DIR}/unconfigured.before" "${SETTINGS_PATH}"
rm -- "${other}"

# Malformed configuration is never replaced with the default template.
printf 'serial: [\n' >"${SETTINGS_PATH}"
cp "${SETTINGS_PATH}" "${TEMP_DIR}/invalid.before"
if (initialize_settings_for_adapter <<<'y'); then
  echo 'Invalid existing settings were accepted.' >&2
  exit 1
fi
cmp "${TEMP_DIR}/invalid.before" "${SETTINGS_PATH}"
cp "${TEMP_DIR}/unconfigured.before" "${SETTINGS_PATH}"
printf 'y\n' | initialize_settings_for_adapter

# Trust enrollment is requested only after the confirmed by-id setting exists;
# the production CLI performs the real protocol and before/after USB checks.
mkdir -p "${TEMP_DIR}/bin"
cat >"${TEMP_DIR}/bin/python" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == - ]]; then exec python3 "$@"; fi
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
cmp "${TEMP_DIR}/credentials.before" "$(dirname -- "${SETTINGS_PATH}")/credentials.yaml"

echo 'PASS: B-route setup creates a confirmed stable serial setting without selecting Onyx or ambiguous FT230X devices.'
