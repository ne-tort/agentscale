#!/usr/bin/env bash
# Build (optional) + import API/web images into k3d for local GitOps without GHCR pull.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export PATH="${HOME}/.local/bin:/usr/bin:/bin:${PATH}"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
API_BASE="${API_BASE:-http://prodavan.local:8088/api/v1}"

if [[ "${BUILD:-1}" == "1" ]]; then
  echo "==> build web (API_BASE=${API_BASE})"
  # Prefer plain docker build when buildx credential helper breaks under WSL.
  if docker buildx version >/dev/null 2>&1 && docker buildx inspect prodavan >/dev/null 2>&1; then
    API_BASE="$API_BASE" bash "${SCRIPT_DIR}/docker-build-cached.sh" web || \
      docker build -f "${ROOT}/apps/flutter/Dockerfile" --target runtime \
        --build-arg "API_BASE=${API_BASE}" \
        -t prodavan-web:local -t ghcr.io/ne-tort/prodavan-web:latest "${ROOT}"
  else
    docker build -f "${ROOT}/apps/flutter/Dockerfile" --target runtime \
      --build-arg "API_BASE=${API_BASE}" \
      -t prodavan-web:local -t ghcr.io/ne-tort/prodavan-web:latest "${ROOT}"
  fi
fi

IMAGES=()
for img in ghcr.io/ne-tort/prodavan-web:latest ghcr.io/ne-tort/prodavan-api:latest \
           prodavan-web:local prodavan-api:local; do
  if docker image inspect "$img" >/dev/null 2>&1; then
    # Prefer ghcr tags for kustomize image names.
    case "$img" in
      prodavan-web:local) docker tag prodavan-web:local ghcr.io/ne-tort/prodavan-web:latest ;;
      prodavan-api:local) docker tag prodavan-api:local ghcr.io/ne-tort/prodavan-api:latest ;;
    esac
  fi
done

for img in ghcr.io/ne-tort/prodavan-web:latest ghcr.io/ne-tort/prodavan-api:latest; do
  if docker image inspect "$img" >/dev/null 2>&1; then
    IMAGES+=("$img")
  fi
done

[[ ${#IMAGES[@]} -gt 0 ]] || { echo "no local API/web images to import"; exit 1; }

echo "==> k3d image import → ${CLUSTER}: ${IMAGES[*]}"
k3d image import "${IMAGES[@]}" -c "$CLUSTER"

export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
kubectl -n prodavan rollout restart deploy/prodavan-web deploy/prodavan-api || true
kubectl -n prodavan rollout status deploy/prodavan-web --timeout=180s || true
echo "ok — web/api reloaded from local images"
