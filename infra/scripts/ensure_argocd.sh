#!/usr/bin/env bash
# Idempotent Argo CD install + prodavan-dev Application.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${ROOT}/infra/.kube/prodavan-k3d.yaml"
fi

need_cmd kubectl
wait_nodes_schedulable 60

echo "==> Argo CD namespace + install (upstream stable, server-side apply)"
kubectl apply -f "${ROOT}/infra/argocd/bootstrap/namespace.yaml"

# Install once with SSA (avoids annotation-too-long). Re-apply only if CRDs incomplete.
if ! kubectl -n argocd get deploy argocd-server >/dev/null 2>&1 \
  || ! kubectl get crd applicationsets.argoproj.io >/dev/null 2>&1; then
  kubectl apply -n argocd --server-side --force-conflicts \
    -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
else
  echo "argocd already installed (deploy+CRDs present) — skip full SSA reinstall"
fi

# Preload quay/ecr into k3d only when this Docker engine owns the cluster.
if command -v k3d >/dev/null 2>&1 && command -v docker >/dev/null 2>&1 \
  && k3d cluster list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "${K3D_CLUSTER:-prodavan-dev}"; then
  echo "==> Warm Argo CD images into k3d (same Docker engine)"
  bash "${SCRIPT_DIR}/warm_argocd_images.sh"
else
  echo "==> Skip k3d image import (cluster not in this Docker engine; kubelet pulls registries)"
fi

wait_nodes_schedulable 60

echo "==> Argo git HTTP timeout / retries (I19)"
bash "${SCRIPT_DIR}/patch_argocd_git_timeout.sh"

# Upstream uses imagePullPolicy: Always — force local preload path + recreate pods.
bash "${SCRIPT_DIR}/patch_argocd_pull_policy.sh" argocd

wait_nodes_schedulable 30

echo "Waiting for argocd-server..."
kubectl -n argocd rollout status deployment/argocd-server --timeout=300s
kubectl wait --for=condition=Established crd/applications.argoproj.io --timeout=120s || true

# Controller must be up before Application status is meaningful.
echo "Waiting for argocd-application-controller..."
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s || true
kubectl -n argocd wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-application-controller --timeout=180s || true

kubectl apply -f "${ROOT}/infra/argocd/apps/"

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
