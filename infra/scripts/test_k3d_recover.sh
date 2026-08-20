#!/usr/bin/env bash
# Integration test: ensure → stop → ensure recovers → nodes Ready.
# With TEST_WORKLOADS=1 also waits rollouts + smoke (needs images already in cluster).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
export KUBECONFIG_OUT="${ROOT}/infra/.kube/prodavan-k3d.yaml"
export KUBECONFIG="$KUBECONFIG_OUT"
# Faster test path: base images already imported
export WARM_K3D_IMAGES="${WARM_K3D_IMAGES:-0}"

echo "TEST 1: ensure_k3d_cluster (create or start)"
bash "${SCRIPT_DIR}/ensure_k3d_cluster.sh"

echo "TEST 2: stop cluster (simulate reboot / docker stop)"
k3d cluster stop "$CLUSTER"

if docker ps --filter "name=k3d-${CLUSTER}-server" --filter "status=running" --format '{{.ID}}' | grep -q .; then
  echo "FAIL: server still running after stop" >&2
  exit 1
fi
echo "cluster stopped OK"

echo "TEST 3: ensure recovers"
bash "${SCRIPT_DIR}/ensure_k3d_cluster.sh"

kubectl get nodes
kubectl wait --for=condition=Ready nodes --all --timeout=60s
echo "PASS: k3d recover after stop"

if [[ "${TEST_WORKLOADS:-0}" == "1" ]]; then
  echo "TEST 4: workloads + smoke"
  bash "${SCRIPT_DIR}/wait_prodavan_ready.sh"
  bash "${SCRIPT_DIR}/smoke_ingress.sh"
  echo "PASS: workloads after recover"
fi
