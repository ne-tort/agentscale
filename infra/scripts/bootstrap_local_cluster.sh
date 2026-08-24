#!/usr/bin/env bash
# Bootstrap / recover local k3d + GitOps (legacy entry → recover_local_stack).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TF_DIR="${ROOT}/infra/terraform/environments/local"

TF=""
if command -v terraform >/dev/null 2>&1; then
  TF=terraform
elif [[ -x "${ROOT}/tools/terraform" ]]; then
  TF="${ROOT}/tools/terraform"
elif [[ -x "${ROOT}/tools/terraform.exe" ]]; then
  TF="${ROOT}/tools/terraform.exe"
fi

if [[ -n "$TF" ]]; then
  echo "==> Terraform state sync (cluster only; GitOps via recover_local_stack)"
  (
    cd "$TF_DIR"
    "$TF" init -input=false
    "$TF" apply -auto-approve -input=false -var=bootstrap_gitops=false \
      || echo "WARN: terraform apply failed — cluster may already exist"
  )
fi

exec bash "${SCRIPT_DIR}/recover_local_stack.sh"
