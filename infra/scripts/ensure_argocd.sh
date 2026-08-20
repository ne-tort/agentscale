#!/usr/bin/env bash
# Idempotent Argo CD install + prodavan-dev Application.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${ROOT}/infra/.kube/prodavan-k3d.yaml"
fi

need_kubectl() {
  command -v kubectl >/dev/null 2>&1 || {
    echo "kubectl required" >&2
    exit 1
  }
}

need_kubectl

echo "==> Argo CD namespace + install (upstream stable)"
kubectl apply -f "${ROOT}/infra/argocd/bootstrap/namespace.yaml"

if ! kubectl -n argocd get deploy argocd-server >/dev/null 2>&1; then
  kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
else
  echo "argocd-server already present — skip full reinstall"
fi

echo "Waiting for argocd-server..."
kubectl -n argocd rollout status deployment/argocd-server --timeout=300s
kubectl wait --for=condition=Established crd/applications.argoproj.io --timeout=120s || true

kubectl apply -f "${ROOT}/infra/argocd/apps/prodavan-dev.yaml"

if [[ -n "${ARGOCD_REPO_TOKEN:-}" ]]; then
  echo "==> Argo CD repository credentials"
  kubectl -n argocd create secret generic repo-prodavan \
    --from-literal=type=git \
    --from-literal=url=https://github.com/ne-tort/prodavan.git \
    --from-literal=password="$ARGOCD_REPO_TOKEN" \
    --from-literal=username="${ARGOCD_REPO_USERNAME:-git}" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n argocd label secret repo-prodavan argocd.argoproj.io/secret-type=repository --overwrite
fi

echo "Applied Application prodavan-dev"
