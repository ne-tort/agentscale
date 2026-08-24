#!/usr/bin/env bash
# Hard refresh Argo Application prodavan-dev and wait for Healthy+Synced.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/argo.sh
source "${SCRIPT_DIR}/lib/argo.sh"

export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
ATTEMPTS="${ARGO_ATTEMPTS:-24}"
SLEEP_SEC="${ARGO_SLEEP_SEC:-10}"

need_cmd kubectl

echo "==> apply AppProject + Application CR (syncOptions / ignoreDifferences from git)"
kubectl apply -f "${ROOT}/infra/argocd/apps/"

if [[ "${RESTORE_SELF_HEAL:-1}" == "1" ]]; then
  kubectl -n argocd patch application prodavan-dev --type merge --patch-file "${SCRIPT_DIR}/_patch_argo_selfheal_on.yaml" \
    || kubectl -n argocd patch application prodavan-dev --type merge -p '{"spec":{"syncPolicy":{"automated":{"selfHeal":true,"prune":false}}}}'
fi

kubectl -n argocd annotate application prodavan-dev argocd.argoproj.io/refresh=hard --overwrite >/dev/null

for i in $(seq 1 "$ATTEMPTS"); do
  health="$(argo_app_health)"
  sync="$(argo_app_sync)"
  echo "  [${i}/${ATTEMPTS}] sync=${sync} health=${health}"
  if argo_app_acceptable; then
    if argo_app_i19_fallback; then
      echo "WARN: Argo ComparisonError (git TLS) — workloads Ready (I19)"
    fi
    echo "Argo prodavan-dev OK (sync=${sync} health=${health})"
    exit 0
  fi
  if (( i == 3 || i == 8 || i == 16 )); then
    kubectl -n argocd annotate application prodavan-dev argocd.argoproj.io/refresh=hard --overwrite >/dev/null 2>&1 || true
  fi
  sleep "$SLEEP_SEC"
done

kubectl -n argocd get application prodavan-dev -o yaml | tail -40
exit 1
