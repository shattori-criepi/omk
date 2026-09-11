#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT}/scripts/setup-data-exporter.sh"
HELPER="${ROOT}/scripts/omk-export-usb-helper"

# The helper accepts only the two fixed operations and obtains both the USB
# candidate and fixed mount point itself; callers cannot pass a device/path.
grep -Fq 'len(sys.argv) != 3' "${HELPER}"
grep -Fq 'MOUNT_POINT = "/run/omk-export-usb"' "${HELPER}"
grep -Fq 'HOST_MOUNT_NAMESPACE = "/proc/1/ns/mnt"' "${HELPER}"
grep -Fq 'def host_run(command, **kwargs):' "${HELPER}"
grep -Fq 'transport == "usb" and filesystem in {"vfat", "exfat"}' "${HELPER}"
grep -Fq 'NAME,PATH,TYPE,TRAN,FSTYPE,MOUNTPOINT,SERIAL,WWN,UUID,PARTUUID' "${HELPER}"
grep -Fq 'identity_token(serial, wwn, node.get("uuid"), node.get("partuuid"))' "${HELPER}"
grep -Fq 'host_run(["systemd-umount", device["path"]]' "${HELPER}"
grep -Fq 'NOPASSWD: %s ^(mount|unmount) [0-9a-f]{64}$' "${SETUP}"
grep -Fq 'install_cli_launcher omk-export-usb' "${SETUP}"
grep -Fq 'command -v nsenter' "${SETUP}"

temporary="$(mktemp)"
trap 'rm -f -- "${temporary}"' EXIT
sed -e 's|@TARGET_UID@|1000|g' -e 's|@TARGET_GID@|1000|g' -e 's|@NSENTER@|/usr/bin/nsenter|g' "${HELPER}" >"${temporary}"
python3 -m py_compile "${temporary}"
grep -Fq 'NSENTER = "/usr/bin/nsenter"' "${temporary}"
grep -Fq 'HOST_MOUNT_NAMESPACE = "/proc/1/ns/mnt"' "${temporary}"
echo 'PASS: USB helper has fixed actions, host namespace, fixed mount point, and narrow sudoers contract.'
