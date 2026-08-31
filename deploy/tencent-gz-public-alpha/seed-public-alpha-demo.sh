#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
require_root
require_file "$ENV_FILE"

read -r -p "Seed optional Synthetic/Public Non-clinical demo metadata? [yes/NO] " answer
[[ "$answer" == "yes" ]] || { echo "Cancelled."; exit 1; }

result="$(dc run --rm backend python -m app.tools.seed_public_alpha_demo)"
space_id="$(printf '%s' "$result" | sed -n 's/.*"space_id": "\([^"]*\)".*/\1/p')"
[[ -n "$space_id" ]] || {
  echo "Demo seed result could not be parsed." >&2
  exit 1
}
echo "Optional demo metadata initialized: space_id=${space_id}"
echo "No model weights, patient data, Artifact, or EvidenceBundle was created."
