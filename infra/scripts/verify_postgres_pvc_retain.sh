#!/usr/bin/env bash
# Postgres PVC retain: INSERT marker → delete pod → SELECT still present (I14).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
MARKER="persist-$(date +%s)-$$"

pod="$(kubectl -n "$NS" get pod -l app=prodavan-postgres -o jsonpath='{.items[0].metadata.name}')"
[[ -n "$pod" ]] || { echo "FAIL: no postgres pod" >&2; exit 1; }
kubectl -n "$NS" wait --for=condition=Ready "pod/${pod}" --timeout=120s >/dev/null

echo "==> INSERT ops_persist marker then delete ${pod}"
kubectl -n "$NS" exec "$pod" -- psql -U prodavan -d prodavan -v ON_ERROR_STOP=1 -c \
  "CREATE TABLE IF NOT EXISTS ops_persist (k text PRIMARY KEY, v text NOT NULL);
   INSERT INTO ops_persist(k, v) VALUES ('broker-recover', '${MARKER}')
   ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v;"

kubectl -n "$NS" delete "pod/${pod}" --wait=true
kubectl -n "$NS" wait --for=condition=Available deploy/prodavan-postgres --timeout=240s
pod="$(kubectl -n "$NS" get pod -l app=prodavan-postgres -o jsonpath='{.items[0].metadata.name}')"
kubectl -n "$NS" wait --for=condition=Ready "pod/${pod}" --timeout=120s >/dev/null

got="$(kubectl -n "$NS" exec "$pod" -- psql -U prodavan -d prodavan -At -c \
  "SELECT v FROM ops_persist WHERE k = 'broker-recover';" | tr -d '\r')"
if [[ "$got" != "$MARKER" ]]; then
  echo "FAIL: Postgres SELECT after reschedule got=${got} want=${MARKER}" >&2
  exit 1
fi
kubectl -n "$NS" exec "$pod" -- psql -U prodavan -d prodavan -c \
  "DELETE FROM ops_persist WHERE k = 'broker-recover';" >/dev/null
echo "ok Postgres PVC retained ${MARKER}"
echo "verify_postgres_pvc_retain OK"
