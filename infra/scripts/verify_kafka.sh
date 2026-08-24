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

rpk_in() {
  kubectl -n "$NS" exec -i "$POD" -- rpk "$@" -X brokers=127.0.0.1:9092
}

kubectl -n "$NS" wait --for=condition=Ready "pod/${POD}" --timeout=120s >/dev/null

echo "==> cluster info"
rpk cluster info >/dev/null
echo "ok cluster reachable"

cfg="$(rpk cluster config get write_caching_default 2>/dev/null || true)"
echo "write_caching_default=${cfg}"
norm="$(printf '%s' "$cfg" | tr -d '"' | tr '[:upper:]' '[:lower:]')"
if [[ "$norm" != "false" ]]; then
  echo "FAIL: write_caching_default must be false on local (got ${cfg})" >&2
  exit 1
fi

topics="$(rpk topic list)"
echo "$topics"
echo "$topics" | grep -q 'prodavan.platform.events' \
  || { echo "FAIL: missing topic prodavan.platform.events" >&2; exit 1; }
echo "$topics" | grep -q 'prodavan.project.triggers' \
  || { echo "FAIL: missing topic prodavan.project.triggers" >&2; exit 1; }

auto="$(rpk cluster config get auto_create_topics_enabled 2>/dev/null || true)"
echo "auto_create_topics_enabled=${auto}"
auto_norm="$(printf '%s' "$auto" | tr -d '"' | tr '[:upper:]' '[:lower:]')"
if [[ "$auto_norm" != "false" ]]; then
  echo "FAIL: auto_create_topics_enabled must be false (got ${auto})" >&2
  exit 1
fi

echo "==> produce/consume probe (prodavan.ops.health, isolated from app consumer group)"
if ! echo "$topics" | grep -q 'prodavan.ops.health'; then
  rpk topic create prodavan.ops.health -p 1 -r 1 >/dev/null 2>&1 || true
fi
PROBE="probe-$(date +%s)-$$"
ok=0
for i in 1 2 3; do
  printf '%s\n' "$PROBE" | rpk_in topic produce prodavan.ops.health >/dev/null 2>&1 || true
  got="$(rpk topic consume prodavan.ops.health --num 1 --offset -1 --format '%v' --fetch-max-wait 8s 2>/dev/null | tr -d '\r' | tail -1 || true)"
  if [[ "$got" == "$PROBE" ]]; then
    ok=1
    break
  fi
  sleep 2
done
if [[ "$ok" != "1" ]]; then
  echo "FAIL: kafka probe roundtrip got=${got} want=${PROBE}" >&2
  exit 1
fi
echo "ok produce/consume ${PROBE}"

echo "verify_kafka OK (single-node Redpanda; HA is I9)"
