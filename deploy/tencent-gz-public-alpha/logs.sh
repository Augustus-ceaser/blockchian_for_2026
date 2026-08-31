#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
dc logs --tail "${LOG_TAIL:-200}" "${@:-}"
