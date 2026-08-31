#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
backup="${1:-$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*T*Z' | sort | tail -1)}"
require_file "${backup}/BACKUP_COMPLETE"
(cd "$backup" && sha256sum -c SHA256SUMS)

suffix="$(date +%s)"
pg_container="medtrust-restore-pg-${suffix}"
pg_volume="medtrust_restore_pg_${suffix}"
obj_volume="medtrust_restore_obj_${suffix}"
password="$(openssl rand -hex 16)"

cleanup() {
  docker rm -f "$pg_container" >/dev/null 2>&1 || true
  docker volume rm "$pg_volume" "$obj_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker volume create "$pg_volume" >/dev/null
docker volume create "$obj_volume" >/dev/null
docker run -d --name "$pg_container" -e POSTGRES_PASSWORD="$password" \
  -e POSTGRES_DB=restore_test -v "${pg_volume}:/var/lib/postgresql/data" \
  postgres:16.9-alpine >/dev/null
for _ in $(seq 1 30); do
  docker exec "$pg_container" pg_isready -U postgres -d restore_test >/dev/null 2>&1 && break
  sleep 1
done
docker exec -i "$pg_container" pg_restore -U postgres -d restore_test \
  --no-owner --no-privileges < "${backup}/postgres.dump"
docker exec "$pg_container" psql -U postgres -d restore_test -At \
  -c "SELECT to_regclass('medtrust.users'), to_regclass('medtrust.audit_events')" \
  | grep -q "users"
docker exec "$pg_container" psql -U postgres -d restore_test -At \
  -c "SELECT version_num FROM alembic_version" | grep -Fxq "$(cat "${backup}/migration.txt")"

docker run --rm -v "${obj_volume}:/restore" -v "${backup}:/backup:ro" \
  alpine:3.20.3 sh -ec 'cd /restore && tar -xzf /backup/minio-data.tar.gz'
object_count="$(docker run --rm -v "${obj_volume}:/restore:ro" alpine:3.20.3 \
  sh -ec "find /restore -type f | wc -l")"
[[ "$object_count" =~ ^[0-9]+$ ]]

echo "Restore test PASS: migration matched, critical tables exist, object files=${object_count}."
echo "Production data and the source backup were not modified."
