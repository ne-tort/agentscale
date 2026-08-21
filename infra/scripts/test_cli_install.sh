#!/usr/bin/env bash
# Verifies official k3d install.sh works non-interactively (USE_SUDO=false on bash side).
set -euo pipefail
export PATH="${HOME}/.local/bin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if [[ -x "${HOME}/.local/bin/k3d" ]]; then
  mv "${HOME}/.local/bin/k3d" "${TMP}/k3d.away"
fi

bash "${SCRIPT_DIR}/install_cli_tools.sh"
command -v k3d | grep -q "${HOME}/.local/bin/k3d"
k3d version
echo "PASS: install_cli_tools.sh (official install.sh, no sudo)"
