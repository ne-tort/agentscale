#!/usr/bin/env bash
# I8: Job can mount API PVC. Product create stays object-ws (API does not spawn on create).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
JOB="prodavan-sandbox-probe"
API_IMAGE="${API_IMAGE:-ghcr.io/ne-tort/prodavan-api:latest}"
MANIFEST="${ROOT}/infra/k3s/base/prodavan-sandbox/probe-job.yaml"

kubectl -n "$NS" get sa prodavan-sandbox >/dev/null \
  || { echo "FAIL: ServiceAccount prodavan-sandbox missing" >&2; exit 1; }

kubectl -n "$NS" delete job "$JOB" --ignore-not-found --wait=true >/dev/null 2>&1 || true

tmp="$(mktemp)"
sed "s|IMAGE_PLACEHOLDER|${API_IMAGE}|g" "$MANIFEST" > "$tmp"
kubectl apply -f "$tmp"
rm -f "$tmp"

kubectl -n "$NS" wait --for=condition=complete "job/${JOB}" --timeout=120s
kubectl -n "$NS" logs "job/${JOB}" | grep -q 'ok pvc' \
  || { echo "FAIL: sandbox job logs missing ok pvc" >&2; kubectl -n "$NS" logs "job/${JOB}"; exit 1; }
echo "ok Job ${JOB} mounted prodavan-api-storage (create path still object-ws)"
kubectl -n "$NS" delete job "$JOB" --ignore-not-found --wait=true >/dev/null 2>&1 || true
echo "verify_sandbox_job OK"
