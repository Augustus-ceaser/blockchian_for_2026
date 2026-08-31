#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
load_server_env
require_file "$ENV_FILE"
check_disk 15

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary="${BACKUP_ROOT}/.${timestamp}.tmp"
destination="${BACKUP_ROOT}/${timestamp}"
project_volume="${PROJECT_NAME//-/_}"
minio_volume="${PROJECT_NAME}_minio_data"

[[ ! -e "$temporary" && ! -e "$destination" ]] || {
  echo "Backup destination already exists." >&2
  exit 1
}
install -d -m 0700 "$temporary"
trap 'rm -rf "$temporary"' ERR

dc exec -T postgres pg_dump -U medtrust -d medtrust \
  --format=custom --no-owner --no-privileges > "${temporary}/postgres.dump"
dc exec -T postgres psql -U medtrust -d medtrust -At \
  -c "SELECT version_num FROM alembic_version" > "${temporary}/migration.txt"
dc exec -T postgres psql -U medtrust -d medtrust -At \
  -c "SELECT schemaname||'.'||relname||'='||n_live_tup FROM pg_stat_user_tables ORDER BY 1" \
  > "${temporary}/table-counts.txt"

dc stop backend dispatcher minio
trap 'dc start minio backend dispatcher >/dev/null 2>&1 || true; rm -rf "$temporary"' ERR
docker run --rm -v "${minio_volume}:/source:ro" -v "${temporary}:/backup" \
  alpine:3.20.3 sh -ec 'cd /source && tar -czf /backup/minio-data.tar.gz .'
dc start minio backend dispatcher
trap 'rm -rf "$temporary"' ERR

cp "$ENV_FILE" "${temporary}/production.env.redacted"
sed -i -E '/(PASSWORD|SECRET|TOKEN|KEY)=/d' "${temporary}/production.env.redacted"
git -C "$INSTALL_ROOT" rev-parse HEAD > "${temporary}/git-commit.txt" 2>/dev/null || \
  printf 'package-deployment\n' > "${temporary}/git-commit.txt"
docker compose version > "${temporary}/compose-version.txt"
find "$temporary" -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum > "${temporary}/SHA256SUMS"
printf 'complete\n' > "${temporary}/BACKUP_COMPLETE"
chmod -R go-rwx "$temporary"
mv "$temporary" "$destination"
trap - ERR

daily="${BACKUP_KEEP_DAILY:-7}"
weekly="${BACKUP_KEEP_WEEKLY:-4}"
mapfile -t backups < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
  -name '20*T*Z' -printf '%T@ %p\n' | sort -nr | awk '{print $2}')
declare -A kept_week
weekly_count=0
for index in "${!backups[@]}"; do
  candidate="${backups[$index]}"
  if (( index < daily )); then
    continue
  fi
  week="$(date -u -r "$candidate" +%G-%V)"
  if [[ -z "${kept_week[$week]:-}" && "$weekly_count" -lt "$weekly" ]]; then
    kept_week[$week]=1
    weekly_count=$((weekly_count + 1))
    continue
  fi
  rm -rf --one-file-system "$candidate"
done

echo "Backup complete: $destination"
echo "An encrypted off-host or COS copy remains a required operational follow-up."
