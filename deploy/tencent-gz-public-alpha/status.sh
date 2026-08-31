#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
dc ps
docker ps --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
