#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SETUP="${ROOT_DIR}/scripts/setup-wifi-access-point.sh"
COMPOSE="${ROOT_DIR}/compose.yaml"
DASHBOARD_SOCKET="${ROOT_DIR}/systemd/omk-dashboard-ap-proxy.socket.in"
MQTT_SOCKET="${ROOT_DIR}/systemd/omk-mqtt-ap-proxy.socket.in"

grep -Fq 'ipv6.method disabled' "${SETUP}"
grep -Fq 'type filter hook forward priority -100' "${SETUP}"
grep -Fq 'iifname "${INTERFACE}" oifname != "${INTERFACE}" counter drop' "${SETUP}"
if grep -Eq 'br-[0-9a-f]{12}|br-c5ef253e42eb' "${SETUP}"; then
  echo 'AP isolation must not depend on a generated Docker bridge name.' >&2
  exit 1
fi
grep -Fq '127.0.0.1:1883:1883' "${COMPOSE}"
if grep -Eq '192\.168\.50\.1:(1883|8000):|0\.0\.0\.0:(1883|8000):|"(1883|8000):(1883|8000)"' "${COMPOSE}"; then
  echo 'Docker must publish Dashboard and MQTT only on IPv4 loopback.' >&2
  exit 1
fi
grep -Fq 'systemctl reload-or-restart omk-ap-isolation.service' "${SETUP}"
grep -Fxq 'delete table inet omk_ap_isolation' "${SETUP}"
if grep -Fq 'ct status dnat counter accept' "${SETUP}"; then
  echo 'AP forwarding must not depend on Docker DNAT.' >&2
  exit 1
fi
grep -Fq 'DNSMASQ_SHARED_DIR="/etc/NetworkManager/dnsmasq-shared.d"' "${SETUP}"
grep -Fq "'no-resolv'" "${SETUP}"
grep -Fq 'install_ap_isolation_firewall' "${SETUP}"
for socket in "${DASHBOARD_SOCKET}" "${MQTT_SOCKET}"; do
  grep -Fxq 'BindToDevice=wlan0' "${socket}"
  grep -Fxq 'FreeBind=yes' "${socket}"
  grep -Fxq 'Accept=no' "${socket}"
done
grep -Fxq 'ListenStream=192.168.50.1:8000' "${DASHBOARD_SOCKET}"
grep -Fxq 'ListenStream=192.168.50.1:1883' "${MQTT_SOCKET}"

echo 'PASS: AP isolation and device-bound FreeBind socket contracts are present.'
