#!/usr/bin/env bash
# Pull platform broker images on the host and import into k3d (avoids node TLS/pull flakes).
set -euo pipefail

CLUSTER="${K3D_CLUSTER_NAME:-${K3D_CLUSTER:-prodavan-dev}}"
IMAGES=(
  "redis:7-alpine"
  "minio/minio:RELEASE.2024-10-02T17-50-41Z"
  "minio/mc:RELEASE.2024-10-08T09-37-26Z"
  "docker.redpanda.com/redpandadata/redpanda:v24.2.4"
)

export PATH="${HOME}/.local/bin:/usr/bin:/bin:${PATH}"

for img in "${IMAGES[@]}"; do
  echo "==> pull $img"
  docker pull "$img"
done

echo "==> k3d image import → $CLUSTER"
k3d image import "${IMAGES[@]}" -c "$CLUSTER"
echo "ok"
