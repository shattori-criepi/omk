#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd -P)"
unit="$root/systemd/omk-ble-sensor-manager.service.in"
setup="$root/scripts/setup-ble-sensor-manager.sh"

grep -Fq 'Environment=OMK_NODE_REGISTRY=@OMK_ROOT@/data/ble/nodes.json' "$unit"
grep -Fq 'ReadWritePaths=@OMK_ROOT@/data/ble' "$unit"
grep -Fq 'ProtectSystem=strict' "$unit"
grep -Fq 'install -d -o "${TARGET_USER}"' "$setup"
grep -Fq 'systemctl daemon-reload' "$setup"
grep -Fq 'systemctl restart "${SERVICE_NAME}"' "$setup"
