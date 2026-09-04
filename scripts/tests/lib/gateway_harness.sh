#!/usr/bin/env bash

# Shared disposable host for standard Gateway orchestration tests.  It never
# writes /etc or invokes host Docker/systemd/NetworkManager.

gateway_harness_create() {
  GATEWAY_HARNESS_ROOT="$(mktemp -d)"
  export GATEWAY_HARNESS_ROOT
  mkdir -p "${GATEWAY_HARNESS_ROOT}"/{scripts/lib,bin,state,units,etc/omk,etc,proc/device-tree,sys/class/net/wlan0,services/system-manager,services/data-transformer,services/data-exporter,services/mosquitto/config,services/mosquitto/data,data/sensors,data/latest,data/processed,data/dashboard,data/harvest-uploader,data/errors/transform,logs,kiosk/default.target.wants}
  printf 'ID=debian\nVERSION_ID=12\n' >"${GATEWAY_HARNESS_ROOT}/etc/os-release"
  printf 'Raspberry Pi 4 Model B' >"${GATEWAY_HARNESS_ROOT}/proc/device-tree/model"
  cp "${GATEWAY_HARNESS_SOURCE_ROOT}/scripts/setup-omk-gateway.sh" "${GATEWAY_HARNESS_ROOT}/scripts/"
  cp "${GATEWAY_HARNESS_SOURCE_ROOT}/scripts/lib/apt-helpers.sh" "${GATEWAY_HARNESS_ROOT}/scripts/lib/"
  touch "${GATEWAY_HARNESS_ROOT}/compose.yaml" "${GATEWAY_HARNESS_ROOT}/services/mosquitto/config/mosquitto.conf" \
    "${GATEWAY_HARNESS_ROOT}/etc/omk/dashboard-system-manager.env" \
    "${GATEWAY_HARNESS_ROOT}/units/omk-system-manager.service" "${GATEWAY_HARNESS_ROOT}/units/omk-data-transformer.service" "${GATEWAY_HARNESS_ROOT}/units/omk-data-transformer.timer"
  chmod 775 "${GATEWAY_HARNESS_ROOT}/data" "${GATEWAY_HARNESS_ROOT}/logs"
  cat >"${GATEWAY_HARNESS_ROOT}/state/owner-mode" <<'EOF'
data|omk|omk|775
logs|omk|omk|775
EOF
  export OMK_PREFLIGHT_SYSTEMD_UNIT_DIR="${GATEWAY_HARNESS_ROOT}/units"
  export OMK_PREFLIGHT_DASHBOARD_ENV_FILE="${GATEWAY_HARNESS_ROOT}/etc/omk/dashboard-system-manager.env"
  export OMK_TEST_OS_RELEASE="${GATEWAY_HARNESS_ROOT}/etc/os-release"
  export OMK_TEST_DEVICE_MODEL="${GATEWAY_HARNESS_ROOT}/proc/device-tree/model"
  export OMK_TEST_SYS_CLASS_NET="${GATEWAY_HARNESS_ROOT}/sys/class/net"
  touch "${GATEWAY_HARNESS_ROOT}/kiosk/omk-dashboard-kiosk.service"
  ln -s ../omk-dashboard-kiosk.service "${GATEWAY_HARNESS_ROOT}/kiosk/default.target.wants/omk-dashboard-kiosk.service"
  export OMK_KIOSK_UNIT_PATH="${GATEWAY_HARNESS_ROOT}/kiosk/omk-dashboard-kiosk.service"
  export OMK_KIOSK_ENABLE_LINK="${GATEWAY_HARNESS_ROOT}/kiosk/default.target.wants/omk-dashboard-kiosk.service"
  export GW_CALL_LOG="${GATEWAY_HARNESS_ROOT}/calls"
  export GW_STATE_DIR="${GATEWAY_HARNESS_ROOT}/state"
  : >"${GW_CALL_LOG}"
  gateway_harness_install_commands
  gateway_harness_install_steps
}

gateway_harness_destroy() { rm -rf -- "${GATEWAY_HARNESS_ROOT:?}"; }
gateway_harness_calls() { cat "${GW_CALL_LOG}"; }
gateway_harness_run() { PATH="${GATEWAY_HARNESS_ROOT}/bin:${PATH}" "${GATEWAY_HARNESS_ROOT}/scripts/setup-omk-gateway.sh" "$@"; }
gateway_harness_artifact_snapshot() {
  local path checksum
  for path in data logs state/site_uuid state/token state/token-env state/unit state/sudoers state/helper state/ap_psk state/network-profile \
    services/system-manager/.venv/bin/python services/data-transformer/.venv/bin/python services/data-exporter/.venv/bin/python; do
    [[ -e "${GATEWAY_HARNESS_ROOT}/${path}" ]] || continue
    checksum='directory'
    [[ -f "${GATEWAY_HARNESS_ROOT}/${path}" ]] && checksum="$(cksum <"${GATEWAY_HARNESS_ROOT}/${path}" | awk '{print $1}')"
    printf '%s|%s|%s\n' "${path}" "$(stat -c '%a' "${GATEWAY_HARNESS_ROOT}/${path}")" "${checksum}"
  done | sort
}
gateway_harness_assert_artifact_contract() {
  local path mode
  while IFS='|' read -r path mode; do
    [[ "$(stat -c '%a' "${GATEWAY_HARNESS_ROOT}/${path}")" == "${mode}" ]]
  done <<'EOF'
data|775
logs|775
state/site_uuid|600
state/token|600
state/token-env|600
state/unit|644
state/sudoers|440
state/helper|755
state/ap_psk|600
state/network-profile|600
services/system-manager/.venv/bin/python|755
services/data-transformer/.venv/bin/python|755
services/data-exporter/.venv/bin/python|755
EOF
  grep -Fxq 'data|omk|omk|775' "${GW_STATE_DIR}/owner-mode"
  grep -Fxq 'logs|omk|omk|775' "${GW_STATE_DIR}/owner-mode"
  grep -Fxq 'state/site_uuid|omk|omk|600' "${GW_STATE_DIR}/owner-mode"
  grep -Fxq 'state/token|omk|omk|600' "${GW_STATE_DIR}/owner-mode"
  grep -Fxq 'state/token-env|root|omk|600' "${GW_STATE_DIR}/owner-mode"
  grep -Fxq 'state/unit|root|root|644' "${GW_STATE_DIR}/owner-mode"
  grep -Fxq 'state/sudoers|root|root|440' "${GW_STATE_DIR}/owner-mode"
  grep -Fxq 'state/helper|root|root|755' "${GW_STATE_DIR}/owner-mode"
  grep -Fxq 'state/ap_psk|root|root|600' "${GW_STATE_DIR}/owner-mode"
  grep -Fxq 'state/network-profile|root|root|600' "${GW_STATE_DIR}/owner-mode"
}

gateway_harness_install_commands() {
  cat >"${GATEWAY_HARNESS_ROOT}/bin/gw-command" <<'EOF'
#!/usr/bin/env bash
name="$(basename "$0")"; key="${name} $*"; printf '%s\n' "$key" >>"${GW_CALL_LOG}"
counter_key="${name}"; [[ -n "${GW_FAIL_SUBCOMMAND:-}" ]] && counter_key="${name}-$(printf '%s' "${GW_FAIL_SUBCOMMAND}" | cksum | awk '{print $1}')"
counter="${GW_STATE_DIR}/count-${counter_key}"; count=0; [[ -f "$counter" ]] && count="$(<"$counter")"; count=$((count+1)); printf '%s' "$count" >"$counter"
if [[ -n "${GW_FAIL_COMMAND:-}" && "$name" == "$GW_FAIL_COMMAND" && ( -z "${GW_FAIL_SUBCOMMAND:-}" || "$*" == *"${GW_FAIL_SUBCOMMAND}"* ) ]]; then
  if [[ "${GW_FAIL_MODE:-always}" == always || ( "${GW_FAIL_MODE}" == fail-count && "$count" -le "${GW_FAIL_COUNT:-1}" ) ]]; then
    [[ "${GW_FAIL_MODE:-}" == lock ]] && { printf '%s\n' 'E: Could not get lock /var/lib/dpkg/lock-frontend' >&2; exit 100; }
    exit "${GW_FAIL_STATUS:-42}"
  fi
fi
case "$name" in
  dpkg) [[ "$*" == *'--print-architecture'* ]] && printf '%s\n' arm64 ;;
  df) printf '%s\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on' '/dev/fake 20000000 1000 12000000 1% /' ;;
  docker) if [[ "$*" == *'config --images'* ]]; then printf '%s\n' mosquitto image-collector image-dashboard image-harvest || true; elif [[ "$*" == *'ps --status'* ]]; then printf '%s\n' mosquitto sensor-collector dashboard harvest-uploader || true; fi ;;
  ip) [[ "$*" == *'route show default'* ]] && printf '%s\n' 'default via 192.0.2.1 dev wlan0' || printf '%s\n' 'inet 192.168.50.1/24' ;;
  getent) printf '%s\n' '192.0.2.2 STREAM deb.debian.org' ;;
  date) printf '%s\n' 1735689600 ;;
  iw) printf '%s\n' ' * AP' ;;
  nmcli)
    if [[ "$*" == *'--show-secrets'* ]]; then printf '%s\n' test-psk
    elif [[ "$*" == *'connection.interface-name'* ]]; then printf '%s\n' wlan0
    elif [[ "$*" == *'802-11-wireless.mode'* ]]; then printf '%s\n' ap
    elif [[ "$*" == *'connection.autoconnect'* ]]; then printf '%s\n' yes
    elif [[ "$*" == *'ipv4.method'* ]]; then printf '%s\n' shared
    elif [[ "$*" == *'ipv4.addresses'* ]]; then printf '%s\n' 192.168.50.1/24
    elif [[ "$*" == *'ipv6.method'* ]]; then printf '%s\n' disabled; fi ;;
  sudo) if [[ "${1:-}" == apt-get ]]; then shift; exec apt-get "$@"; fi ;;
  env) while [[ "${1:-}" == *=* ]]; do export "$1"; shift; done; exec "$@" ;;
  id) [[ "${1:-}" == -nG ]] && printf '%s\n' docker || printf '%s\n' omkdev ;;
  uname) if [[ "${1:-}" == -r ]]; then printf '%s\n' 6.1-rpi; else printf '%s\n' Linux; fi ;;
esac
EOF
  chmod +x "${GATEWAY_HARNESS_ROOT}/bin/gw-command"
  local stub_command
  for stub_command in apt-get dpkg docker python pip systemctl systemd-analyze visudo nmcli nft curl getent df date sudo ip id uname find iw env; do ln -s gw-command "${GATEWAY_HARNESS_ROOT}/bin/${stub_command}"; done
}

gateway_harness_install_steps() {
cat >"${GATEWAY_HARNESS_ROOT}/scripts/gw-step" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "${GW_DEBUG:-false}" == true ]] && set -x
name="$(basename "$0")"; printf '%s %s\n' "$name" "$*" >>"${GW_CALL_LOG}"
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/apt-helpers.sh"
checkpoint() {
  if [[ "${GW_CHECKPOINT:-}" == "$1" && ! -e "${GW_STATE_DIR}/checkpoint-$1" ]]; then
    touch "${GW_STATE_DIR}/checkpoint-$1"
    exit 99
  fi
}
case "$name" in
 setup-raspberry-pi.sh) omk_apt apt-get update; checkpoint apt-update; omk_apt apt-get full-upgrade -y; checkpoint os-upgrade; omk_apt apt-get install -y base; checkpoint package-install; docker info; checkpoint docker-install ;;
 setup-system-manager.sh)
   metadata() { grep -Fqx "$1|$2|$3|$4" "${GW_STATE_DIR}/owner-mode" || printf '%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" >>"${GW_STATE_DIR}/owner-mode"; }
   artifact() { local path="$1" content="$2" mode="$3" owner="$4" group="$5"; [[ ! -e "${GW_STATE_DIR}/${path}" ]] || chmod u+w "${GW_STATE_DIR}/${path}"; printf '%s\n' "${content}" >"${GW_STATE_DIR}/${path}"; chmod "${mode}" "${GW_STATE_DIR}/${path}"; metadata "state/${path}" "${owner}" "${group}" "${mode}"; }
   if [[ ! -e "${GW_STATE_DIR}/site_uuid" ]]; then artifact site_uuid site-uuid 600 omk omk; fi
   if [[ ! -e "${GW_STATE_DIR}/token" ]]; then artifact token token 600 omk omk; fi
   artifact token-env token-env 600 root omk
   mkdir -p "${GATEWAY_HARNESS_ROOT}/services/system-manager/.venv/bin"; printf '%s\n' python >"${GATEWAY_HARNESS_ROOT}/services/system-manager/.venv/bin/python"; chmod 755 "${GATEWAY_HARNESS_ROOT}/services/system-manager/.venv/bin/python"
   python -m venv system-manager; checkpoint venv; pip install -r requirements; checkpoint pip; env "PYTHONPATH=${GATEWAY_HARNESS_ROOT}/services/system-manager/src" "OMK_IMPORT_SMOKE_SRC=${GATEWAY_HARNESS_ROOT}/services/system-manager/src" "OMK_IMPORT_SMOKE_MODULE=omk_system_manager" python -c 'import importlib'
   artifact unit unit 644 root root; checkpoint unit; systemctl daemon-reload; systemctl enable service; checkpoint enable; visudo -cf rule; artifact sudoers sudoers 440 root root ;;
 setup-data-collection.sh) if [[ "${1:-}" == --prepare ]]; then docker compose config --quiet; docker compose pull mosquitto; checkpoint docker-pull; docker compose build; checkpoint docker-build; touch "${GW_STATE_DIR}/images"; else docker compose up -d; for attempt in 1 2 3; do curl --fail http://127.0.0.1:8000/health && break; [[ "$attempt" == 3 ]] && exit 1; sleep 0; done; fi ;;
 setup-data-transformer.sh) mkdir -p "${GATEWAY_HARNESS_ROOT}/services/data-transformer/.venv/bin"; printf '%s\n' python >"${GATEWAY_HARNESS_ROOT}/services/data-transformer/.venv/bin/python"; chmod 755 "${GATEWAY_HARNESS_ROOT}/services/data-transformer/.venv/bin/python"; python -m venv transformer; pip install -r requirements; env "PYTHONPATH=${GATEWAY_HARNESS_ROOT}/services/data-transformer/src" "OMK_IMPORT_SMOKE_SRC=${GATEWAY_HARNESS_ROOT}/services/data-transformer/src" "OMK_IMPORT_SMOKE_MODULE=data_transformer" python -c 'import importlib'; systemctl daemon-reload; systemctl enable timer ;;
 setup-data-exporter.sh) mkdir -p "${GATEWAY_HARNESS_ROOT}/services/data-exporter/.venv/bin"; printf '%s\n' python >"${GATEWAY_HARNESS_ROOT}/services/data-exporter/.venv/bin/python"; chmod 755 "${GATEWAY_HARNESS_ROOT}/services/data-exporter/.venv/bin/python"; python -m venv exporter; pip install -r requirements; env "PYTHONPATH=${GATEWAY_HARNESS_ROOT}/services/data-exporter/src" "OMK_IMPORT_SMOKE_SRC=${GATEWAY_HARNESS_ROOT}/services/data-exporter/src" "OMK_IMPORT_SMOKE_MODULE=data_exporter" python -c 'import importlib'; visudo -cf exporter; printf '%s\n' helper >"${GW_STATE_DIR}/helper"; chmod 755 "${GW_STATE_DIR}/helper"; grep -Fqx 'state/helper|root|root|755' "${GW_STATE_DIR}/owner-mode" || printf '%s\n' 'state/helper|root|root|755' >>"${GW_STATE_DIR}/owner-mode" ;;
 setup-wifi-access-point.sh) checkpoint pre-ap; [[ -e "${GW_STATE_DIR}/ap_psk" ]] || { printf 'psk\n' >"${GW_STATE_DIR}/ap_psk"; chmod 600 "${GW_STATE_DIR}/ap_psk"; grep -Fqx 'state/ap_psk|root|root|600' "${GW_STATE_DIR}/owner-mode" || printf '%s\n' 'state/ap_psk|root|root|600' >>"${GW_STATE_DIR}/owner-mode"; }; [[ ! -e "${GW_STATE_DIR}/network-profile" ]] || chmod u+w "${GW_STATE_DIR}/network-profile"; printf '%s\n' omk-ap >"${GW_STATE_DIR}/network-profile"; chmod 600 "${GW_STATE_DIR}/network-profile"; grep -Fqx 'state/network-profile|root|root|600' "${GW_STATE_DIR}/owner-mode" || printf '%s\n' 'state/network-profile|root|root|600' >>"${GW_STATE_DIR}/owner-mode"; [[ "${1:-}" == --activate ]] && nmcli connection up omk-ap; nft -f rules ;;
 setup-dashboard-kiosk.sh) : ;;
esac
EOF
  chmod +x "${GATEWAY_HARNESS_ROOT}/scripts/gw-step"
  for step in setup-raspberry-pi.sh setup-system-manager.sh setup-data-collection.sh setup-data-transformer.sh setup-data-exporter.sh setup-wifi-access-point.sh setup-dashboard-kiosk.sh; do ln -s gw-step "${GATEWAY_HARNESS_ROOT}/scripts/${step}"; done
}
