#!/usr/bin/env bash
# Put GHCR :latest into the cluster, then bounce first-party Deployments.
# If this Docker engine owns k3d → pull+import (reliable on Desktop/WSL TLS).
# Else (GHA runner on Desktop, k3d in Kali) → only rollout; Kali recover imports.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

NS="${PRODAVAN_NS:-prodavan}"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
need_cmd kubectl
ensure_kubeconfig_env

if command -v k3d >/dev/null 2>&1 && command -v docker >/dev/null 2>&1 \
  && k3d cluster list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$CLUSTER"; then
  echo "==> k3d in this Docker engine — import overlay :latest from GHCR"
  bash "${SCRIPT_DIR}/import_overlay_images.sh" || echo "WARN: import_overlay_images failed"
else
  echo "==> k3d not in this Docker engine — skip import (kubelet cache / Kali recover)"
fi

for dep in prodavan-api prodavan-web prodavan-celery-worker prodavan-celery-beat; do
  if kubectl -n "$NS" get deploy "$dep" >/dev/null 2>&1; then
    echo "rollout restart deploy/${dep}"
    kubectl -n "$NS" rollout restart "deploy/${dep}"
  fi
done
echo "rollout_latest_from_ghcr queued"
