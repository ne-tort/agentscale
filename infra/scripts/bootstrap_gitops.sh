#!/usr/bin/env bash
# GitOps layer only (Argo + images + wait + smoke). Cluster must already exist.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
export PATH="${HOME}/.local/bin:/usr/sbin:${PATH}"

wait_docker 30
wait_nodes_schedulable 60

echo "==> Platform broker images"
bash "${SCRIPT_DIR}/import_platform_images_k3d.sh" || echo "WARN: platform image import failed"

if [[ -n "${GHCR_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  echo "==> GHCR pull secret"
  bash "${SCRIPT_DIR}/create_ghcr_pull_secret.sh"
  echo "==> Import overlay images (k3d preload)"
  bash "${SCRIPT_DIR}/import_overlay_images.sh" || echo "WARN: import incomplete"
  wait_nodes_schedulable 60
else
  echo "WARN: GHCR_TOKEN unset — private image pulls may fail; importing local tags if present"
  CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
  for img in ghcr.io/ne-tort/prodavan-api:latest ghcr.io/ne-tort/prodavan-web:latest; do
    if docker image inspect "$img" >/dev/null 2>&1; then
      k3d image import "$img" -c "$CLUSTER" || true
    fi
  done
fi

echo "==> Argo CD + Application"
bash "${SCRIPT_DIR}/ensure_argocd.sh"

echo "==> Wait workloads"
bash "${SCRIPT_DIR}/wait_prodavan_ready.sh"

echo "==> Smoke"
bash "${SCRIPT_DIR}/smoke_ingress.sh"

echo "GitOps bootstrap OK (KUBECONFIG=${KUBECONFIG})"
echo "Optional UI seed: bash infra/scripts/seed_dev_identity.sh"
