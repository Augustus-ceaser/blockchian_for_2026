#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
mode="${1:-pre-icp}"

if [[ "$mode" == "public" ]]; then
  domain="$(grep -E '^PUBLIC_DOMAIN=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
  curl -fsS --max-time 15 "https://${domain}/" >/dev/null
  curl -fsS --max-time 15 "https://${domain}/api/v1/health/live" | grep -q '"ok"'
else
  port="$(grep -E '^PRE_ICP_ENTRY_PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2)"
  port="${port:-18080}"
  curl -fsS --max-time 10 "http://127.0.0.1:${port}/" >/dev/null
  curl -fsS --max-time 10 "http://127.0.0.1:${port}/api/v1/health/live" | grep -q '"ok"'
fi

dc ps --status running --services | grep -q '^gateway$'
echo "Health check PASS (${mode})."
