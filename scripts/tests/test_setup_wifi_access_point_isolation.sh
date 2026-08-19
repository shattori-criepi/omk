#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT_DIR}/scripts/setup-wifi-access-point.sh"

grep -Fq 'ipv6.method disabled' "${SETUP}"
grep -Fq 'type filter hook forward priority -100' "${SETUP}"
grep -Fq 'iifname "${INTERFACE}" oifname != "${INTERFACE}" counter drop' "${SETUP}"
grep -Fq 'systemctl enable --now omk-ap-isolation.service' "${SETUP}"
grep -Fq 'DNSMASQ_SHARED_DIR="/etc/NetworkManager/dnsmasq-shared.d"' "${SETUP}"
grep -Fq "'no-resolv'" "${SETUP}"
grep -Fq 'Restarting the active AP so its DHCP/DNS isolation configuration is reloaded.' "${SETUP}"
grep -Fq 'install_ap_isolation_firewall' "${SETUP}"

echo 'PASS: Wi-Fi AP setup disables AP IPv6 and installs routed-client isolation.'
