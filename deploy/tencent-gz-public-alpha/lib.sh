#!/usr/bin/env bash
set -Eeuo pipefail

load_server_env() {
  if [[ -f /etc/medtrust/server.env ]]; then
    # shellcheck disable=SC1091
    source /etc/medtrust/server.env
  fi
}

load_server_env

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${SERVER_INSTALL_ROOT:-/opt/medtrust}"
STATE_ROOT="${SERVER_STATE_ROOT:-/srv/medtrust}"
BACKUP_ROOT="${SERVER_BACKUP_ROOT:-/srv/medtrust/backups}"
ENV_FILE="${MEDTRUST_ENV_FILE:-/etc/medtrust/production.env}"
PROJECT_NAME="medtrust-public-alpha"

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
  fi
}

require_file() {
  [[ -f "$1" ]] || { echo "Missing required file: $1" >&2; exit 1; }
}

check_disk() {
  local minimum="${1:-8}"
  local free_gb
  free_gb="$(df -Pk "$STATE_ROOT" | awk 'NR==2 {printf "%d", $4/1024/1024}')"
  if (( free_gb < minimum )); then
    echo "Only ${free_gb}GB is free; ${minimum}GB is required." >&2
    exit 1
  fi
  if (( free_gb < 15 )); then
    echo "WARNING: only ${free_gb}GB remains; 15GB is recommended." >&2
  fi
}

dc() {
  docker compose --project-directory "$INSTALL_ROOT" --env-file "$ENV_FILE" \
    -f "$DEPLOY_DIR/compose.production.yaml" "$@"
}

dc_pre() {
  docker compose --project-directory "$INSTALL_ROOT" --env-file "$ENV_FILE" \
    -f "$DEPLOY_DIR/compose.production.yaml" \
    -f "$DEPLOY_DIR/compose.pre-icp.yaml" "$@"
}

dc_public() {
  docker compose --project-directory "$INSTALL_ROOT" --env-file "$ENV_FILE" \
    -f "$DEPLOY_DIR/compose.production.yaml" \
    -f "$DEPLOY_DIR/compose.public.yaml" "$@"
}
