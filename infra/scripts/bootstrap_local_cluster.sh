#!/usr/bin/env bash
# Bootstrap / recover local k3d + Argo + workload (idempotent after reboot).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TF_DIR="${ROOT}/infra/terraform/environments/local"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
HTTP_PORT="${HTTP_PORT:-8088}"

TF=""
if command -v terraform >/dev/null 2>&1; then
  TF=terraform
elif [[ -x "${ROOT}/tools/terraform" ]]; then
  TF="${ROOT}/tools/terraform"
elif [[ -x "${ROOT}/tools/terraform.exe" ]]; then
  TF="${ROOT}/tools/terraform.exe"
fi

echo "==> Ensure k3d cluster (create/start + kubeconfig)"
bash "${SCRIPT_DIR}/ensure_k3d_cluster.sh"
export KUBECONFIG="${KUBECONFIG_OUT:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"

if [[ -n "$TF" ]]; then
  echo "==> Terraform state sync (optional; ensure script is source of truth for runtime)"
  (
    cd "$TF_DIR"
    "$TF" init -input=false
    "$TF" apply -auto-approve -input=false || echo "WARN: terraform apply failed — cluster already ensured"
  )
else
  echo "WARN: terraform not found — skipped state sync"
fi

export KUBECONFIG="${ROOT}/infra/.kube/prodavan-k3d.yaml"

if [[ -n "${GHCR_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  echo "==> GHCR pull secret"
  bash "${SCRIPT_DIR}/create_ghcr_pull_secret.sh"
  echo "==> Import overlay images into k3d"
  bash "${SCRIPT_DIR}/import_overlay_images.sh" || echo "WARN: import_overlay_images failed"
else
  echo "WARN: GHCR_TOKEN/GITHUB_TOKEN unset — private pulls need secret ghcr-pull"
fi

echo "==> Argo CD"
bash "${SCRIPT_DIR}/ensure_argocd.sh"

echo "==> Wait workload"
bash "${SCRIPT_DIR}/wait_prodavan_ready.sh"

echo "==> Smoke"
bash "${SCRIPT_DIR}/smoke_ingress.sh" || echo "WARN: smoke failed — check imagePullSecrets / GHCR"

echo "Done. Cluster=${CLUSTER} KUBECONFIG=${KUBECONFIG}"
echo "After reboot: bash infra/scripts/recover_local_stack.sh"
