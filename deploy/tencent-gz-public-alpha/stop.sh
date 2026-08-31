#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
dc_public down --remove-orphans 2>/dev/null || dc_pre down --remove-orphans
echo "Services stopped. Named volumes were retained."
