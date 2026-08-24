#!/usr/bin/env bash
# Post-deploy acceptance: HTTP smoke + seed e2e (chat must return 200).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"

echo "==> smoke"
bash "${SCRIPT_DIR}/smoke_ingress.sh"

echo "==> kafka topics + durability knobs"
bash "${SCRIPT_DIR}/verify_kafka.sh"

echo "==> celery worker + beat"
bash "${SCRIPT_DIR}/verify_celery.sh"

echo "==> seed + e2e chat"
bash "${SCRIPT_DIR}/seed_dev_identity.sh" | tee /tmp/prodavan_verify_ui.log

grep -q 'POST chat -> 200' /tmp/prodavan_verify_ui.log \
  || { echo "FAIL: chat e2e missing in seed output" >&2; exit 1; }

echo "==> project sandbox (object-ws materialize)"
bash "${SCRIPT_DIR}/verify_project_sandbox.sh"

echo "==> sandbox Job PVC mount (I8)"
bash "${SCRIPT_DIR}/verify_sandbox_job.sh"

echo "==> API sandbox ServiceAccount token"
bash "${SCRIPT_DIR}/verify_sandbox_sa.sh"

echo "verify_touchable_ui OK — http://${SMOKE_HOST:-prodavan.local}:${HTTP_PORT:-8088}/"
