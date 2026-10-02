#!/bin/bash
# Rendered by Terraform templatefile → file provisioner (gitops_bootstrap).
set -eu
trap 'rm -f /tmp/prodavan-ghcr.token /tmp/prodavan-gitops.sh' EXIT
export KUBECONFIG='${kubeconfig_path}'
export PATH=/usr/local/bin:/usr/bin:/bin
REPO='${remote_repo_path}'
KCTL=(sudo -n /usr/local/bin/k3s kubectl)
test -d "$REPO/infra/argocd/install"
sync="$("$${KCTL[@]}" -n argocd get application agentscale-dev -o jsonpath='{.status.sync.status}' 2>/dev/null || true)"
health="$("$${KCTL[@]}" -n argocd get application agentscale-dev -o jsonpath='{.status.health.status}' 2>/dev/null || true)"
if [ "$sync" = Synced ] && [ "$health" = Healthy ]; then
  echo gitops-already-healthy
  exit 0
fi
"$${KCTL[@]}" apply -k "$REPO/infra/argocd/install"
"$${KCTL[@]}" -n argocd wait --for=condition=Available deployment/argocd-server --timeout=300s
"$${KCTL[@]}" -n argocd wait --for=condition=Available deployment/argocd-repo-server --timeout=300s
if [ -s /tmp/prodavan-ghcr.token ]; then
  "$${KCTL[@]}" -n argocd delete secret repo-agentscale --ignore-not-found
  "$${KCTL[@]}" -n argocd create secret generic repo-agentscale \
    --from-literal=type=git \
    --from-literal=url=https://github.com/ne-tort/agentscale.git \
    --from-literal=username=git \
    --from-file=password=/tmp/prodavan-ghcr.token
  "$${KCTL[@]}" -n argocd label secret repo-agentscale argocd.argoproj.io/secret-type=repository --overwrite
else
  echo 'WARN: no TF_VAR_ghcr_token - Argo cannot sync private repo'
fi
"$${KCTL[@]}" apply -k "$REPO/infra/argocd/sealed-secrets"
"$${KCTL[@]}" -n kube-system wait --for=condition=Available deployment/sealed-secrets-controller --timeout=180s
if [ -s /tmp/prodavan-ghcr.token ]; then
  # Both contours live on the same host: dev (ns agentscale-dev*) and prod
  # (ns agentscale*). Argo CreateNamespace makes the namespaces; ghcr-pull
  # secrets must exist BEFORE the first image pull.
  for ns in agentscale-dev agentscale-dev-sandboxes agentscale agentscale-sandboxes; do
    "$${KCTL[@]}" create namespace "$ns" --dry-run=client -o yaml | "$${KCTL[@]}" apply -f -
    "$${KCTL[@]}" -n "$ns" delete secret ghcr-pull --ignore-not-found
    GHCR_PASS="$(cat /tmp/prodavan-ghcr.token)"
    "$${KCTL[@]}" -n "$ns" create secret docker-registry ghcr-pull \
      --docker-server=ghcr.io \
      --docker-username='${ghcr_username}' \
      --docker-password="$GHCR_PASS"
    unset GHCR_PASS
  done
fi
"$${KCTL[@]}" apply -f "$REPO/infra/argocd/root-app.yaml"
# agent-sandbox app (sync-wave -5) must be Healthy before agentscale-dev:
# the agentscale-dev-sandboxes layer needs agent-sandbox CRDs to exist.
for _ in $(seq 1 36); do
  asb_sync="$("$${KCTL[@]}" -n argocd get application agent-sandbox -o jsonpath='{.status.sync.status}' 2>/dev/null || echo Pending)"
  asb_health="$("$${KCTL[@]}" -n argocd get application agent-sandbox -o jsonpath='{.status.health.status}' 2>/dev/null || echo Unknown)"
  echo "agent-sandbox sync=$asb_sync health=$asb_health"
  if [ "$asb_sync" = Synced ] && [ "$asb_health" = Healthy ]; then
    break
  fi
  sleep 10
done
for _ in $(seq 1 72); do
  sync="$("$${KCTL[@]}" -n argocd get application agentscale-dev -o jsonpath='{.status.sync.status}' 2>/dev/null || echo Pending)"
  health="$("$${KCTL[@]}" -n argocd get application agentscale-dev -o jsonpath='{.status.health.status}' 2>/dev/null || echo Unknown)"
  echo "agentscale-dev sync=$sync health=$health"
  if [ "$sync" = Synced ] && [ "$health" = Healthy ]; then
    exit 0
  fi
  sleep 10
done
"$${KCTL[@]}" -n argocd get applications
"$${KCTL[@]}" -n agentscale-dev get pods || true
exit 1
