#!/usr/bin/env bash
# Idempotent local k3d cluster: create / start after reboot / refresh kubeconfig.
# Safe to run after WSL or Docker Desktop restart.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
K3S_VERSION="${K3S_VERSION:-v1.29.5-k3s1}"
HTTP_PORT="${HTTP_PORT:-8088}"
HTTPS_PORT="${HTTPS_PORT:-8443}"
API_PORT="${API_PORT:-6443}"
KCFG="${KUBECONFIG_OUT:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
WAIT_NODES_TIMEOUT="${WAIT_NODES_TIMEOUT:-180s}"

install_k3d() {
  if command -v k3d >/dev/null 2>&1; then
    return 0
  fi
  echo "Installing k3d..."
  # Prefer writable user bin when /usr/local needs sudo
  export K3D_INSTALL_DIR="${K3D_INSTALL_DIR:-${HOME}/.local/bin}"
  mkdir -p "$K3D_INSTALL_DIR"
  curl -fsSL https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
  export PATH="${K3D_INSTALL_DIR}:${PATH}"
  need_cmd k3d
}

# Docker Desktop may leave a broken kubectl symlink under /usr/local/bin.
install_kubectl() {
  export PATH="${HOME}/.local/bin:${PATH}"
  if command -v kubectl >/dev/null 2>&1 && kubectl version --client >/dev/null 2>&1; then
    return 0
  fi
  echo "Installing kubectl to ~/.local/bin ..."
  mkdir -p "${HOME}/.local/bin"
  local ver
  ver="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
  curl -fsSLo "${HOME}/.local/bin/kubectl" "https://dl.k8s.io/release/${ver}/bin/linux/amd64/kubectl"
  chmod +x "${HOME}/.local/bin/kubectl"
  need_cmd kubectl
  kubectl version --client >/dev/null
}

cluster_listed() {
  k3d cluster list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$CLUSTER"
}

# True if at least one server container is running.
cluster_servers_running() {
  local n
  n="$(docker ps --filter "label=k3d.cluster=${CLUSTER}" --filter "label=k3d.role=server" --format '{{.ID}}' 2>/dev/null | wc -l | tr -d ' ')"
  [[ "${n:-0}" -ge 1 ]]
}

set_restart_unless_stopped() {
  local ids
  ids="$(docker ps -aq --filter "label=k3d.cluster=${CLUSTER}" 2>/dev/null || true)"
  if [[ -z "$ids" ]]; then
    return 0
  fi
  # Persist across Docker Desktop / WSL reboots when engine comes back.
  # shellcheck disable=SC2086
  docker update --restart=unless-stopped $ids >/dev/null || true
}

create_cluster() {
  echo "Creating k3d cluster ${CLUSTER} (HTTP ${HTTP_PORT}->80, API ${API_PORT})..."
  k3d cluster create "$CLUSTER" \
    --image "rancher/k3s:${K3S_VERSION}" \
    --api-port "127.0.0.1:${API_PORT}" \
    -p "${HTTP_PORT}:80@loadbalancer" \
    -p "${HTTPS_PORT}:443@loadbalancer" \
    --wait
  set_restart_unless_stopped
}

start_cluster() {
  echo "Starting k3d cluster ${CLUSTER} (recover after stop/reboot)..."
  k3d cluster start "$CLUSTER"
  set_restart_unless_stopped
}

write_kubeconfig() {
  mkdir -p "$(dirname "$KCFG")"
  k3d kubeconfig get "$CLUSTER" >"$KCFG"
  chmod 600 "$KCFG" || true
  # Also refresh path expected by runner mount (same default).
  echo "kubeconfig -> $KCFG"
}

wait_nodes() {
  export KUBECONFIG="$KCFG"
  need_cmd kubectl
  echo "Waiting for nodes Ready (${WAIT_NODES_TIMEOUT})..."
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if kubectl wait --for=condition=Ready nodes --all --timeout="$WAIT_NODES_TIMEOUT"; then
      kubectl get nodes -o wide
      return 0
    fi
    echo "kubectl wait failed (attempt ${attempt}) — restarting cluster and retrying..."
    k3d cluster start "$CLUSTER" || true
    set_restart_unless_stopped
    write_kubeconfig
    sleep 5
  done
  die "nodes not Ready after retries"
}

main() {
  need_cmd docker
  need_cmd curl
  export PATH="${HOME}/.local/bin:${PATH}"
  install_k3d
  install_kubectl

  if ! docker info >/dev/null 2>&1; then
    die "docker daemon not reachable — start Docker Desktop / dockerd"
  fi

  if cluster_listed; then
    if cluster_servers_running; then
      echo "k3d cluster ${CLUSTER} already running"
      set_restart_unless_stopped
    else
      start_cluster
    fi
  else
    create_cluster
  fi

  write_kubeconfig
  wait_nodes
  export KUBECONFIG="$KCFG"
  echo "OK cluster=${CLUSTER} KUBECONFIG=${KCFG}"
}

main "$@"
