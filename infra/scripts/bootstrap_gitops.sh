#!/usr/bin/env bash
# GitOps layer only (Argo + images + wait + smoke + optional UI seed). Cluster must already exist.
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
  echo "==> Local app images (no GHCR_TOKEN)"
  bash "${SCRIPT_DIR}/ensure_local_app_images_k3d.sh"
fi

echo "==> Argo CD + Application"
bash "${SCRIPT_DIR}/ensure_argocd.sh"

echo "==> Wait workloads"
bash "${SCRIPT_DIR}/wait_prodavan_ready.sh"

echo "==> Argo hard refresh (pick up latest main manifests)"
bash "${SCRIPT_DIR}/refresh_argocd_prodavan.sh" || echo "WARN: argo refresh failed — continuing"

if [[ "${SKIP_SEED:-0}" != "1" && "${SEED_UI:-1}" == "1" ]]; then
  echo "==> Touchable UI (smoke + seed + chat e2e)"
  bash "${SCRIPT_DIR}/verify_touchable_ui.sh"
else
  echo "==> Smoke"
  bash "${SCRIPT_DIR}/smoke_ingress.sh"
  echo "UI seed skipped (SEED_UI=${SEED_UI:-0} SKIP_SEED=${SKIP_SEED:-0})"
fi

echo "GitOps bootstrap OK (KUBECONFIG=${KUBECONFIG})"
echo "UI: http://${SMOKE_HOST:-prodavan.local}:${HTTP_PORT:-8088}/"
