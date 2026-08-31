#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root

source /etc/os-release
[[ "${ID}" == "ubuntu" && "${VERSION_ID}" == "24.04" ]] || {
  echo "Ubuntu 24.04 is required; found ${PRETTY_NAME}." >&2
  exit 1
}

command -v docker >/dev/null
docker compose version >/dev/null
docker info >/dev/null

cpu_count="$(nproc)"
memory_mb="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)"
disk_gb="$(df -Pk / | awk 'NR==2 {printf "%d", $2/1024/1024}')"
(( cpu_count >= 2 )) || { echo "At least 2 CPUs are required." >&2; exit 1; }
(( memory_mb >= 3500 )) || { echo "At least 3.5GB RAM is required." >&2; exit 1; }
(( disk_gb >= 55 )) || { echo "At least 55GB system disk is required." >&2; exit 1; }

install -d -m 0755 /opt/medtrust /srv/medtrust /srv/medtrust/backups
install -d -m 0700 /etc/medtrust /etc/medtrust/secrets
if [[ ! -f /etc/medtrust/server.env ]]; then
  install -m 0600 "$DEPLOY_DIR/server.env.example" /etc/medtrust/server.env
fi

echo "UTC time: $(date -u --iso-8601=seconds)"
timedatectl status --no-pager | sed -n '1,8p'
echo "Recommended timezone: Asia/Shanghai. This script does not change it automatically."

swap_mb="$(awk '/SwapTotal/ {printf "%d", $2/1024}' /proc/meminfo)"
if (( swap_mb < 3500 )); then
  cat <<'EOF'
WARNING: less than 4GB swap is configured.
After operator review, a conventional option is:
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
Swap is OOM buffering, not application memory.
EOF
fi

echo "No firewall, SSH credential, DNS, Caddy, or public-port change was made."
