#!/usr/bin/env bash
set -euo pipefail

SCRIPT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/setup-soracom-onyx.sh"

# Load production parsing functions without entering the host-mutating main flow.
source <(sed -n '/^mmcli_kv_value()/,/^}$/p' "${SCRIPT}")
source <(sed -n '/^modem_is_connected()/,/^}$/p' "${SCRIPT}")

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

echo 'PASS: SORACOM Onyx state validation is machine-readable and interface-bound.'
