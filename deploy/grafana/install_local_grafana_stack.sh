#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PUBLIC_HOST=""
GRAFANA_ADMIN_USER="admin"
GRAFANA_ADMIN_PASSWORD=""
GRAFANA_HTTP_PORT="3000"
PROCESS_EXPORTER_VERSION="${PROCESS_EXPORTER_VERSION:-0.8.7}"

usage() {
  cat <<'EOF'
Usage:
  sudo ./deploy/grafana/install_local_grafana_stack.sh \
    --public-host grafana.example.com \
    --grafana-http-port 3300 \
    --grafana-admin-password '<strong-password>'

Options:
  --public-host               Public HTTP host served by nginx.
  --grafana-http-port         Local Grafana listen port (default: 3000).
  --grafana-admin-user        Grafana admin username (default: admin).
  --grafana-admin-password    Grafana admin password, required.

Environment overrides:
  PROCESS_EXPORTER_VERSION    Default: 0.8.7
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --public-host)
      PUBLIC_HOST="${2:-}"
      shift 2
      ;;
    --grafana-http-port)
      GRAFANA_HTTP_PORT="${2:-}"
      shift 2
      ;;
    --grafana-admin-user)
      GRAFANA_ADMIN_USER="${2:-}"
      shift 2
      ;;
    --grafana-admin-password)
      GRAFANA_ADMIN_PASSWORD="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

if [[ -z "${PUBLIC_HOST}" || -z "${GRAFANA_ADMIN_PASSWORD}" ]]; then
  echo "--public-host and --grafana-admin-password are required." >&2
  usage >&2
  exit 1
fi

if [[ ! "${GRAFANA_HTTP_PORT}" =~ ^[0-9]+$ ]]; then
  echo "--grafana-http-port must be a numeric TCP port." >&2
  exit 1
fi

if [[ ! -d "${REPO_ROOT}/deploy/grafana" ]]; then
  echo "Could not locate deploy/grafana under repo root: ${REPO_ROOT}" >&2
  exit 1
fi

log() {
  printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

wait_for_http() {
  local attempts="$1"
  local delay_seconds="$2"
  shift 2

  local attempt
  for attempt in $(seq 1 "$attempts"); do
    if curl -fsS "$@" >/dev/null; then
      return 0
    fi
    sleep "$delay_seconds"
  done

  curl -fsS "$@" >/dev/null
}

detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64)
      echo "amd64"
      ;;
    aarch64|arm64)
      echo "arm64"
      ;;
    *)
      echo "Unsupported architecture: $(uname -m)" >&2
      exit 1
      ;;
  esac
}

install_base_packages() {
  log "Installing base packages"
  apt-get update
  apt-get install -y ca-certificates curl gpg nginx prometheus prometheus-node-exporter tar
}

install_grafana() {
  if command -v grafana-server >/dev/null 2>&1; then
    log "Grafana already installed"
    return
  fi

  log "Installing Grafana OSS"
  install -d -m 0755 /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/grafana.gpg ]]; then
    curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
  fi
  cat >/etc/apt/sources.list.d/grafana.list <<'EOF'
deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main
EOF
  apt-get update
  apt-get install -y grafana
}

install_process_exporter() {
  local arch url tmpdir extracted_dir
  arch="$(detect_arch)"
  url="https://github.com/ncabatoff/process-exporter/releases/download/v${PROCESS_EXPORTER_VERSION}/process-exporter-${PROCESS_EXPORTER_VERSION}.linux-${arch}.tar.gz"

  log "Installing process-exporter ${PROCESS_EXPORTER_VERSION} (${arch})"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' RETURN

  curl -fsSL "${url}" -o "${tmpdir}/process-exporter.tar.gz"
  tar -xzf "${tmpdir}/process-exporter.tar.gz" -C "${tmpdir}"
  extracted_dir="${tmpdir}/process-exporter-${PROCESS_EXPORTER_VERSION}.linux-${arch}"
  install -m 0755 "${extracted_dir}/process-exporter" /usr/local/bin/process-exporter
}

render_template() {
  local template_path="$1"
  local destination_path="$2"

  python3 - "$template_path" "$destination_path" "$PUBLIC_HOST" "$GRAFANA_ADMIN_USER" "$GRAFANA_ADMIN_PASSWORD" "$GRAFANA_HTTP_PORT" <<'PY2'
from pathlib import Path
import sys

template = Path(sys.argv[1]).read_text()
template = template.replace("__PUBLIC_HOST__", sys.argv[3])
template = template.replace("__GRAFANA_ADMIN_USER__", sys.argv[4])
template = template.replace("__GRAFANA_ADMIN_PASSWORD__", sys.argv[5])
template = template.replace("__GRAFANA_HTTP_PORT__", sys.argv[6])
Path(sys.argv[2]).write_text(template)
PY2
}

install_bundle_files() {
  log "Installing Grafana bundle files"

  install -d -m 0755 /etc/process-exporter
  install -d -m 0755 /opt/agent-team-grafana/prometheus
  install -d -m 0755 /etc/systemd/system/grafana-server.service.d
  install -d -m 0755 /etc/grafana/provisioning/datasources
  install -d -m 0755 /etc/grafana/provisioning/dashboards
  install -d -m 0755 /var/lib/grafana/dashboards/agent-team-grafana
  install -d -m 0755 /var/lib/grafana/dashboards/agent-team-grafana/host-system
  install -d -m 0755 /var/lib/grafana/dashboards/agent-team-grafana/agent-team
  install -d -m 0755 /var/lib/grafana/dashboards/agent-team-grafana/newapi
  install -d -m 0755 /var/lib/grafana/dashboards/agent-team-grafana/arena
  install -d -m 0755 /var/lib/grafana/dashboards/agent-team-grafana/uptime-kuma
  install -d -m 0755 /etc/nginx/sites-available /etc/nginx/sites-enabled
  install -d -m 0755 /var/lib/agent-team-prometheus

  install -m 0644 "${REPO_ROOT}/deploy/grafana/process-exporter/process-exporter.yml" /etc/process-exporter/process-exporter.yml
  install -m 0644 "${REPO_ROOT}/deploy/grafana/systemd/process-exporter.service" /etc/systemd/system/process-exporter.service
  install -m 0644 "${REPO_ROOT}/deploy/grafana/systemd/agent-team-prometheus.service" /etc/systemd/system/agent-team-prometheus.service
  install -m 0644 "${REPO_ROOT}/deploy/grafana/systemd/agent-team-metrics-exporter.service" /etc/systemd/system/agent-team-metrics-exporter.service
  install -m 0644 "${REPO_ROOT}/deploy/grafana/systemd/newapi-metrics-exporter.service" /etc/systemd/system/newapi-metrics-exporter.service
  install -m 0644 "${REPO_ROOT}/deploy/grafana/systemd/arena-metrics-exporter.service" /etc/systemd/system/arena-metrics-exporter.service
  install -m 0644 "${REPO_ROOT}/deploy/grafana/systemd/uptime-kuma-metrics-exporter.service" /etc/systemd/system/uptime-kuma-metrics-exporter.service
  install -m 0644 "${REPO_ROOT}/deploy/grafana/prometheus/prometheus.yml" /opt/agent-team-grafana/prometheus/prometheus.yml
  install -m 0644 "${REPO_ROOT}/deploy/grafana/provisioning/datasources/prometheus.yaml" /etc/grafana/provisioning/datasources/agent-team-prometheus.yaml
  install -m 0644 "${REPO_ROOT}/deploy/grafana/provisioning/dashboards/dashboard-provider.yaml" /etc/grafana/provisioning/dashboards/agent-team-dashboard-provider.yaml
  install -m 0644 "${REPO_ROOT}/deploy/grafana/dashboards/local-host-observability.json" /var/lib/grafana/dashboards/agent-team-grafana/host-system/local-host-observability.json
  for dashboard in "${REPO_ROOT}"/deploy/grafana/dashboards/agent-team-*.json; do
    install -m 0644 "$dashboard" "/var/lib/grafana/dashboards/agent-team-grafana/agent-team/$(basename "$dashboard")"
  done
  for dashboard in "${REPO_ROOT}"/deploy/grafana/dashboards/newapi-*.json; do
    install -m 0644 "$dashboard" "/var/lib/grafana/dashboards/agent-team-grafana/newapi/$(basename "$dashboard")"
  done
  for dashboard in "${REPO_ROOT}"/deploy/grafana/dashboards/arena-*.json; do
    install -m 0644 "$dashboard" "/var/lib/grafana/dashboards/agent-team-grafana/arena/$(basename "$dashboard")"
  done
  for dashboard in "${REPO_ROOT}"/deploy/grafana/dashboards/uptime-kuma-*.json; do
    install -m 0644 "$dashboard" "/var/lib/grafana/dashboards/agent-team-grafana/uptime-kuma/$(basename "$dashboard")"
  done

  render_template \
    "${REPO_ROOT}/deploy/grafana/grafana/grafana-server.override.conf.template" \
    "/etc/systemd/system/grafana-server.service.d/agent-team-grafana.conf"

  render_template \
    "${REPO_ROOT}/deploy/grafana/nginx/grafana-http.conf.template" \
    "/etc/nginx/sites-available/agent-team-grafana.conf"

  ln -sfn /etc/nginx/sites-available/agent-team-grafana.conf /etc/nginx/sites-enabled/agent-team-grafana.conf
}

restart_services() {
  log "Reloading systemd and starting services"
  systemctl daemon-reload
  systemctl enable --now prometheus-node-exporter.service
  systemctl enable --now process-exporter.service
  systemctl enable --now agent-team-metrics-exporter.service
  systemctl enable --now newapi-metrics-exporter.service
  systemctl enable --now arena-metrics-exporter.service
  systemctl enable --now uptime-kuma-metrics-exporter.service
  systemctl enable --now agent-team-prometheus.service
  systemctl enable --now grafana-server.service
  systemctl enable --now nginx.service
  nginx -t
  systemctl reload nginx.service
}

run_health_checks() {
  log "Running local health checks"
  wait_for_http 10 2 http://127.0.0.1:9256/metrics
  wait_for_http 10 2 http://127.0.0.1:19130/metrics
  wait_for_http 10 2 http://127.0.0.1:19100/metrics
  wait_for_http 10 2 http://127.0.0.1:19150/metrics
  wait_for_http 10 2 http://127.0.0.1:19120/metrics
  wait_for_http 10 2 http://127.0.0.1:19090/-/ready
  wait_for_http 30 2 "http://127.0.0.1:${GRAFANA_HTTP_PORT}/api/health"
  wait_for_http 15 2 -H "Host: ${PUBLIC_HOST}" http://127.0.0.1/
}

main() {
  require_cmd apt-get
  require_cmd curl
  require_cmd python3
  require_cmd tar
  require_cmd nginx

  install_base_packages
  install_grafana
  install_process_exporter
  install_bundle_files
  restart_services
  run_health_checks

  cat <<EOF

Grafana local observability stack installed successfully.

Public URL: http://${PUBLIC_HOST}/
Prometheus:  http://127.0.0.1:19090/
Grafana user: ${GRAFANA_ADMIN_USER}

Next suggested checks:
  1. Verify DNS / external firewall allows HTTP to this host.
  2. Open Grafana and confirm the dashboard "AT | Host-System | System | Overview" is loaded.
  3. Optionally change nginx to HTTPS after initial smoke test.
EOF
}

main
