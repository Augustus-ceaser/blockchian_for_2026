#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
require_file "$ENV_FILE"

config="$(dc config)"
fail=0

reject() {
  local label="$1"
  local pattern="$2"
  if printf '%s\n' "$config" | grep -Eiq "$pattern"; then
    echo "FAIL: ${label}" >&2
    fail=1
  else
    echo "PASS: ${label}"
  fi
}

reject "no privileged services" 'privileged:[[:space:]]*true'
reject "no Docker socket" '/var/run/docker.sock'
reject "no host network" 'network_mode:[[:space:]]*host'
reject "no latest image tag" 'image:[[:space:]].*:latest([[:space:]]|$)'
reject "no development source bind mount" 'source:[[:space:]].*(backend|frontend)$'

for port in 5432 9000 9001 8000 8080; do
  if docker ps --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
      --format '{{.Ports}}' | grep -Eq "(^|:)${port}->|0\\.0\\.0\\.0:${port}|:::${port}"; then
    echo "FAIL: internal port ${port} is host-published." >&2
    fail=1
  else
    echo "PASS: internal port ${port} is not host-published."
  fi
done

for file in /etc/medtrust/production.env /etc/medtrust/secrets/*; do
  [[ -e "$file" ]] || continue
  mode="$(stat -c '%a' "$file")"
  if [[ "$mode" == "600" ]]; then
    continue
  fi
  acl="$(getfacl -cp "$file")"
  if [[ "$mode" == "640" ]] \
      && grep -qx 'user:10001:r--' <<<"$acl" \
      && grep -qx 'group::---' <<<"$acl" \
      && grep -qx 'other::---' <<<"$acl"; then
    continue
  fi
  echo "FAIL: $file permissions do not match the root-only or backend-read ACL policy." >&2
  fail=1
done

if grep -RIlE 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|D:\\\\MedTrustData|patient[_ -]?id' \
    "$INSTALL_ROOT" --exclude-dir=.git --exclude='*.md' | grep -q .; then
  echo "FAIL: forbidden private material or patient/path marker found." >&2
  fail=1
else
  echo "PASS: no forbidden private material or patient/path marker."
fi

(( fail == 0 )) || exit 1
echo "Security check PASS."
