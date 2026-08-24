#!/usr/bin/env bash
# Image pin contract: first-party :latest; third-party frozen version tags (not :latest).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

need() { command -v "$1" >/dev/null || { echo "FAIL: missing $1" >&2; exit 1; }; }
need kubectl

manifest="$(kubectl kustomize "${ROOT}/infra/k3s/overlays/dev")"

first_party=0
while read -r img; do
  [[ -n "$img" ]] || continue
  case "$img" in
    ghcr.io/ne-tort/prodavan-api:latest|ghcr.io/ne-tort/prodavan-web:latest)
      first_party=$((first_party + 1))
      ;;
    ghcr.io/ne-tort/prodavan-api:*|ghcr.io/ne-tort/prodavan-web:*)
      echo "FAIL: first-party image must be :latest (got ${img})" >&2
      exit 1
      ;;
    postgres:16|postgres:latest|redis:7-alpine|redis:7|redis:latest|minio/minio:latest|minio/mc:latest)
      echo "FAIL: infra image must be a frozen version tag (got ${img})" >&2
      exit 1
      ;;
    *:latest)
      echo "FAIL: third-party must not use :latest (got ${img})" >&2
      exit 1
      ;;
  esac
done < <(printf '%s\n' "$manifest" | awk '/^[[:space:]]+image:[[:space:]]/{print $2}')

[[ "$first_party" -ge 2 ]] || { echo "FAIL: expected api+web :latest in overlay render" >&2; exit 1; }

echo "$manifest" | grep -q 'postgres:16.15' \
  || { echo "FAIL: postgres must be pinned to 16.15" >&2; exit 1; }
echo "$manifest" | grep -q 'redis:7.4.11-alpine' \
  || { echo "FAIL: redis must be pinned to 7.4.11-alpine" >&2; exit 1; }
echo "$manifest" | grep -q 'minio/minio:RELEASE.2024-10-02T17-50-41Z' \
  || { echo "FAIL: minio must stay on pinned RELEASE tag" >&2; exit 1; }
echo "$manifest" | grep -q 'redpanda:v24.2.4' \
  || { echo "FAIL: redpanda must stay on v24.2.4 (fsync-validated)" >&2; exit 1; }

echo "verify_image_pins OK"
