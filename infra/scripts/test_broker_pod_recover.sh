#!/usr/bin/env bash
# Simulate broker/API pod failure: durable retain tests + deploy restarts + smoke.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"

recover_deploy() {
  local dep="$1"
  echo "==> rollout restart deploy/${dep}"
  kubectl -n "$NS" rollout restart "deploy/${dep}"
  kubectl -n "$NS" rollout status "deploy/${dep}" --timeout=240s
  bash "${SCRIPT_DIR}/smoke_ingress.sh"
  echo "ok deploy/${dep}"
}

echo "==> minio object retain (S3 put/get via API, not just Ready)"
bash "${SCRIPT_DIR}/verify_minio_pvc_retain.sh"

echo "==> redis PVC retain + noeviction"
bash "${SCRIPT_DIR}/verify_redis_pvc_retain.sh"

echo "==> kafka PVC retain + fsync (not just Ready)"
bash "${SCRIPT_DIR}/verify_kafka_pvc_retain.sh"

echo "==> postgres PVC retain (INSERT/SELECT)"
bash "${SCRIPT_DIR}/verify_postgres_pvc_retain.sh"

recover_deploy prodavan-api
recover_deploy prodavan-celery-worker
recover_deploy prodavan-celery-beat

echo "test_broker_pod_recover OK"
