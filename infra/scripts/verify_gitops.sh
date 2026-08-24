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
proj="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.spec.project}')"
echo "project=${proj} targetRevision=${rev} path=${path}"
[[ "$path" == "infra/k3s/overlays/dev" ]] || { echo "FAIL: unexpected Application path ${path}" >&2; exit 1; }
[[ "$proj" == "prodavan" ]] || { echo "FAIL: Application project must be prodavan not ${proj:-empty}" >&2; exit 1; }

imgs="$(kubectl -n argocd get application prodavan-dev -o jsonpath='{.spec.source.kustomize.images}')"
echo "kustomize.images=${imgs}"
printf '%s' "$imgs" | grep -q 'prodavan-api:local' \
  || { echo "FAIL: Application must force ghcr.io/ne-tort/prodavan-api:local (I18)" >&2; exit 1; }
printf '%s' "$imgs" | grep -q 'prodavan-web:local' \
  || { echo "FAIL: Application must force ghcr.io/ne-tort/prodavan-web:local (I18)" >&2; exit 1; }

ns="$(kubectl -n argocd get appproject prodavan -o jsonpath='{.spec.destinations[0].namespace}')"
repo="$(kubectl -n argocd get appproject prodavan -o jsonpath='{.spec.sourceRepos[0]}')"
echo "AppProject dest.namespace=${ns} sourceRepos=${repo}"
[[ "$ns" == "prodavan" ]] || { echo "FAIL: AppProject destination namespace ${ns}" >&2; exit 1; }
[[ "$repo" == "https://github.com/ne-tort/prodavan.git" ]] \
  || { echo "FAIL: AppProject sourceRepos ${repo}" >&2; exit 1; }

whitelist="$(kubectl -n argocd get appproject prodavan -o jsonpath='{.spec.namespaceResourceWhitelist[*].kind}')"
echo "namespaceResourceWhitelist=${whitelist}"
printf '%s' "$whitelist" | grep -q 'StatefulSet' \
  || { echo "FAIL: AppProject missing namespaceResourceWhitelist (must pin kinds)" >&2; exit 1; }
printf '%s' "$whitelist" | grep -q 'Ingress' \
  || { echo "FAIL: AppProject whitelist missing Ingress" >&2; exit 1; }

orphaned="$(kubectl -n argocd get appproject prodavan -o jsonpath='{.spec.orphanedResources.warn}')"
echo "AppProject orphanedResources.warn=${orphaned}"
[[ "$orphaned" == "true" ]] || { echo "FAIL: AppProject orphanedResources.warn must be true" >&2; exit 1; }

echo "verify_gitops OK"
