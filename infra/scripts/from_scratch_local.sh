#!/usr/bin/env bash
# From-scratch local chain (run on WSL after Terraform SSH apply, or fully on WSL).
# 1) optional: destroy old k3d
# 2) Terraform is expected to create cluster (SSH or local)
# 3) this script only finishes GitOps if FROM_TERRAFORM=1; otherwise full bootstrap
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export PATH="${HOME}/.local/bin:/usr/sbin:${PATH}"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"

if [[ "${DESTROY_FIRST:-0}" == "1" ]]; then
  echo "==> Destroy existing k3d cluster"
  k3d cluster delete "${K3D_CLUSTER:-prodavan-dev}" 2>/dev/null || true
fi

if [[ "${FROM_TERRAFORM:-0}" == "1" ]]; then
  bash "${SCRIPT_DIR}/bootstrap_gitops.sh"
  exit 0
fi

# Legacy full path: ensure + optional terraform state + gitops
bash "${SCRIPT_DIR}/bootstrap_local_cluster.sh"
