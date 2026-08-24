#!/usr/bin/env bash
# Kafka PVC retain: produce → delete broker pod → last record still the marker (I14/I24).
# Do not consume --num N from start: rpk waits forever if the topic has fewer than N records.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
POD="${KAFKA_POD:-prodavan-kafka-0}"

rpk() {
  kubectl -n "$NS" exec "$POD" -- rpk "$@" -X brokers=127.0.0.1:9092 -X admin.hosts=127.0.0.1:9644
}

rpk_in() {
  kubectl -n "$NS" exec -i "$POD" -- rpk "$@" -X brokers=127.0.0.1:9092 -X admin.hosts=127.0.0.1:9644
}

kubectl -n "$NS" wait --for=condition=Ready "pod/${POD}" --timeout=120s >/dev/null

rpk topic create prodavan.ops.health -p 1 -r 1 >/dev/null 2>&1 || true
MARKER="persist-$(date +%s)-$$"
echo "==> produce ${MARKER} then delete ${POD} (PVC must retain)"
printf '%s\n' "$MARKER" | rpk_in topic produce prodavan.ops.health >/dev/null

kubectl -n "$NS" delete "pod/${POD}" --wait=true
kubectl -n "$NS" wait --for=condition=Ready "pod/${POD}" --timeout=240s

echo "==> last record must still be the pre-delete marker (before other probes produce)"
got=""
for i in 1 2 3 4 5; do
  got="$(rpk topic consume prodavan.ops.health --num 1 --offset -1 --format '%v' --fetch-max-wait 8s 2>/dev/null | tr -d '\r' | tail -1 || true)"
  if [[ "$got" == "$MARKER" ]]; then
    break
  fi
  sleep 2
done
if [[ "$got" != "$MARKER" ]]; then
  echo "FAIL: last record after reschedule got=${got} want=${MARKER}" >&2
  exit 1
fi
echo "ok Kafka PVC retained ${MARKER}"

bash "${SCRIPT_DIR}/verify_kafka.sh"
echo "verify_kafka_pvc_retain OK"
