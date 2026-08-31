#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
new_release="${1:-}"
[[ -n "$new_release" && -d "$new_release" ]] || {
  echo "Usage: $0 /opt/medtrust/releases/<new-release>" >&2
  exit 1
}
check_disk 8
"$DEPLOY_DIR/backup.sh"

current_link="/opt/medtrust/current"
previous="$(readlink -f "$current_link" 2>/dev/null || true)"
ln -sfn "$new_release" "${current_link}.new"
mv -Tf "${current_link}.new" "$current_link"
printf '%s\n' "$previous" > /var/lib/medtrust-previous-release
DEPLOY_DIR="${new_release}/deploy/tencent-gz-public-alpha"

export COMPOSE_PARALLEL_LIMIT=1
dc build backend
dc build gateway
"$DEPLOY_DIR/migrate.sh"
dc up -d
"$DEPLOY_DIR/health-check.sh" "${DEPLOY_MODE:-pre-icp}"
echo "Upgrade PASS. Database downgrades are never automatic."
