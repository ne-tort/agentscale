#!/bin/bash
# Rendered by Terraform templatefile → file provisioner (gitops_bootstrap).
set -eu
trap 'rm -f /tmp/prodavan-ghcr.token /tmp/prodavan-gitops.sh' EXIT
export KUBECONFIG='${kubeconfig_path}'
export PATH=/usr/local/bin:/usr/bin:/bin
REPO='${remote_repo_path}'
KCTL=(sudo -n /usr/local/bin/k3s kubectl)
test -d "$REPO/infra/argocd/install"
sync="$("$${KCTL[@]}" -n argocd get application prodavan-dev -o jsonpath='{.status.sync.status}' 2>/dev/null || true)"
health="$("$${KCTL[@]}" -n argocd get application prodavan-dev -o jsonpath='{.status.health.status}' 2>/dev/null || true)"
if [ "$sync" = Synced ] && [ "$health" = Healthy ]; then
  echo gitops-already-healthy
  exit 0
fi
"$${KCTL[@]}" apply -k "$REPO/infra/argocd/install"
"$${KCTL[@]}" -n argocd wait --for=condition=Available deployment/argocd-server --timeout=300s
"$${KCTL[@]}" -n argocd wait --for=condition=Available deployment/argocd-repo-server --timeout=300s
if [ -s /tmp/prodavan-ghcr.token ]; then
  "$${KCTL[@]}" -n argocd delete secret repo-prodavan --ignore-not-found
  "$${KCTL[@]}" -n argocd create secret generic repo-prodavan \
    --from-literal=type=git \
    --from-literal=url=https://github.com/ne-tort/prodavan.git \
    --from-literal=username=git \
    --from-file=password=/tmp/prodavan-ghcr.token
  "$${KCTL[@]}" -n argocd label secret repo-prodavan argocd.argoproj.io/secret-type=repository --overwrite
else
  echo 'WARN: no TF_VAR_ghcr_token - Argo cannot sync private repo'
fi
"$${KCTL[@]}" apply -k "$REPO/infra/argocd/sealed-secrets"
"$${KCTL[@]}" -n kube-system wait --for=condition=Available deployment/sealed-secrets-controller --timeout=180s
if [ -s /tmp/prodavan-ghcr.token ]; then
  "$${KCTL[@]}" create namespace prodavan --dry-run=client -o yaml | "$${KCTL[@]}" apply -f -
  "$${KCTL[@]}" create namespace prodavan-sandboxes --dry-run=client -o yaml | "$${KCTL[@]}" apply -f -
  "$${KCTL[@]}" -n prodavan delete secret ghcr-pull --ignore-not-found
  "$${KCTL[@]}" -n prodavan-sandboxes delete secret ghcr-pull --ignore-not-found
  GHCR_PASS="$(cat /tmp/prodavan-ghcr.token)"
  "$${KCTL[@]}" -n prodavan create secret docker-registry ghcr-pull \
    --docker-server=ghcr.io \
    --docker-username='${ghcr_username}' \
    --docker-password="$GHCR_PASS"
  "$${KCTL[@]}" -n prodavan-sandboxes create secret docker-registry ghcr-pull \
    --docker-server=ghcr.io \
    --docker-username='${ghcr_username}' \
    --docker-password="$GHCR_PASS"
  unset GHCR_PASS
fi
"$${KCTL[@]}" apply -f "$REPO/infra/argocd/root-app.yaml"
for _ in $(seq 1 72); do
  sync="$("$${KCTL[@]}" -n argocd get application prodavan-dev -o jsonpath='{.status.sync.status}' 2>/dev/null || echo Pending)"
  health="$("$${KCTL[@]}" -n argocd get application prodavan-dev -o jsonpath='{.status.health.status}' 2>/dev/null || echo Unknown)"
  echo "prodavan-dev sync=$sync health=$health"
  if [ "$sync" = Synced ] && [ "$health" = Healthy ]; then
    exit 0
  fi
  sleep 10
done
"$${KCTL[@]}" -n argocd get applications
"$${KCTL[@]}" -n prodavan get pods || true
exit 1
