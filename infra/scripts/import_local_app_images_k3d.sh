#!/usr/bin/env bash
# Build (optional) + import API/web images into k3d for local GitOps without GHCR pull.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export PATH="${HOME}/.local/bin:/usr/bin:/bin:${PATH}"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
API_BASE="${API_BASE:-http://prodavan.local:8088/api/v1}"
API_IMAGE="${API_IMAGE:-ghcr.io/ne-tort/prodavan-api:latest}"
WEB_IMAGE="${WEB_IMAGE:-ghcr.io/ne-tort/prodavan-web:latest}"

if [[ "${BUILD:-1}" == "1" ]]; then
  echo "==> build api"
  build_ok=0
  if docker buildx version >/dev/null 2>&1 && docker buildx inspect prodavan >/dev/null 2>&1; then
    bash "${SCRIPT_DIR}/docker-build-cached.sh" api && build_ok=1 || true
  fi
  if [[ "$build_ok" != "1" ]]; then
    docker build -f "${ROOT}/apps/api/Dockerfile" \
      -t prodavan-api:latest -t prodavan-api:local -t "${API_IMAGE}" "${ROOT}" \
      || bash "${SCRIPT_DIR}/bridge_docker_desktop_image.sh" "${API_IMAGE}" || true
  fi

  echo "==> build web (API_BASE=${API_BASE})"
  if docker buildx version >/dev/null 2>&1 && docker buildx inspect prodavan >/dev/null 2>&1; then
    API_BASE="$API_BASE" bash "${SCRIPT_DIR}/docker-build-cached.sh" web || \
      docker build -f "${ROOT}/apps/flutter/Dockerfile" --target runtime \
        --build-arg "API_BASE=${API_BASE}" \
        -t prodavan-web:latest -t prodavan-web:local -t "${WEB_IMAGE}" "${ROOT}"
  else
    docker build -f "${ROOT}/apps/flutter/Dockerfile" --target runtime \
      --build-arg "API_BASE=${API_BASE}" \
      -t prodavan-web:latest -t prodavan-web:local -t "${WEB_IMAGE}" "${ROOT}"
  fi
fi

# Promote legacy :local Desktop tags to overlay :latest.
for src in ghcr.io/ne-tort/prodavan-api:local prodavan-api:local; do
  if docker image inspect "$src" >/dev/null 2>&1; then
    docker tag "$src" "${API_IMAGE}"
    docker tag "$src" prodavan-api:latest || true
  fi
done
for src in ghcr.io/ne-tort/prodavan-web:local prodavan-web:local; do
  if docker image inspect "$src" >/dev/null 2>&1; then
    docker tag "$src" "${WEB_IMAGE}"
    docker tag "$src" prodavan-web:latest || true
  fi
done

IMAGES=()
for img in "${WEB_IMAGE}" "${API_IMAGE}"; do
  if docker image inspect "$img" >/dev/null 2>&1; then
    IMAGES+=("$img")
  fi
done

[[ ${#IMAGES[@]} -gt 0 ]] || { echo "no local API/web images to import"; exit 1; }

for img in "${IMAGES[@]}"; do
  bash "${SCRIPT_DIR}/bridge_docker_desktop_image.sh" "$img" || true
done

DESKTOP_TAR="${DESKTOP_IMAGE_TAR:-/mnt/c/Temp/prodavan-docker-bridge.tar}"
if [[ "${VERIFY_ALEMBIC:-1}" == "1" ]]; then
  if ! API_IMAGE="$API_IMAGE" bash "${SCRIPT_DIR}/verify_api_image_alembic.sh"; then
    if [[ -f "$DESKTOP_TAR" ]]; then
      echo "==> stale app image — reload ${DESKTOP_TAR}"
      docker rmi "${API_IMAGE}" "${WEB_IMAGE}" 2>/dev/null || true
      docker load -i "$DESKTOP_TAR"
      IMAGES=("${WEB_IMAGE}" "${API_IMAGE}")
      API_IMAGE="$API_IMAGE" bash "${SCRIPT_DIR}/verify_api_image_alembic.sh"
    else
      exit 1
    fi
  fi
fi

echo "==> k3d image import → ${CLUSTER}: ${IMAGES[*]}"
k3d image import "${IMAGES[@]}" -c "$CLUSTER"

export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
if [[ "${RESTART_DEPLOYS:-1}" == "1" ]]; then
  kubectl -n prodavan rollout restart deploy/prodavan-web deploy/prodavan-api deploy/prodavan-celery-worker || true
  kubectl -n prodavan rollout status deploy/prodavan-api --timeout=300s || true
  kubectl -n prodavan rollout status deploy/prodavan-web --timeout=180s || true
fi
echo "ok — web/api/celery use ${API_IMAGE} / ${WEB_IMAGE}"
