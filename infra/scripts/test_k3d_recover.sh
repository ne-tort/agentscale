#!/usr/bin/env bash
# Integration test: ensure → stop → ensure recovers → nodes Ready.
# Does not require Argo/images (cluster-only).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
export KUBECONFIG_OUT="${ROOT}/infra/.kube/prodavan-k3d.yaml"
export KUBECONFIG="$KUBECONFIG_OUT"

echo "TEST 1: ensure_k3d_cluster (create or start)"
bash "${SCRIPT_DIR}/ensure_k3d_cluster.sh"

echo "TEST 2: stop cluster (simulate reboot / docker stop)"
k3d cluster stop "$CLUSTER"

if docker ps --filter "label=k3d.cluster=${CLUSTER}" --filter "label=k3d.role=server" --format '{{.ID}}' | grep -q .; then
  echo "FAIL: server still running after stop" >&2
  exit 1
fi
echo "cluster stopped OK"

echo "TEST 3: ensure recovers"
bash "${SCRIPT_DIR}/ensure_k3d_cluster.sh"

kubectl get nodes
kubectl wait --for=condition=Ready nodes --all --timeout=60s
echo "PASS: k3d recover after stop"
