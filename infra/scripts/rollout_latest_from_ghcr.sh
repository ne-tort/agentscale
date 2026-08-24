#!/usr/bin/env bash
# Recreate first-party pods so kubelet re-pulls :latest from GHCR (Always).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

NS="${PRODAVAN_NS:-prodavan}"
need_cmd kubectl
ensure_kubeconfig_env

for dep in prodavan-api prodavan-web prodavan-celery-worker prodavan-celery-beat; do
  if kubectl -n "$NS" get deploy "$dep" >/dev/null 2>&1; then
    echo "rollout restart deploy/${dep}"
    kubectl -n "$NS" rollout restart "deploy/${dep}"
  fi
done
echo "rollout_latest_from_ghcr queued"
