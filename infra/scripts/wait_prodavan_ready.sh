#!/usr/bin/env bash
# Wait for Argo Application Healthy+Synced; fallback to kubectl apply -k; wait rollouts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS_APP="${PRODAVAN_NS:-prodavan}"
ARGO_ATTEMPTS="${ARGO_ATTEMPTS:-60}"
ARGO_SLEEP_SEC="${ARGO_SLEEP_SEC:-10}"

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${ROOT}/infra/.kube/prodavan-k3d.yaml"
fi

apply_fallback() {
  echo "Fallback: kubectl apply -k infra/k3s/overlays/dev"
  kubectl apply -k "${ROOT}/infra/k3s/overlays/dev"
}

wait_argo() {
  local i health sync
  if ! kubectl -n argocd get application prodavan-dev >/dev/null 2>&1; then
    echo "Application prodavan-dev missing"
    return 1
  fi
  for i in $(seq 1 "$ARGO_ATTEMPTS"); do
    health="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.status.health.status}' 2>/dev/null || echo Unknown)"
    sync="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.status.sync.status}' 2>/dev/null || echo Unknown)"
    echo "  [${i}/${ARGO_ATTEMPTS}] sync=${sync} health=${health}"
    if [[ "$health" == "Healthy" && "$sync" == "Synced" ]]; then
      return 0
    fi
    sleep "$ARGO_SLEEP_SEC"
  done
  return 1
}

echo "==> Wait Argo / apply overlay"
if wait_argo; then
  echo "Argo Application Healthy+Synced"
else
  apply_fallback
fi

echo "==> Rollout status"
kubectl -n "$NS_APP" rollout status deploy/prodavan-postgres --timeout=300s || true
kubectl -n "$NS_APP" rollout status deploy/prodavan-api --timeout=300s
kubectl -n "$NS_APP" rollout status deploy/prodavan-web --timeout=300s
kubectl -n "$NS_APP" get pods
