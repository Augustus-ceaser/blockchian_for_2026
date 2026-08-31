#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
require_file "$ENV_FILE"

for _ in $(seq 1 30); do
  if dc exec -T postgres pg_isready -U medtrust -d medtrust >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
dc exec -T postgres pg_isready -U medtrust -d medtrust >/dev/null

heads="$(dc run --rm --no-deps backend alembic heads | grep -c '(head)')"
[[ "$heads" == "1" ]] || { echo "Alembic must have exactly one head." >&2; exit 1; }
dc run --rm --no-deps backend alembic upgrade head
dc run --rm --no-deps backend alembic current

dc exec -T postgres psql -U medtrust -d medtrust -v ON_ERROR_STOP=1 \
  -c "SELECT to_regclass('medtrust.users'), to_regclass('medtrust.audit_events');" \
  | grep -q "users"
echo "Fresh or incremental migration completed. No downgrade was attempted."
