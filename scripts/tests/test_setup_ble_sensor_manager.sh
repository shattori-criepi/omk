#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd -P)"
unit="$root/systemd/omk-ble-sensor-manager.service.in"
setup="$root/scripts/setup-ble-sensor-manager.sh"

grep -Fq 'Environment=OMK_NODE_REGISTRY=@OMK_ROOT@/data/ble/nodes.json' "$unit"
grep -Fq 'Environment=OMK_NODE_CREDENTIAL_DIRECTORY=@OMK_ROOT@/data/provisioning/nodes' "$unit"
grep -Fq 'ReadWritePaths=@OMK_ROOT@/data/ble' "$unit"
grep -Fq 'ProtectSystem=strict' "$unit"
grep -Fq 'install -d -o "${TARGET_USER}"' "$setup"
grep -Fq 'systemctl daemon-reload' "$setup"
grep -Fq 'systemctl restart "${SERVICE_NAME}"' "$setup"
grep -Fq 'ESP_IDF_VERSION=6.0.1' "$setup"
grep -Fq 'NETWORK_PROVISIONING_VERSION=1.2.4' "$setup"
grep -Fq 'github.com/espressif/idf-extra-components/archive/${NETWORK_PROVISIONING_COMMIT}.tar.gz' "$setup"
grep -Fq 'github.com/espressif/esp-idf/archive/refs/tags/v${ESP_IDF_VERSION}.tar.gz' "$setup"
! grep -Eq 'refs/(heads/)?(main|master)|/latest' "$setup"

# Component Manager emits the fixed component version as a quoted YAML scalar.
# The setup validation must accept that form without weakening version checks.
quoted_manifest="$(mktemp)"
trap 'rm -f "$quoted_manifest"' EXIT
printf '  version: "1.2.4"\n' > "$quoted_manifest"
awk -v expected='1.2.4' '
  /^[[:space:]]*version:[[:space:]]*/ {
    value = $0
    sub(/^[[:space:]]*version:[[:space:]]*/, "", value)
    sub(/[[:space:]]*#.*/, "", value)
    gsub(/^[[:space:]]+/, "", value)
    gsub(/[[:space:]]+$/, "", value)
    single_quote = sprintf("%c", 39)
    first = substr(value, 1, 1)
    last = substr(value, length(value), 1)
    if ((first == "\"" && last == "\"") || (first == single_quote && last == single_quote)) value = substr(value, 2, length(value) - 2)
    if (value == expected) found = 1
    exit
  }
  END { exit(found ? 0 : 1) }
' "$quoted_manifest"
