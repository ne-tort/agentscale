#!/usr/bin/env bash
# Simulate broker/API pod failure: delete pod → wait Ready → smoke.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"

recover_pod() {
  local pod="$1"
  echo "==> delete pod/${pod} (expect reschedule + PVC retain)"
  kubectl -n "$NS" delete "pod/${pod}" --ignore-not-found --wait=false
  kubectl -n "$NS" wait --for=condition=Ready "pod/${pod}" --timeout=240s
  bash "${SCRIPT_DIR}/smoke_ingress.sh"
  echo "ok pod/${pod}"
}

recover_deploy() {
  local dep="$1"
  echo "==> rollout restart deploy/${dep}"
  kubectl -n "$NS" rollout restart "deploy/${dep}"
  kubectl -n "$NS" rollout status "deploy/${dep}" --timeout=240s
  bash "${SCRIPT_DIR}/smoke_ingress.sh"
  echo "ok deploy/${dep}"
}

for p in prodavan-redis-0 prodavan-minio-0; do
  recover_pod "$p"
done

echo "==> kafka PVC retain + fsync (not just Ready)"
bash "${SCRIPT_DIR}/verify_kafka_pvc_retain.sh"

recover_deploy prodavan-postgres
recover_deploy prodavan-api
recover_deploy prodavan-celery-worker
recover_deploy prodavan-celery-beat

echo "test_broker_pod_recover OK"
