#!/usr/bin/env bash
# Verify Celery worker + beat runtime contracts in k3s.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"

kubectl -n "$NS" rollout status deploy/prodavan-celery-worker --timeout=180s >/dev/null
kubectl -n "$NS" rollout status deploy/prodavan-celery-beat --timeout=180s >/dev/null

echo "==> celery worker inspect ping"
kubectl -n "$NS" exec deploy/prodavan-celery-worker -- \
  python -m celery -A prodavan.core.infra.worker_manager.celery_app inspect ping --timeout 8 >/dev/null
echo "ok worker ping"

echo "==> celery beat process"
kubectl -n "$NS" exec deploy/prodavan-celery-beat -- \
  python -c "import pathlib; data=pathlib.Path('/proc/1/cmdline').read_text(); assert 'celery' in data and 'beat' in data, data; print('ok beat cmdline')"

echo "verify_celery OK"
