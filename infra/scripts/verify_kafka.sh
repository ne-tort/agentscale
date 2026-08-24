#!/usr/bin/env bash
# Kafka/Redpanda contract for local k3d: topics + durability knobs (not HA).
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

echo "==> topic replica factor (single-node: all REPLICAS=1)"
while read -r name _parts replicas; do
  [[ -n "$name" && "$name" != "NAME" ]] || continue
  [[ "$replicas" == "1" ]] || { echo "FAIL: topic ${name} replicas=${replicas} want 1" >&2; exit 1; }
done < <(rpk topic list --internal)

auto="$(rpk cluster config get auto_create_topics_enabled 2>/dev/null || true)"
echo "auto_create_topics_enabled=${auto}"
auto_norm="$(printf '%s' "$auto" | tr -d '"' | tr '[:upper:]' '[:lower:]')"
if [[ "$auto_norm" != "false" ]]; then
  echo "FAIL: auto_create_topics_enabled must be false (got ${auto})" >&2
  exit 1
fi

internal_rf="$(rpk cluster config get internal_topic_replication_factor 2>/dev/null || true)"
echo "internal_topic_replication_factor=${internal_rf}"
internal_norm="$(printf '%s' "$internal_rf" | tr -d '"' | tr -d '[:space:]')"
if [[ "$internal_norm" != "1" ]]; then
  echo "FAIL: internal_topic_replication_factor must be 1 on single-node (got ${internal_rf})" >&2
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

echo "==> fsync durability (rpk start --check=false must not sneak --unsafe-bypass-fsync=true)"
cmd="$(kubectl -n "$NS" exec "$POD" -- sh -c "tr '\\0' ' ' < /proc/1/cmdline")"
echo "cmdline=${cmd}"
if printf '%s' "$cmd" | grep -q 'unsafe-bypass-fsync=true'; then
  echo "FAIL: Redpanda PID 1 has --unsafe-bypass-fsync=true (data loss on crash)" >&2
  exit 1
fi
if ! printf '%s' "$cmd" | grep -q 'unsafe-bypass-fsync=false'; then
  echo "FAIL: Redpanda PID 1 missing --unsafe-bypass-fsync=false" >&2
  exit 1
fi
devmode="$(kubectl -n "$NS" exec "$POD" -- grep -E '^[[:space:]]*developer_mode:' /etc/redpanda/redpanda.yaml | awk '{print $2}' | tr -d '"' | head -1 || true)"
echo "developer_mode=${devmode}"
if [[ "$(printf '%s' "$devmode" | tr -d '"' | tr '[:upper:]' '[:lower:]')" != "false" ]]; then
  echo "FAIL: developer_mode must be false in node yaml (got ${devmode})" >&2
  exit 1
fi

echo "verify_kafka OK (single-node Redpanda; HA is I9)"
