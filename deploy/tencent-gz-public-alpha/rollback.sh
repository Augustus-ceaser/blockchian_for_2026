#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
previous_file="/var/lib/medtrust-previous-release"
require_file "$previous_file"
previous="$(cat "$previous_file")"
[[ -d "$previous" ]] || { echo "Previous release is unavailable." >&2; exit 1; }

current_migration="$(dc run --rm --no-deps backend alembic current 2>/dev/null | tail -1)"
read -r -p "Rollback application files only; no database downgrade will run [yes/NO]: " answer
[[ "$answer" == "yes" ]] || { echo "Cancelled."; exit 1; }

ln -sfn "$previous" /opt/medtrust/current.new
mv -Tf /opt/medtrust/current.new /opt/medtrust/current
DEPLOY_DIR="${previous}/deploy/tencent-gz-public-alpha"
dc build backend
dc build gateway
dc up -d
"$DEPLOY_DIR/health-check.sh" "${DEPLOY_MODE:-pre-icp}"
echo "Application rollback PASS. Database remained at: ${current_migration}"
echo "If the older application is migration-incompatible, stop and restore a tested backup."
