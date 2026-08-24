#!/usr/bin/env bash
# Ensure API/web images exist (build if needed) and import into k3d when GHCR is unavailable.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export PATH="${HOME}/.local/bin:/usr/bin:/bin:${PATH}"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
API_BASE="${API_BASE:-http://prodavan.local:8088/api/v1}"
API_IMAGE="${API_IMAGE:-ghcr.io/ne-tort/prodavan-api:local}"
WEB_IMAGE="${WEB_IMAGE:-ghcr.io/ne-tort/prodavan-web:local}"

have_image() {
  docker image inspect "$1" >/dev/null 2>&1
}

if [[ -n "${GHCR_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  echo "ensure_local_app_images: GHCR token set — overlay import handles app images"
  exit 0
fi

need_api=1
need_web=1
have_image "${API_IMAGE}" && need_api=0
have_image "${WEB_IMAGE}" && need_web=0

if [[ "$need_api" == "0" && "$need_web" == "0" ]]; then
  echo "==> import existing local API/web tags into k3d"
  BUILD=0 bash "${SCRIPT_DIR}/import_local_app_images_k3d.sh"
  exit 0
fi

if [[ "${BUILD_LOCAL_IMAGES:-1}" != "1" ]]; then
  echo "ERROR: missing ${API_IMAGE} / ${WEB_IMAGE} and BUILD_LOCAL_IMAGES=0" >&2
  echo "Set BUILD_LOCAL_IMAGES=1, GHCR_TOKEN, or build images manually." >&2
  exit 1
fi

echo "==> build + import API/web (no GHCR_TOKEN; BUILD_LOCAL_IMAGES=1)"
BUILD=1 API_BASE="$API_BASE" bash "${SCRIPT_DIR}/import_local_app_images_k3d.sh"
