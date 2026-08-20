#!/usr/bin/env bash
# Single entrypoint after WSL/Docker reboot: cluster → secrets/images → wait → smoke.
# Idempotent. Prefer this over ad-hoc terraform apply for runtime recover.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
export KUBECONFIG_OUT="${KUBECONFIG}"

echo "==> 1/5 ensure k3d"
bash "${SCRIPT_DIR}/ensure_k3d_cluster.sh"

if [[ -n "${GHCR_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  echo "==> 2/5 GHCR pull secret"
  bash "${SCRIPT_DIR}/create_ghcr_pull_secret.sh"
  echo "==> 3/5 import overlay images"
  bash "${SCRIPT_DIR}/import_overlay_images.sh" || echo "WARN: import_overlay_images failed"
else
  echo "==> 2/5 skip GHCR secret (set GHCR_TOKEN)"
  echo "==> 3/5 skip image import"
fi

echo "==> 4/5 wait workloads (Argo or kubectl apply -k)"
bash "${SCRIPT_DIR}/wait_prodavan_ready.sh"

echo "==> 5/5 smoke"
bash "${SCRIPT_DIR}/smoke_ingress.sh"

echo "recover_local_stack OK"
