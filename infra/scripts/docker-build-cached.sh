#!/usr/bin/env bash
# Cached image builds for Prodavan (BuildKit local cache under .docker-cache/).
# Requires buildx driver docker-container (created automatically).
#
# Usage (from prodavan/ or any cwd):
#   bash infra/scripts/docker-build-cached.sh api|web|all|ensure-builder

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CACHE_ROOT="${ROOT}/.docker-cache"
BUILDER_NAME="${PRODAVAN_BUILDX_BUILDER:-prodavan}"

export DOCKER_BUILDKIT=1

ensure_builder() {
  if ! docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
    echo "creating buildx builder '${BUILDER_NAME}' (docker-container)..."
    docker buildx create \
      --name "${BUILDER_NAME}" \
      --driver docker-container \
      --driver-opt network=host \
      --bootstrap
  fi
  docker buildx use "${BUILDER_NAME}"
  docker buildx inspect --bootstrap >/dev/null
}

mkdir -p "${CACHE_ROOT}/api" "${CACHE_ROOT}/web"

build_api() {
  ensure_builder
  docker buildx build \
    --builder "${BUILDER_NAME}" \
    --file "${ROOT}/apps/api/Dockerfile" \
    --tag prodavan-api:latest \
    --tag ghcr.io/ne-tort/prodavan-api:latest \
    --tag prodavan-api:local \
    --tag ghcr.io/ne-tort/prodavan-api:local \
    --cache-from "type=local,src=${CACHE_ROOT}/api" \
    --cache-to "type=local,dest=${CACHE_ROOT}/api,mode=max" \
    --load \
    "${ROOT}"
}

build_web() {
  ensure_builder
  docker buildx build \
    --builder "${BUILDER_NAME}" \
    --file "${ROOT}/apps/flutter/Dockerfile" \
    --target runtime \
    --build-arg "API_BASE=${API_BASE:-http://prodavan.local:8088/api/v1}" \
    --build-arg "BUILD_ID=${BUILD_ID:-local}" \
    --tag prodavan-web:latest \
    --tag ghcr.io/ne-tort/prodavan-web:latest \
    --tag prodavan-web:local \
    --tag ghcr.io/ne-tort/prodavan-web:local \
    --cache-from "type=local,src=${CACHE_ROOT}/web" \
    --cache-to "type=local,dest=${CACHE_ROOT}/web,mode=max" \
    --load \
    "${ROOT}"
}

case "${1:-all}" in
  ensure-builder) ensure_builder ;;
  api) build_api ;;
  web) build_web ;;
  all) build_api; build_web ;;
  *)
    echo "usage: $0 [api|web|all|ensure-builder]" >&2
    exit 1
    ;;
esac
