#!/usr/bin/env bash
# Backward-compatible entrypoint → ensure_argocd.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/ensure_argocd.sh" "$@"
