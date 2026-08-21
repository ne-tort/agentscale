#!/usr/bin/env bash
# GitOps layer only (Argo + GHCR + wait + smoke). Cluster must already exist.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
export PATH="${HOME}/.local/bin:/usr/sbin:${PATH}"

wait_docker 30
wait_nodes_schedulable 60

if [[ -n "${GHCR_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  echo "==> GHCR pull secret"
  bash "${SCRIPT_DIR}/create_ghcr_pull_secret.sh"
  echo "==> Import overlay images (k3d preload)"
  bash "${SCRIPT_DIR}/import_overlay_images.sh" || echo "WARN: import incomplete"
  wait_nodes_schedulable 60
else
  echo "WARN: GHCR_TOKEN unset — private image pulls may fail"
fi

echo "==> Argo CD + Application"
bash "${SCRIPT_DIR}/ensure_argocd.sh"

echo "==> Wait workloads"
bash "${SCRIPT_DIR}/wait_prodavan_ready.sh"

echo "==> Smoke"
bash "${SCRIPT_DIR}/smoke_ingress.sh"

echo "GitOps bootstrap OK (KUBECONFIG=${KUBECONFIG})"
