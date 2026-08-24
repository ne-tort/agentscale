#!/usr/bin/env bash
# Kafka/Redpanda contract for local k3d: topics + durability knobs (not HA).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
POD="${KAFKA_POD:-prodavan-kafka-0}"

rpk() {
  kubectl -n "$NS" exec "$POD" -- rpk "$@" -X brokers=127.0.0.1:9092
}

kubectl -n "$NS" wait --for=condition=Ready "pod/${POD}" --timeout=120s >/dev/null

echo "==> cluster info"
rpk cluster info >/dev/null
echo "ok cluster reachable"

cfg="$(rpk cluster config get write_caching_default 2>/dev/null || true)"
echo "write_caching_default=${cfg}"
if echo "$cfg" | grep -qi 'true'; then
  echo "FAIL: write_caching_default must be false on local (durability)" >&2
  exit 1
fi

topics="$(rpk topic list)"
echo "$topics"
echo "$topics" | grep -q 'prodavan.platform.events' \
  || { echo "FAIL: missing topic prodavan.platform.events" >&2; exit 1; }
echo "$topics" | grep -q 'prodavan.project.triggers' \
  || { echo "FAIL: missing topic prodavan.project.triggers" >&2; exit 1; }

echo "verify_kafka OK (single-node Redpanda; HA is I9)"
