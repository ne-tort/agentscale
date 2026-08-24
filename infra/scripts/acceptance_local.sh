#!/usr/bin/env bash
# Full local acceptance: recover stack + touchable UI (smoke + seed chat).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export KUBECONFIG="${KUBECONFIG:-$(cd "${SCRIPT_DIR}/../.." && pwd)/infra/.kube/prodavan-k3d.yaml}"

bash "${SCRIPT_DIR}/recover_local_stack.sh"
bash "${SCRIPT_DIR}/verify_touchable_ui.sh"
echo "acceptance_local OK"
