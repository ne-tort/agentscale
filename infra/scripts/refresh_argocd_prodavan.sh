#!/usr/bin/env bash
# Hard refresh Argo Application prodavan-dev and wait for Healthy+Synced.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
ATTEMPTS="${ARGO_ATTEMPTS:-60}"
SLEEP_SEC="${ARGO_SLEEP_SEC:-10}"

need_cmd kubectl

if [[ "${RESTORE_SELF_HEAL:-1}" == "1" ]]; then
  kubectl -n argocd patch application prodavan-dev --type merge --patch-file "${SCRIPT_DIR}/_patch_argo_selfheal_on.yaml" \
    || kubectl -n argocd patch application prodavan-dev --type merge -p '{"spec":{"syncPolicy":{"automated":{"selfHeal":true,"prune":true}}}}'
fi

kubectl -n argocd annotate application prodavan-dev argocd.argoproj.io/refresh=hard --overwrite >/dev/null

for i in $(seq 1 "$ATTEMPTS"); do
  health="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.status.health.status}' 2>/dev/null || echo Unknown)"
  sync="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.status.sync.status}' 2>/dev/null || echo Unknown)"
  echo "  [${i}/${ATTEMPTS}] sync=${sync} health=${health}"
  if [[ "$health" == "Healthy" && "$sync" == "Synced" ]]; then
    echo "Argo prodavan-dev OK"
    exit 0
  fi
  sleep "$SLEEP_SEC"
done

kubectl -n argocd get application prodavan-dev -o yaml | tail -40
exit 1
