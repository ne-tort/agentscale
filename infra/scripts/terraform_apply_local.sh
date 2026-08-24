#!/usr/bin/env bash
# One-shot: Windows image build (if needed) + terraform apply (local connection) + GitOps.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TF_DIR="${ROOT}/infra/terraform/environments/local"

export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
export K3D_CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
export SEED_UI="${SEED_UI:-1}"
export BUILD_LOCAL_IMAGES="${BUILD_LOCAL_IMAGES:-1}"

TF=""
if command -v terraform >/dev/null 2>&1; then
  TF=terraform
elif [[ -x "${ROOT}/tools/terraform" ]]; then
  TF="${ROOT}/tools/terraform"
elif [[ -x "${ROOT}/tools/terraform.exe" ]]; then
  TF="${ROOT}/tools/terraform.exe"
fi
[[ -n "$TF" ]] || { echo "ERROR: terraform not found" >&2; exit 1; }

# Pre-build on Docker Desktop when GHCR token absent (WSL apt often flaky).
if [[ -z "${GHCR_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  bash "${SCRIPT_DIR}/build_local_app_images_from_wsl.sh" \
    || echo "WARN: Windows build skipped — ensure_local will retry bridge"
fi

(
  cd "$TF_DIR"
  "$TF" init -input=false
  "$TF" apply -auto-approve -input=false \
    -var=connection_type=local \
    -var=bootstrap_gitops=true \
    -var=remote_repo_path="$ROOT"
)

echo "terraform_apply_local OK"
echo "UI: http://prodavan.local:${HTTP_PORT:-8088}/"
