#!/usr/bin/env bash
# Start the production data collection and display containers on an OMK Pi.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OMK_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
COMPOSE_FILE="${OMK_ROOT}/compose.yaml"
TARGET_USER="${SUDO_USER:-$(id -un)}"
PRODUCTION_SERVICES=(
  mosquitto
  sensor-collector
  dashboard
  harvest-uploader
)
BUILD_SERVICES=(
  sensor-collector
  dashboard
  harvest-uploader
)
REQUIRED_DIRECTORIES=(data/sensors data/latest data/processed data/dashboard data/harvest-uploader services/mosquitto/data logs/setup)
BACKEND_BIND_ADDRESS="127.0.0.1"
MQTT_PORT="1883"
DASHBOARD_PORT="8000"
HARVEST_ENDPOINT="${HARVEST_ENDPOINT:-http://harvest.soracom.io}"
HARVEST_QUEUE_HOST_PATH="data/harvest-uploader/queue.sqlite3"
HARVEST_QUEUE_CONTAINER_PATH="/app/data/harvest-uploader/queue.sqlite3"
HARVEST_QUEUE_CONTAINER_DIR="${HARVEST_QUEUE_CONTAINER_PATH%/*}"
HARVEST_LOG_TAIL=100
DRY_RUN=false
PRINT_CONFIG=false
PULL=false
BUILD=false
RESTART=false
PREPARE=false
WITH_AP_PROXIES=false
LOG_FILE=""
DOCKER_CMD=()

usage() {
  cat <<'EOF'
Usage: scripts/setup-data-collection.sh [OPTIONS]

Safely start only OMK's production containers. B-route remains a host systemd
service.

Options:
  --dry-run       Show checks and Docker operations without changing anything.
  --print-config  Print non-secret configuration and exit.
  --pull          Pull Mosquitto; with --build, also refresh build base images.
  --build         Build local application services.
  --restart       Explicitly restart only the production services after startup.
  --with-ap-proxies Migrate backend publishes, then start prepared AP sockets.
  --prepare       Fetch and build production images without requiring the OMK AP
                  address or starting containers.
  -h, --help      Show this help.
EOF
}

log() { printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"; }
warn() { log "WARN: $*"; }
fail() { log "FAIL: $*"; exit 1; }

target_can_write() {
  local path="$1"
  if (( EUID == 0 )) && command -v runuser >/dev/null 2>&1; then
    runuser -u "${TARGET_USER}" -- test -w "${path}" -a -x "${path}"
  else
    test -w "${path}" -a -x "${path}"
  fi
}

target_can_read_directory() {
  local path="$1"
  if (( EUID == 0 )) && command -v runuser >/dev/null 2>&1; then
    runuser -u "${TARGET_USER}" -- test -r "${path}" -a -x "${path}"
  else
    test -r "${path}" -a -x "${path}"
  fi
}

check_directory() {
  local relative="$1" path="${OMK_ROOT}/${1}" ownership
  if [[ -e "${path}" && ! -d "${path}" ]]; then
    fail "Required directory path is not a directory: ${path}"
  fi
  if [[ ! -d "${path}" ]]; then
    if "${DRY_RUN}"; then
      log "Would create required directory: ${path} (owner ${TARGET_USER})"
      return
    fi
    mkdir -p "${path}" || fail "Cannot create required directory: ${path}"
    if (( EUID == 0 )); then
      chown "${TARGET_USER}:$(id -gn "${TARGET_USER}")" "${path}"
    fi
    log "Created required directory: ${path} (owner ${TARGET_USER})"
  fi
  ownership="$(stat -c 'owner=%U:%G uid=%u gid=%g mode=%a' "${path}")"
  log "Directory state: ${path} (${ownership})"
  case "${relative}" in
    data/sensors|data/latest|data/dashboard|data/harvest-uploader|logs/setup)
      target_can_write "${path}" || fail "${TARGET_USER} cannot write required directory: ${path}. Review ownership and permissions without using chown -R."
      if [[ "${relative}" == "data/sensors" || "${relative}" == "data/latest" || "${relative}" == "data/dashboard" || "${relative}" == "data/harvest-uploader" ]]; then
        target_can_read_directory "${path}" || fail "${TARGET_USER} cannot read/manage required directory: ${path}. Review ownership and permissions without using chown -R."
      fi
      log "PASS: ${TARGET_USER} can write ${path}"
      ;;
    data/processed)
      target_can_read_directory "${path}" || fail "${TARGET_USER} cannot read/manage required directory: ${path}. Review ownership and permissions without using chown -R."
      log "PASS: ${TARGET_USER} can read/manage ${path}; host write access is not required"
      ;;
    services/mosquitto/data)
      log "INFO: Host-user write access is not required for ${path}; container mount access is checked after startup."
      ;;
  esac
}

docker_compose() { "${DOCKER_CMD[@]}" compose -f "${COMPOSE_FILE}" "$@"; }

print_config() {
  local uid gid directory
  uid="$(id -u "${TARGET_USER}")"; gid="$(id -g "${TARGET_USER}")"
  printf '%s\n' "Repository root: ${OMK_ROOT}" "Target user: ${TARGET_USER}" "UID/GID: ${uid}/${gid}" \
    "Compose file: ${COMPOSE_FILE}" "Production services: ${PRODUCTION_SERVICES[*]}" \
    "Build services: ${BUILD_SERVICES[*]}" \
    "Backend bind address: ${BACKEND_BIND_ADDRESS}" "MQTT port: ${MQTT_PORT}" \
    "Dashboard port: ${DASHBOARD_PORT}" "Harvest endpoint: ${HARVEST_ENDPOINT}" \
    "Harvest queue host path: ${HARVEST_QUEUE_HOST_PATH}" "Harvest queue container path: ${HARVEST_QUEUE_CONTAINER_PATH}" \
    "dry-run=${DRY_RUN} pull=${PULL} build=${BUILD} restart=${RESTART} prepare=${PREPARE}"
  printf 'Required directories:\n'
  for directory in "${REQUIRED_DIRECTORIES[@]}"; do printf '  %s\n' "${OMK_ROOT}/${directory}"; done
}

validate_static_files() {
  [[ "$(uname -s)" == Linux ]] || fail "This script supports Linux only."
  id "${TARGET_USER}" >/dev/null 2>&1 || fail "Target user does not exist: ${TARGET_USER}"
  [[ -f "${COMPOSE_FILE}" ]] || fail "Compose file is missing: ${COMPOSE_FILE}"
  [[ -f "${OMK_ROOT}/services/mosquitto/config/mosquitto.conf" ]] || fail "Mosquitto configuration is missing."
  [[ -f "${OMK_ROOT}/services/sensor-collector/Dockerfile" ]] || fail "sensor-collector Dockerfile is missing."
  [[ -f "${OMK_ROOT}/services/dashboard/Dockerfile" ]] || fail "dashboard Dockerfile is missing."
  [[ -f "${OMK_ROOT}/services/harvest-uploader/Dockerfile" ]] || fail "harvest-uploader Dockerfile is missing."
  [[ -f "${OMK_ROOT}/services/harvest-uploader/requirements.txt" ]] || fail "harvest-uploader requirements.txt is missing."
  [[ -f "${OMK_ROOT}/services/harvest-uploader/src/harvest_uploader/__main__.py" ]] || fail "harvest-uploader entry point is missing."
  for service in "${PRODUCTION_SERVICES[@]}"; do
    grep -q "^  ${service}:" "${COMPOSE_FILE}" || fail "Production service is absent from compose.yaml: ${service}"
  done
  [[ -x "${OMK_ROOT}/scripts/lib/validate-compose-publishes.py" ]] || fail "Compose publish validator is missing or not executable."
}

configure_docker_command() {
  command -v docker >/dev/null 2>&1 || fail "docker command was not found. Run scripts/setup-raspberry-pi.sh first."
  if (( EUID == 0 )); then
    if command -v runuser >/dev/null 2>&1; then
      DOCKER_CMD=(runuser -u "${TARGET_USER}" -- docker)
    else
      fail "Root execution requires runuser so Docker is checked as ${TARGET_USER}."
    fi
  else
    DOCKER_CMD=(docker)
  fi
  docker_compose version >/dev/null 2>&1 || fail "Docker Compose plugin is unavailable for ${TARGET_USER}."
  "${DOCKER_CMD[@]}" info >/dev/null 2>&1 || fail "Cannot connect to Docker as ${TARGET_USER}. Check: systemctl status docker; re-login after docker group changes; do not rely on sudo docker."
}

validate_compose_publishes() {
  docker_compose config --format json | "${OMK_ROOT}/scripts/lib/validate-compose-publishes.py" ||
    fail 'Dashboard/MQTT Docker publishing does not satisfy the IPv4 loopback-only security boundary.'
}

container_status() {
  local service="$1" name state policy
  name="$(docker_compose ps -q "${service}")"
  [[ -n "${name}" ]] || { log "FAIL: ${service} container does not exist"; return 1; }
  state="$("${DOCKER_CMD[@]}" inspect -f '{{.State.Running}}' "${name}")"
  policy="$("${DOCKER_CMD[@]}" inspect -f '{{.HostConfig.RestartPolicy.Name}}' "${name}")"
  [[ "${state}" == true ]] && log "PASS: ${service} is running" || { log "FAIL: ${service} is not running"; return 1; }
  [[ "${policy}" == unless-stopped ]] && log "PASS: ${service} restart policy is unless-stopped" || warn "${service} restart policy is ${policy}, expected unless-stopped"
}

check_dashboard() {
  local attempt
  for attempt in 1 2 3 4 5; do
    if curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:${DASHBOARD_PORT}/health" >/dev/null; then
      log "PASS: Dashboard health endpoint responded"
      curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:${DASHBOARD_PORT}/display" -o /dev/null
      log "PASS: Dashboard display endpoint is reachable"
      curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:${DASHBOARD_PORT}/api/display" -o /dev/null
      log "PASS: Dashboard display API is reachable"
      return 0
    fi
    sleep 2
  done
  docker_compose logs --tail 30 dashboard || true
  fail "Dashboard health endpoint did not respond."
}

check_mqtt_listener() {
  local attempt
  for attempt in 1 2 3 4 5; do
    if python3 "${SCRIPT_DIR}/lib/check-mqtt-backend.py"; then
      log "PASS: Mosquitto loopback listener is present at ${BACKEND_BIND_ADDRESS}:${MQTT_PORT}"
      return 0
    fi
    sleep 2
  done
  docker_compose logs --tail 30 mosquitto || true
  fail "Mosquitto loopback listener did not appear at ${BACKEND_BIND_ADDRESS}:${MQTT_PORT}."
}

check_container_paths() {
  local dashboard_id latest_read_only
  docker_compose exec -T sensor-collector sh -c 'test -w /app/data/sensors' ||
    fail "sensor-collector cannot write /app/data/sensors."
  log "PASS: sensor-collector can write its data mount"
  docker_compose exec -T sensor-collector sh -c 'test -w /app/data/latest' ||
    fail "sensor-collector cannot write /app/data/latest."
  log "PASS: sensor-collector can write its latest-data mount"
  docker_compose exec -T dashboard sh -c 'test -r /app/data/processed' ||
    fail "dashboard cannot read /app/data/processed."
  log "PASS: dashboard can read its processed-data mount"
  docker_compose exec -T dashboard sh -c 'test -r /app/data/latest' ||
    fail "dashboard cannot read /app/data/latest."
  log "PASS: dashboard can read its latest-data mount"
  dashboard_id="$(docker_compose ps -q dashboard)"
  latest_read_only="$("${DOCKER_CMD[@]}" inspect -f '{{range .Mounts}}{{if eq .Destination "/app/data/latest"}}{{.RW}}{{end}}{{end}}' "${dashboard_id}")"
  [[ "${latest_read_only}" == false ]] || fail "dashboard latest-data mount is not read-only."
  log "PASS: dashboard latest-data mount is read-only"
  docker_compose exec -T mosquitto sh -c 'test -w /mosquitto/data' ||
    fail "mosquitto cannot write /mosquitto/data. Preserve existing container ownership; inspect its UID with 'docker compose exec mosquitto id' and adjust only this directory if required (never chown -R)."
  log "PASS: mosquitto can use its data mount"
  check_harvest_queue_mount
}

check_harvest_queue_mount() {
  docker_compose exec -T harvest-uploader sh -c "test -w ${HARVEST_QUEUE_CONTAINER_DIR}" ||
    fail "harvest-uploader cannot write ${HARVEST_QUEUE_CONTAINER_DIR}. Review only data/harvest-uploader ownership and permissions; never use chown -R."
  log "PASS: harvest-uploader can write its queue-data mount"
  docker_compose exec -T harvest-uploader sh -c "if test -e ${HARVEST_QUEUE_CONTAINER_PATH}; then test -r ${HARVEST_QUEUE_CONTAINER_PATH} && test -w ${HARVEST_QUEUE_CONTAINER_PATH}; fi" ||
    fail "harvest-uploader cannot read/write its existing queue.sqlite3. Review only that file and its parent directory permissions."
  log "PASS: harvest-uploader queue.sqlite3 is writable when present"
}

check_harvest_mqtt_evidence() {
  local recent_logs="$1"
  if grep -Fq 'MQTT connected; subscribed to omk/#' <<<"${recent_logs}"; then
    log "PASS: harvest-uploader MQTT connection is confirmed"
  elif grep -Fq 'Harvest send succeeded' <<<"${recent_logs}" ||
    grep -Fq 'Harvest send failed; queued for retry' <<<"${recent_logs}"; then
    log "PASS: harvest-uploader has processed MQTT data"
  elif grep -Eqi 'MQTT connection refused|MQTT disconnected|connection error|connection refused' <<<"${recent_logs}"; then
    warn "harvest-uploader has recent MQTT connection errors; it remains running and may reconnect."
  else
    log "INFO: harvest-uploader MQTT connection evidence is not visible in recent logs"
  fi
}

check_harvest_uploader() {
  local queue_path="${OMK_ROOT}/${HARVEST_QUEUE_HOST_PATH}" recent_logs
  if [[ -e "${queue_path}" ]]; then
    [[ -f "${queue_path}" ]] || fail "Harvest queue path is not a regular file: ${queue_path}"
    log "INFO: Harvest queue state: $(stat -c 'exists=yes size=%s uid=%u gid=%g mode=%a' "${queue_path}")"
  else
    log "INFO: Harvest queue has not been created yet: ${HARVEST_QUEUE_HOST_PATH}"
  fi

  recent_logs="$(docker_compose logs --tail "${HARVEST_LOG_TAIL}" harvest-uploader 2>&1 || true)"
  check_harvest_mqtt_evidence "${recent_logs}"
  if grep -Fq 'Harvest send failed; queued for retry' <<<"${recent_logs}"; then
    warn "Harvest delivery has failed recently; records are queued for retry."
  elif grep -Fq 'Harvest send succeeded' <<<"${recent_logs}"; then
    log "PASS: harvest-uploader has recent successful Harvest delivery"
  else
    log "INFO: No Harvest delivery result is visible; there may be no completed one-minute record yet."
  fi
}

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=true ;; --print-config) PRINT_CONFIG=true ;; --pull) PULL=true ;;
    --with-ap-proxies) WITH_AP_PROXIES=true ;;
    --build) BUILD=true ;; --restart) RESTART=true ;; --prepare) PREPARE=true; PULL=true; BUILD=true ;; -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

validate_static_files
if "${PRINT_CONFIG}"; then print_config; exit 0; fi

# A dry run intentionally does not create its usual log file or any directories.
if ! "${DRY_RUN}"; then
  check_directory logs/setup
  LOG_FILE="${OMK_ROOT}/logs/setup/setup-data-collection-$(date '+%Y%m%d-%H%M%S').log"
  exec > >(tee -a "${LOG_FILE}") 2>&1
  log "Log file: ${LOG_FILE}"
fi
log "Production targets: ${PRODUCTION_SERVICES[*]}"
print_config

if "${DRY_RUN}"; then
  for directory in "${REQUIRED_DIRECTORIES[@]}"; do check_directory "${directory}"; done
  if command -v curl >/dev/null 2>&1; then
    log "PASS: curl is available for the planned Dashboard health check"
  else
    warn "curl is unavailable; a real run will fail before the Dashboard health check."
  fi
  if command -v docker >/dev/null 2>&1; then
    log "Docker command is present; Compose and daemon access would be checked on a real run."
  else
    warn "docker is unavailable; this is permitted for a dry run only."
  fi
  if "${PREPARE}"; then
    log "Would check Docker, validate loopback-only Compose publishes, pull Mosquitto, and build ${BUILD_SERVICES[*]} without requiring the OMK AP address or starting containers."
  else
    log "Would check Docker, validate loopback-only Compose publishes, and run: docker compose up -d ${PRODUCTION_SERVICES[*]}"
  fi
  "${PULL}" && log "Would run: docker compose pull mosquitto"
  if "${BUILD}"; then
    if "${PULL}"; then
      log "Would run: docker compose build --pull ${BUILD_SERVICES[*]}"
    else
      log "Would run: docker compose build ${BUILD_SERVICES[*]}"
    fi
  fi
  "${RESTART}" && log "Would run: docker compose restart ${PRODUCTION_SERVICES[*]}"
  if ! "${PREPARE}"; then
    log "Would verify container mounts after startup: sensor-collector write /app/data/sensors and /app/data/latest; dashboard read /app/data/processed and read-only /app/data/latest; mosquitto write /mosquitto/data; harvest-uploader write /app/data/harvest-uploader."
    log "Would inspect harvest-uploader MQTT and Harvest retry logs, and queue.sqlite3 metadata without reading payloads."
    log "Would check ${BACKEND_BIND_ADDRESS}:${MQTT_PORT}, http://127.0.0.1:${DASHBOARD_PORT}/health, /display, and /api/display."
  fi
  exit 0
fi

configure_docker_command
docker_compose config --quiet || fail "Compose configuration validation failed."
validate_compose_publishes
for directory in "${REQUIRED_DIRECTORIES[@]}"; do check_directory "${directory}"; done
if "${PREPARE}"; then
  docker_compose pull mosquitto
  docker_compose build --pull "${BUILD_SERVICES[@]}"
  log "SUCCESS: production images are prepared; containers can start on loopback before OMK AP activation."
  exit 0
fi
command -v curl >/dev/null 2>&1 || fail "curl is required for Dashboard health checks."
command -v ss >/dev/null 2>&1 || fail "ss is required to verify the Mosquitto listener."
"${PULL}" && docker_compose pull mosquitto
if "${BUILD}"; then
  if "${PULL}"; then
    docker_compose build --pull "${BUILD_SERVICES[@]}"
  else
    docker_compose build "${BUILD_SERVICES[@]}"
  fi
fi
if "${WITH_AP_PROXIES}"; then
  source "${SCRIPT_DIR}/lib/compose-ap-migration.sh"
  PORT_ROLLBACK='' IMAGE_ROLLBACK='' FIREWALL_ROLLBACK='' MIGRATION_STARTED=no
  trap 'status=$?; finish_publish_migration "${status}"' EXIT
  trap 'exit 143' TERM HUP
  trap 'exit 130' INT
  prepare_publish_rollback
  MIGRATION_STARTED=yes
fi
docker_compose up -d "${PRODUCTION_SERVICES[@]}"
"${RESTART}" && docker_compose restart "${PRODUCTION_SERVICES[@]}"
for service in "${PRODUCTION_SERVICES[@]}"; do container_status "${service}"; done
check_container_paths
check_harvest_uploader
check_mqtt_listener
docker_compose logs --tail 30 mosquitto | grep -Eqi 'fatal|error' && warn "Mosquitto logs contain error text; review the log." || log "PASS: No fatal/error text in recent Mosquitto logs"
docker_compose logs --tail 30 sensor-collector | grep -Eqi 'connection refused|connection error' && warn "sensor-collector shows MQTT connection errors; it may recover when Mosquitto is ready." || log "PASS: No persistent MQTT connection text in recent collector logs"
check_dashboard
if "${WITH_AP_PROXIES}"; then
  "${SCRIPT_DIR}/setup-wifi-access-point.sh" --start-proxies
  # Final isolation replacement is the last migration mutation.
  rm -f -- "${PORT_ROLLBACK}" "${IMAGE_ROLLBACK}" "${FIREWALL_ROLLBACK}"
  trap - EXIT TERM INT HUP
fi
log "SUCCESS: running omk-mosquitto, omk-sensor-collector, omk-dashboard, omk-harvest-uploader"
log "MQTT backend: ${BACKEND_BIND_ADDRESS}:${MQTT_PORT}; Dashboard: http://localhost:${DASHBOARD_PORT}/display; Harvest: ${HARVEST_ENDPOINT}"
log "Data: data/sensors (JSONL), data/latest (live cache), data/processed (processed data), ${HARVEST_QUEUE_HOST_PATH} (Harvest retry queue)"
log "Status: docker compose ps ${PRODUCTION_SERVICES[*]}"
log "Logs: docker compose logs --tail 100 ${PRODUCTION_SERVICES[*]}"
log "Harvest logs: docker compose logs --tail 100 harvest-uploader"
log "Stop only these services: docker compose stop ${PRODUCTION_SERVICES[*]}"
log "Restart only these services: docker compose restart ${PRODUCTION_SERVICES[*]}"
