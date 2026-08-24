#!/usr/bin/env bash
# Lengthen Argo repo-server git HTTP timeout/retries (k3d → GitHub TLS is slow).
# Idempotent. Pods pick this up on next recreate (ensure_argocd pull-policy) or explicit restart.
set -euo pipefail

NS="${ARGO_NS:-argocd}"

if ! kubectl -n "$NS" get configmap argocd-cmd-params-cm >/dev/null 2>&1; then
  echo "WARN: argocd-cmd-params-cm missing — skip git timeout patch"
  exit 0
fi

echo "==> Argo git timeout (reposerver.git.request.timeout=90s, repo RPC 120s)"
kubectl -n "$NS" patch configmap argocd-cmd-params-cm --type merge -p '{
  "data": {
    "reposerver.git.request.timeout": "90s",
    "controller.repo.server.timeout.seconds": "120"
  }
}'

if kubectl -n "$NS" get deploy argocd-repo-server >/dev/null 2>&1; then
  kubectl -n "$NS" set env deploy/argocd-repo-server \
    ARGOCD_GIT_ATTEMPTS_COUNT=10 \
    ARGOCD_EXEC_TIMEOUT=5m \
    --overwrite >/dev/null
fi

if [[ "${ARGO_GIT_TIMEOUT_RESTART:-0}" == "1" ]]; then
  kubectl -n "$NS" rollout restart deploy/argocd-repo-server 2>/dev/null || true
  kubectl -n "$NS" rollout restart sts/argocd-application-controller 2>/dev/null || true
fi

echo "ok — Argo git timeout patched"
