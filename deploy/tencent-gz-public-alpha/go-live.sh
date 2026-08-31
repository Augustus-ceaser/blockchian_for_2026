#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
require_file "$ENV_FILE"
check_disk 8

domain="$(grep -E '^PUBLIC_DOMAIN=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
icp="$(grep -E '^ICP_NUMBER=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
[[ -n "$domain" && "$domain" != *".invalid" ]] || {
  echo "A real PUBLIC_DOMAIN is required." >&2; exit 1;
}
[[ -n "$icp" ]] || { echo "A real ICP_NUMBER is required." >&2; exit 1; }

read -r -p "Confirm ICP approval, DNS, and Tencent firewall 80/443 are ready [yes/NO]: " answer
[[ "$answer" == "yes" ]] || { echo "Cancelled."; exit 1; }

export COMPOSE_PARALLEL_LIMIT=1
dc_public config --quiet
dc_public build gateway
dc_pre down --remove-orphans
dc_public up -d
"$DEPLOY_DIR/health-check.sh" public
echo "Public Alpha started. Verify HTTPS and the real ICP footer manually."
