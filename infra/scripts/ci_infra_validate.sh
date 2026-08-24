#!/usr/bin/env bash
# Cheap, cluster-free infra contract for CI Gate / local.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

need() { command -v "$1" >/dev/null || { echo "FAIL: missing $1" >&2; exit 1; }; }
need kubectl
if ! command -v terraform >/dev/null 2>&1; then
  need docker
fi

echo "==> kustomize overlays/dev"
kubectl kustomize "${ROOT}/infra/k3s/overlays/dev" >/tmp/prodavan-kustomize.yaml
echo "ok kustomize ($(wc -l < /tmp/prodavan-kustomize.yaml) lines)"

echo "==> image pins"
bash "${SCRIPT_DIR}/verify_image_pins.sh"

echo "==> bash -n infra/scripts"
fail=0
while IFS= read -r -d '' f; do
  bash -n "$f" || fail=1
done < <(find "${ROOT}/infra/scripts" -name '*.sh' -print0)
[[ "$fail" == "0" ]] || { echo "FAIL: bash -n" >&2; exit 1; }
echo "ok shell syntax"

TF_IMAGE="${TF_IMAGE:-hashicorp/terraform:1.9.8}"

tf() {
  if command -v terraform >/dev/null 2>&1; then
    terraform "$@"
    return
  fi
  docker run --rm \
    -v "${ROOT}:/workspace" \
    -w /workspace/infra/terraform/environments/local \
    "${TF_IMAGE}" "$@"
}

echo "==> terraform validate (local env, no apply)"
(
  cd "${ROOT}/infra/terraform/environments/local"
  tf init -backend=false -input=false
  tf validate
)

echo "ci_infra_validate OK"
