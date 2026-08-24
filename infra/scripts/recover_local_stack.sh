#!/usr/bin/env bash
# Single entrypoint after WSL/Docker reboot: cluster → images → Argo → wait → smoke → optional seed.
# Idempotent. Prefer this over ad-hoc terraform apply for runtime recover.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
export KUBECONFIG_OUT="${KUBECONFIG}"
export PATH="${HOME}/.local/bin:/usr/sbin:${PATH}"

echo "==> 1/7 ensure k3d"
bash "${SCRIPT_DIR}/ensure_k3d_cluster.sh"

echo "==> 2/7 platform broker images (public registries)"
bash "${SCRIPT_DIR}/import_platform_images_k3d.sh" || echo "WARN: platform image import failed"

if [[ -n "${GHCR_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  echo "==> 3/7 GHCR pull secret + overlay images"
  bash "${SCRIPT_DIR}/create_ghcr_pull_secret.sh"
  bash "${SCRIPT_DIR}/import_overlay_images.sh" || echo "WARN: import_overlay_images failed"
else
  echo "==> 3/7 local API/web images (no GHCR_TOKEN)"
  bash "${SCRIPT_DIR}/ensure_local_app_images_k3d.sh" || echo "WARN: local app images unavailable"
fi

echo "==> 4/7 Argo CD"
bash "${SCRIPT_DIR}/ensure_argocd.sh"

echo "==> 5/7 wait workloads"
bash "${SCRIPT_DIR}/wait_prodavan_ready.sh"

if [[ "${SKIP_SEED:-0}" != "1" && "${SEED_UI:-1}" == "1" ]]; then
  echo "==> 6-7/7 touchable UI (smoke + seed + chat)"
  bash "${SCRIPT_DIR}/verify_touchable_ui.sh"
else
  echo "==> 6/7 smoke"
  bash "${SCRIPT_DIR}/smoke_ingress.sh"
  echo "==> 7/7 UI seed skipped"
fi

echo "recover_local_stack OK"
echo "UI: http://${SMOKE_HOST:-prodavan.local}:${HTTP_PORT:-8088}/"
