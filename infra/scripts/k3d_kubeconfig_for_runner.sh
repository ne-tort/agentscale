#!/usr/bin/env bash
# Export k3d kubeconfig for github-runner mount (no sudo).
# Prefer: bash infra/scripts/ensure_k3d_cluster.sh (also refreshes kubeconfig).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
OUT="${KUBECONFIG_OUT:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"

export KUBECONFIG_OUT="$OUT"
export K3D_CLUSTER="$CLUSTER"

# Reuse ensure path when cluster may be stopped after reboot.
if [[ "${K3D_KUBECONFIG_ONLY:-0}" == "1" ]]; then
  command -v k3d >/dev/null 2>&1 || {
    echo "k3d not found" >&2
    exit 1
  }
  mkdir -p "$(dirname "$OUT")"
  k3d kubeconfig get "$CLUSTER" >"$OUT"
  chmod 600 "$OUT" || true
  echo "Wrote $OUT"
else
  bash "${SCRIPT_DIR}/ensure_k3d_cluster.sh"
fi

echo "export KUBECONFIG=$OUT"
