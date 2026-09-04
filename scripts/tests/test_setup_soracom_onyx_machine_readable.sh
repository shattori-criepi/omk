#!/usr/bin/env bash
set -euo pipefail

SCRIPT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-soracom-onyx.sh"

# Load production parsing functions without entering the host-mutating main flow.
source <(sed -n '/^mmcli_kv_value()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^modem_is_connected()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^redact_modem_identifiers()/,/^}$/p' "${SCRIPT}")

valid_kv() {
  cat <<EOF
modem.generic.state: "connected"
modem.generic.sim: /org/freedesktop/ModemManager1/SIM/0
modem.3gpp.registration-state: $1
modem.3gpp.packet-service-state: $2
EOF
}

modem_is_connected "$(valid_kv home attached)"
modem_is_connected "$(valid_kv roaming attached)"
! modem_is_connected "$(valid_kv searching attached)"
! modem_is_connected "$(valid_kv denied attached)"
! modem_is_connected "$(valid_kv home detached)"
! modem_is_connected $'modem.generic.state: connected\nmodem.generic.sim: /\nmodem.3gpp.registration-state: home\nmodem.3gpp.packet-service-state: attached'

# Human-readable tables are diagnostics only; success checks use -K fields.
grep -Fq -- '--output-keyvalue' "${SCRIPT}"
grep -Fq 'ping -I wwan0 -c 4 pong.soracom.io' "${SCRIPT}"
grep -Fq 'udevadm trigger --action=add --path' "${SCRIPT}"
grep -Fq 'nmcli -g NAME connection show --active' "${SCRIPT}"
grep -Fq 'GENERAL.DEVICES connection show soracom' "${SCRIPT}"
grep -Fq 'ping -I wwan0 -c 4 pong.soracom.io' "${SCRIPT}"
grep -Fq 'already active; preserving the current cellular connection' "${SCRIPT}"
redacted="$(printf '%s\n' 'device id: 123456' 'Numbers | own: +819012345678' '|                     own: +819012345678' 'imei: 123' 'operator name: SORACOM' 'state UNKNOWN' 'wwan0' 'UNKNOWN' | redact_modem_identifiers)"
grep -Fq 'device id: [REDACTED]' <<<"${redacted}"
grep -Fq 'own: [REDACTED]' <<<"${redacted}"
grep -Fq 'imei: [REDACTED]' <<<"${redacted}"
grep -Fq 'operator name: SORACOM' <<<"${redacted}"
grep -Fxq 'state UNKNOWN' <<<"${redacted}"
grep -Fxq 'wwan0' <<<"${redacted}"
grep -Fxq 'UNKNOWN' <<<"${redacted}"

echo 'PASS: SORACOM Onyx state validation is machine-readable and interface-bound.'
