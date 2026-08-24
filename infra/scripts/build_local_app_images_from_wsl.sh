#!/usr/bin/env bash
# Invoke build_local_app_images.ps1 on Docker Desktop when WSL docker build fails.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PS1="${SCRIPT_DIR}/build_local_app_images.ps1"
API_BASE="${API_BASE:-http://prodavan.local:8088/api/v1}"

if [[ ! -f "$PS1" ]]; then
  echo "WARN: ${PS1} not found" >&2
  exit 1
fi

if ! command -v powershell.exe >/dev/null 2>&1; then
  echo "WARN: powershell.exe not available — build on Windows manually" >&2
  exit 1
fi

echo "==> Windows Docker build (API_BASE=${API_BASE})"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$PS1")" -ApiBase "$API_BASE"
echo "ok — Windows images built; WSL will bridge on import"
