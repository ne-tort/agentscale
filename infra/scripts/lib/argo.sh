# Argo Application helpers (GitOps I19: GitHub TLS from k3d).
# shellcheck shell=bash

argo_app_health() {
  kubectl -n argocd get application prodavan-dev -o jsonpath='{.status.health.status}' 2>/dev/null || echo Unknown
}

argo_app_sync() {
  kubectl -n argocd get application prodavan-dev -o jsonpath='{.status.sync.status}' 2>/dev/null || echo Unknown
}

argo_has_comparison_error() {
  kubectl -n argocd get application prodavan-dev \
    -o jsonpath='{range .status.conditions[*]}{.type}{"\n"}{end}' 2>/dev/null \
    | grep -qx ComparisonError
}

# Core workloads Ready enough to serve UI (does not require Argo Synced).
prodavan_core_ready() {
  local ns="${PRODAVAN_NS:-prodavan}" d r p
  for d in prodavan-api prodavan-web prodavan-postgres prodavan-celery-worker; do
    r="$(kubectl -n "$ns" get deploy "$d" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
    [[ "${r:-0}" -ge 1 ]] || return 1
  done
  for p in prodavan-redis-0 prodavan-minio-0 prodavan-kafka-0; do
    [[ "$(kubectl -n "$ns" get pod "$p" -o jsonpath='{.status.phase}' 2>/dev/null)" == "Running" ]] || return 1
  done
  return 0
}

# 0 = GitOps comparison OK or local-acceptable (Healthy + git ComparisonError + workloads up).
# Quiet: callers log. Use argo_app_i19_fallback to distinguish.
argo_app_i19_fallback() {
  local health sync
  health="$(argo_app_health)"
  sync="$(argo_app_sync)"
  [[ "$health" == "Healthy" ]] || return 1
  [[ "$sync" != "Synced" ]] || return 1
  argo_has_comparison_error || return 1
  prodavan_core_ready
}

argo_app_acceptable() {
  local health sync
  health="$(argo_app_health)"
  sync="$(argo_app_sync)"
  if [[ "$health" == "Healthy" && "$sync" == "Synced" ]]; then
    return 0
  fi
  argo_app_i19_fallback
}
