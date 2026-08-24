#!/usr/bin/env bash
# Single entrypoint after WSL/Docker reboot: cluster → images → Argo → wait → smoke.
# Idempotent. Prefer this over ad-hoc terraform apply for runtime recover.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
export KUBECONFIG_OUT="${KUBECONFIG}"
export PATH="${HOME}/.local/bin:/usr/sbin:${PATH}"

echo "==> 1/6 ensure k3d"
bash "${SCRIPT_DIR}/ensure_k3d_cluster.sh"

echo "==> 2/6 platform broker images (public registries)"
bash "${SCRIPT_DIR}/import_platform_images_k3d.sh" || echo "WARN: platform image import failed"

echo "==> 2b/6 local API/web images if present (no rebuild)"
BUILD=0 bash "${SCRIPT_DIR}/import_local_app_images_k3d.sh" || echo "WARN: no local app images"

if [[ -n "${GHCR_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  echo "==> 3/6 GHCR pull secret + overlay images"
  bash "${SCRIPT_DIR}/create_ghcr_pull_secret.sh"
  bash "${SCRIPT_DIR}/import_overlay_images.sh" || echo "WARN: import_overlay_images failed"
else
  echo "==> 3/6 skip GHCR (set GHCR_TOKEN for private pulls); try local tag import if present"
  if docker image inspect ghcr.io/ne-tort/prodavan-api:latest >/dev/null 2>&1; then
    k3d image import ghcr.io/ne-tort/prodavan-api:latest -c "${K3D_CLUSTER:-prodavan-dev}" || true
  fi
  if docker image inspect ghcr.io/ne-tort/prodavan-web:latest >/dev/null 2>&1; then
    k3d image import ghcr.io/ne-tort/prodavan-web:latest -c "${K3D_CLUSTER:-prodavan-dev}" || true
  fi
fi

echo "==> 4/6 Argo CD"
bash "${SCRIPT_DIR}/ensure_argocd.sh"

echo "==> 5/6 wait workloads"
bash "${SCRIPT_DIR}/wait_prodavan_ready.sh"

echo "==> 6/6 smoke"
bash "${SCRIPT_DIR}/smoke_ingress.sh"

echo "recover_local_stack OK"
echo "Optional UI seed: bash infra/scripts/seed_dev_identity.sh"
