#!/usr/bin/env bash
# Wait for Argo Application Healthy+Synced; refresh if stuck; apply -k only if Argo absent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS_APP="${PRODAVAN_NS:-prodavan}"
ARGO_ATTEMPTS="${ARGO_ATTEMPTS:-24}"
ARGO_SLEEP_SEC="${ARGO_SLEEP_SEC:-10}"

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/argo.sh
source "${SCRIPT_DIR}/lib/argo.sh"
ensure_kubeconfig_env
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"

apply_fallback() {
  if [[ "${ALLOW_KUSTOMIZE_FALLBACK:-0}" == "1" ]]; then
    echo "Fallback: kubectl apply -k (ALLOW_KUSTOMIZE_FALLBACK=1; Argo selfHeal may revert)"
    kubectl apply -k "${ROOT}/infra/k3s/overlays/dev"
    return 0
  fi
  echo "Argo Application not Healthy/Synced — refusing kubectl apply -k (selfHeal would fight)."
  echo "Fix: push main, kubectl -n argocd annotate application prodavan-dev argocd.argoproj.io/refresh=hard --overwrite"
  echo "Or set ALLOW_KUSTOMIZE_FALLBACK=1 for emergency local apply."
  return 1
}

refresh_argo() {
  kubectl -n argocd annotate application prodavan-dev \
    argocd.argoproj.io/refresh=hard --overwrite >/dev/null 2>&1 || true
}

wait_argo() {
  local i health sync
  if ! kubectl -n argocd get application prodavan-dev >/dev/null 2>&1; then
    echo "Application prodavan-dev missing"
    return 1
  fi
  for i in $(seq 1 "$ARGO_ATTEMPTS"); do
    uncordon_all_nodes 2>/dev/null || true
    health="$(argo_app_health)"
    sync="$(argo_app_sync)"
    echo "  [${i}/${ARGO_ATTEMPTS}] sync=${sync} health=${health}"
    if argo_app_acceptable; then
      if argo_app_i19_fallback; then
        echo "WARN: Argo ComparisonError (git TLS) — workloads Ready, not waiting for Synced (I19)"
      fi
      return 0
    fi
    if (( i == 3 || i == 8 || i == 16 )); then
      refresh_argo
    fi
    sleep "$ARGO_SLEEP_SEC"
  done
  return 1
}

echo "==> Wait Argo / optional fallback"
if kubectl -n argocd get application prodavan-dev >/dev/null 2>&1; then
  refresh_argo
  if wait_argo; then
    echo "Argo Application acceptable (Healthy+Synced or I19 git TLS + workloads Ready)"
  else
    apply_fallback
  fi
else
  echo "No Argo Application — applying overlay once (bootstrap before Argo)"
  kubectl apply -k "${ROOT}/infra/k3s/overlays/dev"
fi

echo "==> Rollout status"
if kubectl -n "$NS_APP" get pods --no-headers 2>/dev/null | grep -q ImagePullBackOff; then
  echo "==> ImagePullBackOff detected — import :local app images (dev overlay, no GHCR SHA)"
  bash "${SCRIPT_DIR}/ensure_local_app_images_k3d.sh" || echo "WARN: image rescue failed"
  kubectl -n "$NS_APP" delete pods --field-selector=status.phase=Pending --ignore-not-found || true
fi
for dep in prodavan-postgres prodavan-api prodavan-web prodavan-celery-worker; do
  if kubectl -n "$NS_APP" get deploy "$dep" >/dev/null 2>&1; then
    paused="$(kubectl -n "$NS_APP" get deploy "$dep" -o jsonpath='{.spec.paused}' 2>/dev/null || echo false)"
    if [[ "$paused" == "true" ]]; then
      echo "Resuming paused deployment/${dep}"
      kubectl -n "$NS_APP" rollout resume "deploy/${dep}" || true
    fi
    kubectl -n "$NS_APP" rollout status "deploy/${dep}" --timeout=300s || true
  fi
done
for sts in prodavan-redis prodavan-minio prodavan-kafka; do
  if kubectl -n "$NS_APP" get sts "$sts" >/dev/null 2>&1; then
    kubectl -n "$NS_APP" rollout status "sts/${sts}" --timeout=300s || true
  fi
done
kubectl -n "$NS_APP" wait --for=condition=Ready pods \
  -l 'app.kubernetes.io/part-of=prodavan,app.kubernetes.io/component!=kafka-init,app.kubernetes.io/component!=minio-init' \
  --timeout=360s 2>/dev/null \
  || kubectl -n "$NS_APP" wait --for=condition=Ready \
    -l 'app in (prodavan-api,prodavan-web,prodavan-postgres,prodavan-celery-worker)' \
    pods --timeout=120s \
  || true
# StatefulSets may not share the app= label — best-effort Ready on named pods.
for p in prodavan-redis-0 prodavan-minio-0 prodavan-kafka-0; do
  kubectl -n "$NS_APP" wait --for=condition=Ready "pod/${p}" --timeout=60s 2>/dev/null || true
done
kubectl -n "$NS_APP" get pods,pvc
