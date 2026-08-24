#!/usr/bin/env bash
# Local GitOps contract for Application prodavan-dev (not a full Argo audit).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"

need() { command -v "$1" >/dev/null || { echo "FAIL: missing $1" >&2; exit 1; }; }
need kubectl

opts="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.spec.syncPolicy.syncOptions}' 2>/dev/null || true)"
echo "syncOptions=${opts}"
printf '%s' "$opts" | grep -q 'RespectIgnoreDifferences=true' \
  || { echo "FAIL: Application missing RespectIgnoreDifferences=true" >&2; exit 1; }
if printf '%s' "$opts" | grep -q 'ApplyOutOfSyncOnly'; then
  echo "FAIL: ApplyOutOfSyncOnly skips probe/config patches (I15)" >&2
  exit 1
fi

prune="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.spec.syncPolicy.automated.prune}')"
heal="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.spec.syncPolicy.automated.selfHeal}')"
echo "prune=${prune} selfHeal=${heal}"
[[ "$prune" == "false" ]] || { echo "FAIL: local prune must be false (I19)" >&2; exit 1; }
[[ "$heal" == "true" ]] || { echo "FAIL: selfHeal must be true" >&2; exit 1; }

rev="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.spec.source.targetRevision}')"
path="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.spec.source.path}')"
echo "targetRevision=${rev} path=${path}"
[[ "$path" == "infra/k3s/overlays/dev" ]] || { echo "FAIL: unexpected Application path ${path}" >&2; exit 1; }

echo "verify_gitops OK"
