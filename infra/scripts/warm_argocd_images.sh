#!/usr/bin/env bash
# Preload Argo CD images into k3d (quay/ecr often fail kubelet-only pulls on Docker Desktop/WSL).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
IMAGES=(
  "quay.io/argoproj/argocd:v3.5.1"
  "public.ecr.aws/docker/library/redis:8.2.3-alpine"
  "ghcr.io/dexidp/dex:v2.45.0"
)

wait_docker 30

for img in "${IMAGES[@]}"; do
  echo "pull ${img}"
  docker pull "$img"
done

echo "Importing into k3d cluster ${CLUSTER}..."
k3d image import "${IMAGES[@]}" -c "$CLUSTER"
after_k3d_image_import
echo "Argo base images imported"
