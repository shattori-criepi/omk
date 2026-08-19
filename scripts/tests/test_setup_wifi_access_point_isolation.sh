#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT_DIR}/scripts/setup-wifi-access-point.sh"
COMPOSE="${ROOT_DIR}/compose.yaml"

grep -Fq 'ipv6.method disabled' "${SETUP}"
grep -Fq 'type filter hook forward priority -100' "${SETUP}"
grep -Fq 'iifname "${INTERFACE}" ct status dnat counter accept' "${SETUP}"
grep -Fq 'iifname "${INTERFACE}" oifname != "${INTERFACE}" counter drop' "${SETUP}"
dnat_line="$(grep -n -F 'iifname "${INTERFACE}" ct status dnat counter accept' "${SETUP}" | head -n 1 | cut -d: -f1)"
drop_line="$(grep -n -F 'iifname "${INTERFACE}" oifname != "${INTERFACE}" counter drop' "${SETUP}" | head -n 1 | cut -d: -f1)"
[[ "${dnat_line}" -lt "${drop_line}" ]]
if grep -Eq 'br-[0-9a-f]{12}|br-c5ef253e42eb' "${SETUP}"; then
  echo 'AP isolation must not depend on a generated Docker bridge name.' >&2
  exit 1
fi
grep -Fq '192.168.50.1:1883:1883' "${COMPOSE}"
grep -Fq 'systemctl restart omk-ap-isolation.service' "${SETUP}"
grep -Fq 'ExecStartPre=-/usr/sbin/nft delete table inet omk_ap_isolation' "${SETUP}"
[[ "$(grep -F -c 'ct status dnat counter accept' "${SETUP}")" -eq 1 ]]
grep -Fq 'DNSMASQ_SHARED_DIR="/etc/NetworkManager/dnsmasq-shared.d"' "${SETUP}"
grep -Fq "'no-resolv'" "${SETUP}"
grep -Fq 'Restarting the active AP so its DHCP/DNS isolation configuration is reloaded.' "${SETUP}"
grep -Fq 'install_ap_isolation_firewall' "${SETUP}"

echo 'PASS: AP isolation allows Docker DNAT-published ports before blocking all external forwarding.'
