#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
umask 077

secret_dir="/etc/medtrust/secrets"
install -d -m 0700 "$secret_dir"
command -v setfacl >/dev/null || {
  echo "The Ubuntu acl package is required for non-root container secret access." >&2
  exit 1
}

write_new_secret() {
  local name="$1"
  local value="$2"
  local path="$secret_dir/$name"
  [[ ! -e "$path" ]] || { echo "Refusing to overwrite $path" >&2; exit 1; }
  printf '%s' "$value" > "$path"
  chmod 0600 "$path"
  chown root:root "$path"
}

random_hex() { openssl rand -hex "$1"; }
random_urlsafe() { openssl rand -base64 "$1" | tr -d '\n=+/' | cut -c1-"$2"; }

postgres_password="$(random_hex 24)"
minio_user="medtrust_$(random_hex 6)"
minio_password="$(random_hex 24)"

write_new_secret postgres_password "$postgres_password"
write_new_secret database_url \
  "postgresql+asyncpg://medtrust:${postgres_password}@postgres:5432/medtrust"
write_new_secret minio_root_user "$minio_user"
write_new_secret minio_root_password "$minio_password"
write_new_secret demo_hospital_password "$(random_urlsafe 32 28)"
write_new_secret demo_model_password "$(random_urlsafe 32 28)"
write_new_secret demo_requester_password "$(random_urlsafe 32 28)"
write_new_secret demo_operator_password "$(random_urlsafe 32 28)"
write_new_secret demo_catalog_curator_password "$(random_urlsafe 32 28)"

if [[ ! -f "$ENV_FILE" ]]; then
  install -m 0600 "$DEPLOY_DIR/production.env.example" "$ENV_FILE"
fi
chmod 0600 "$ENV_FILE"
chown root:root "$ENV_FILE"

# Docker Compose file secrets are bind mounts. Grant only the fixed Backend UID
# access while retaining root ownership and denying all ordinary host users.
setfacl -m u:10001:--x "$secret_dir"
setfacl -m u:10001:r-- \
  "$secret_dir/database_url" \
  "$secret_dir/minio_root_user" \
  "$secret_dir/minio_root_password" \
  "$secret_dir/demo_hospital_password" \
  "$secret_dir/demo_model_password" \
  "$secret_dir/demo_requester_password" \
  "$secret_dir/demo_operator_password" \
  "$secret_dir/demo_catalog_curator_password"

unset postgres_password minio_password minio_user
echo "Production secret files created with root-only permissions."
echo "Secret values were not printed. Existing files are never overwritten."
