#!/usr/bin/env bash
# Pull base images on the Docker host (with retries) and import into k3d.
# Helps when kubelet TLS to docker.io is flaky after WSL/Desktop restarts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
RETRIES="${IMAGE_PULL_RETRIES:-5}"

IMAGES=(
  "rancher/mirrored-pause:3.6"
  "rancher/local-path-provisioner:v0.0.26"
  "rancher/mirrored-coredns-coredns:1.10.1"
  "rancher/mirrored-metrics-server:v0.7.0"
  "rancher/klipper-helm:v0.8.3-build20240228"
  "rancher/mirrored-library-traefik:2.10.7"
  "rancher/mirrored-library-busybox:1.36.1"
  "rancher/klipper-lb:v0.4.7"
  "postgres:16"
)

pull_one() {
  local img="$1" i
  for i in $(seq 1 "$RETRIES"); do
    echo "pull ${img} (attempt ${i}/${RETRIES})"
    if docker pull "$img"; then
      return 0
    fi
    sleep $((i * 3))
  done
  echo "WARN: failed to pull ${img}" >&2
  return 1
}

main() {
  need_cmd docker
  need_cmd k3d
  wait_docker 30
  local ok=()
  local img
  for img in "${IMAGES[@]}"; do
    if pull_one "$img"; then
      ok+=("$img")
    fi
  done
  if [[ ${#ok[@]} -eq 0 ]]; then
    die "no images pulled"
  fi
  echo "Importing ${#ok[@]} image(s) into k3d cluster ${CLUSTER}..."
  k3d image import "${ok[@]}" -c "$CLUSTER"
  after_k3d_image_import
  echo "warm images done"
}

main "$@"
