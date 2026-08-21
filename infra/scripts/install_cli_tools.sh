#!/usr/bin/env bash
# Install CLI tools required for local k3d (official upstream installers).
# Idempotent. Safe for non-interactive WSL/CI (never prompts for sudo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

BIN_DIR="${K3D_INSTALL_DIR:-${CLI_BIN_DIR:-${HOME}/.local/bin}}"
mkdir -p "$BIN_DIR"
export PATH="${BIN_DIR}:${PATH}"

install_k3d() {
  if command -v k3d >/dev/null 2>&1; then
    echo "k3d already installed: $(command -v k3d)"
    k3d version || true
    return 0
  fi
  need_cmd curl
  echo "Installing k3d via official install.sh into ${BIN_DIR}"
  # CRITICAL: environment must be on the *bash* side of the pipe.
  #   WRONG: USE_SUDO=false curl ... | bash   # vars apply to curl only → defaults to
  #          /usr/local/bin + sudo → hangs in non-interactive WSL/CI waiting for password
  #   RIGHT: curl ... | USE_SUDO=false K3D_INSTALL_DIR=... bash
  # Docs: https://github.com/k3d-io/k3d#get
  if [[ -n "${K3D_VERSION:-}" ]]; then
    curl -fsSL https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh \
      | USE_SUDO=false K3D_INSTALL_DIR="${BIN_DIR}" TAG="${K3D_VERSION}" bash
  else
    curl -fsSL https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh \
      | USE_SUDO=false K3D_INSTALL_DIR="${BIN_DIR}" bash
  fi
  need_cmd k3d
  k3d version
}

install_kubectl() {
  export PATH="${BIN_DIR}:${PATH}"
  # Prefer a real binary over Docker Desktop's often-broken /usr/local/bin/kubectl symlink.
  if [[ -x "${BIN_DIR}/kubectl" ]] && "${BIN_DIR}/kubectl" version --client >/dev/null 2>&1; then
    echo "kubectl already installed: ${BIN_DIR}/kubectl"
    "${BIN_DIR}/kubectl" version --client
    return 0
  fi
  if command -v kubectl >/dev/null 2>&1 && kubectl version --client >/dev/null 2>&1; then
    echo "kubectl already on PATH: $(command -v kubectl)"
    return 0
  fi
  need_cmd curl
  echo "Installing kubectl into ${BIN_DIR} (official Kubernetes release)"
  # https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/
  local ver
  ver="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
  curl -fsSLo "${BIN_DIR}/kubectl" "https://dl.k8s.io/release/${ver}/bin/linux/amd64/kubectl"
  chmod +x "${BIN_DIR}/kubectl"
  need_cmd kubectl
  kubectl version --client
}

main() {
  install_k3d
  install_kubectl
  echo "OK CLI tools in PATH (BIN_DIR=${BIN_DIR})"
}

main "$@"
