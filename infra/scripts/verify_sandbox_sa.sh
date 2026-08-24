#!/usr/bin/env bash
# I8: API runs as prodavan-sandbox and has an in-cluster token (spawn still off).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"

sa="$(kubectl -n "$NS" get deploy prodavan-api -o jsonpath='{.spec.template.spec.serviceAccountName}')"
if [[ "$sa" != "prodavan-sandbox" ]]; then
  echo "FAIL: prodavan-api SA is ${sa:-empty}, want prodavan-sandbox" >&2
  exit 1
fi

pod="$(kubectl -n "$NS" get pod -l app=prodavan-api -o jsonpath='{.items[0].metadata.name}')"
[[ -n "$pod" ]] || { echo "FAIL: no prodavan-api pod" >&2; exit 1; }

kubectl -n "$NS" exec "$pod" -- test -s /var/run/secrets/kubernetes.io/serviceaccount/token \
  || { echo "FAIL: API pod missing ServiceAccount token" >&2; exit 1; }

echo "ok API pod ${pod} SA=prodavan-sandbox token mounted (SANDBOX_K8S_JOBS still off — object-ws create)"
echo "verify_sandbox_sa OK"
