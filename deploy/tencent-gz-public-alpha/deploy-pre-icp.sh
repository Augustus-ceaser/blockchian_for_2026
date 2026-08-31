#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
require_file "$ENV_FILE"
check_disk 8

export COMPOSE_PARALLEL_LIMIT=1
dc_pre config --quiet
dc_pre build backend
dc_pre build gateway
dc_pre up -d postgres minio
"$DEPLOY_DIR/migrate.sh"
dc_pre up -d backend dispatcher gateway
"$DEPLOY_DIR/health-check.sh" pre-icp

entry_port="$(grep -E '^PRE_ICP_ENTRY_PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2)"
entry_port="${entry_port:-18080}"
echo "Pre-ICP is loopback-only at 127.0.0.1:${entry_port}."
echo "No Caddy, port 80, or port 443 service was started."
