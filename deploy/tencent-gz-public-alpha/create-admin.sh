#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
require_file "$ENV_FILE"

status="$(dc run --rm backend python -m app.tools.bootstrap_public_alpha_accounts --status)"
if printf '%s' "$status" | grep -q '"foundation_complete": true'; then
  username="operator.demo"
  user_id="$(printf '%s' "$status" | sed -n 's/.*"operator_id": "\([^"]*\)".*/\1/p')"
  echo "Administrator already exists: username=${username}, user_id=${user_id}"
  echo "Existing password was not changed."
  exit 0
fi
if printf '%s' "$status" | grep -q '"foundation_present": true'; then
  echo "Public Alpha account foundation is incomplete; refusing automatic repair." >&2
  echo "Review the account status before retrying." >&2
  exit 1
fi

read -r -p "Initialize the invitation-only Synthetic Public Alpha accounts? [yes/NO] " answer
[[ "$answer" == "yes" ]] || { echo "Cancelled."; exit 1; }

read -r -s -p "Set the operator.demo password: " password
echo
read -r -s -p "Confirm the operator.demo password: " confirmation
echo
[[ "$password" == "$confirmation" ]] || {
  echo "Passwords do not match." >&2
  exit 1
}
(( ${#password} >= 14 )) || {
  echo "Password must contain at least 14 characters." >&2
  exit 1
}
[[ "$password" != "operator.demo" ]] || {
  echo "Password must not equal the username." >&2
  exit 1
}

operator_secret="/etc/medtrust/secrets/demo_operator_password"
temporary_secret="$(mktemp /etc/medtrust/secrets/.operator-password.XXXXXX)"
trap 'rm -f "$temporary_secret"; unset password confirmation' EXIT
printf '%s' "$password" > "$temporary_secret"
chown root:root "$temporary_secret"
chmod 0600 "$temporary_secret"
setfacl -m u:10001:r-- "$temporary_secret"
mv -f "$temporary_secret" "$operator_secret"
unset password confirmation

result="$(dc run --rm backend python -m app.tools.bootstrap_public_alpha_accounts)"
username="$(printf '%s' "$result" | sed -n 's/.*"username": "\([^"]*\)".*/\1/p')"
user_id="$(printf '%s' "$result" | sed -n 's/.*"user_id": "\([^"]*\)".*/\1/p')"
[[ -n "$username" && -n "$user_id" ]] || {
  echo "Administrator result could not be parsed." >&2
  exit 1
}
install -d -m 0755 /var/log/medtrust
printf '%s public_alpha.admin.bootstrap username=%s user_id=%s result=success\n' \
  "$(date -u --iso-8601=seconds)" "$username" "$user_id" \
  >> /var/log/medtrust/admin-bootstrap.audit.log
chmod 0600 /var/log/medtrust/admin-bootstrap.audit.log
echo "Administrator initialized: username=${username}, user_id=${user_id}"
echo "No password was printed. Existing identities and passwords are never replaced."
