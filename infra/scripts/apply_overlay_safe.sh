#!/usr/bin/env bash
# Safe local apply for overlays/dev when Argo is absent/emergency fallback.
# Deletes fixed-name hook Jobs first to avoid immutable PodTemplate errors.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS="${PRODAVAN_NS:-prodavan}"
OVERLAY="${OVERLAY_PATH:-${ROOT}/infra/k3s/overlays/dev}"

for j in prodavan-kafka-init prodavan-minio-init; do
  kubectl -n "$NS" delete job "$j" --ignore-not-found >/dev/null 2>&1 || true
done

kubectl apply -k "$OVERLAY"
