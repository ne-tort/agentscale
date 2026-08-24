#!/usr/bin/env bash
# Redis AOF: SET → delete pod → GET (preStop SHUTDOWN SAVE + appendonly).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
POD="${REDIS_POD:-prodavan-redis-0}"
KEY="prodavan:ops:persist"

kubectl -n "$NS" wait --for=condition=Ready "pod/${POD}" --timeout=120s >/dev/null

policy="$(kubectl -n "$NS" exec "$POD" -- redis-cli CONFIG GET maxmemory-policy | tail -1 | tr -d '\r')"
echo "maxmemory-policy=${policy}"
[[ "$policy" == "noeviction" ]] \
  || { echo "FAIL: Redis maxmemory-policy must be noeviction for Celery (got ${policy})" >&2; exit 1; }

aof="$(kubectl -n "$NS" exec "$POD" -- redis-cli CONFIG GET appendonly | tail -1 | tr -d '\r')"
echo "appendonly=${aof}"
[[ "$aof" == "yes" ]] || { echo "FAIL: Redis appendonly must be yes (got ${aof})" >&2; exit 1; }

MARKER="persist-$(date +%s)-$$"
echo "==> SET ${KEY}=${MARKER} then delete ${POD}"
kubectl -n "$NS" exec "$POD" -- redis-cli SET "$KEY" "$MARKER" >/dev/null

kubectl -n "$NS" delete "pod/${POD}" --wait=true
kubectl -n "$NS" wait --for=condition=Ready "pod/${POD}" --timeout=180s

got="$(kubectl -n "$NS" exec "$POD" -- redis-cli GET "$KEY" | tr -d '\r')"
if [[ "$got" != "$MARKER" ]]; then
  echo "FAIL: Redis GET ${KEY} after reschedule got=${got} want=${MARKER}" >&2
  exit 1
fi
kubectl -n "$NS" exec "$POD" -- redis-cli DEL "$KEY" >/dev/null
echo "ok Redis PVC retained ${MARKER}"
echo "verify_redis_pvc_retain OK"
