#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
VALIDATOR="${ROOT_DIR}/scripts/lib/validate-compose-publishes.py"

valid='{"services":{"mosquitto":{"ports":[{"target":1883,"published":"1883","host_ip":"127.0.0.1","protocol":"tcp"}]},"dashboard":{"ports":[{"target":8000,"published":"8000","host_ip":"127.0.0.1","protocol":"tcp"}]}}}'
printf '%s\n' "${valid}" | "${VALIDATOR}" >/dev/null

assert_rejected() {
  local name="$1" json="$2"
  if printf '%s\n' "${json}" | "${VALIDATOR}" >"/tmp/omk-compose-${name}.log" 2>&1; then
    printf 'Validator accepted unsafe %s publishing.\n' "${name}" >&2
    exit 1
  fi
  rm -f -- "/tmp/omk-compose-${name}.log"
}

assert_rejected wildcard "${valid/127.0.0.1/0.0.0.0}"
assert_rejected ap_address "${valid/127.0.0.1/192.168.50.1}"
assert_rejected missing_host_ip "${valid/,\"host_ip\":\"127.0.0.1\"/}"

echo 'PASS: semantic Compose validation rejects wildcard and AP-address Docker publishes.'
