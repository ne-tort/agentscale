#!/usr/bin/env bash
# Idempotent local k3d cluster: create / start after reboot / refresh kubeconfig.
# Safe to run after WSL or Docker Desktop restart (including Exited server containers).
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

cluster_listed() {
  k3d cluster list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$CLUSTER"
}

# True if k3d reports all servers up (e.g. 1/1, not 0/1).
k3d_servers_ready() {
  k3d cluster list 2>/dev/null | awk -v c="$CLUSTER" '
    NR>1 && $1==c {
      split($2, a, "/");
      ok = (a[1] == a[2] && a[1] + 0 > 0);
      exit !ok
    }
    END { exit 1 }
  '
}

# True if the server container is actually running (not Exited/Created).
docker_server_running() {
  docker ps --filter "name=k3d-${CLUSTER}-server" --filter "status=running" --format '{{.ID}}' 2>/dev/null | grep -q .
}

cluster_healthy() {
  # Docker may show Up while k3d list still says 0/1 for a moment — prefer API.
  docker_server_running || return 1
  if [[ -f "$KCFG" ]] && KUBECONFIG="$KCFG" kubectl get --raw=/readyz >/dev/null 2>&1; then
    return 0
  fi
  k3d_servers_ready
}

set_restart_unless_stopped() {
  local ids
  ids="$(docker ps -aq --filter "name=k3d-${CLUSTER}" 2>/dev/null || true)"
  if [[ -z "$ids" ]]; then
    ids="$(docker ps -aq --filter "label=k3d.cluster=${CLUSTER}" 2>/dev/null || true)"
  fi
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
  echo "Starting k3d cluster ${CLUSTER} (recover after stop/reboot/crash)..."
  # If docker left server in Exited, k3d start brings it back.
  k3d cluster start "$CLUSTER" || true
  if ! docker_server_running; then
    echo "k3d start incomplete — docker start server containers..."
    docker ps -aq --filter "name=k3d-${CLUSTER}-server" | while read -r id; do
      docker start "$id" || true
    done
    docker ps -aq --filter "name=k3d-${CLUSTER}-serverlb" | while read -r id; do
      docker start "$id" || true
    done
  fi
  set_restart_unless_stopped
}

write_kubeconfig() {
  mkdir -p "$(dirname "$KCFG")"
  k3d kubeconfig get "$CLUSTER" >"$KCFG"
  chmod 600 "$KCFG" || true
  echo "kubeconfig -> $KCFG"
}

wait_api() {
  export KUBECONFIG="$KCFG"
  local i
  for i in $(seq 1 30); do
    if kubectl get --raw=/readyz >/dev/null 2>&1 || kubectl get nodes >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_nodes() {
  export KUBECONFIG="$KCFG"
  need_cmd kubectl
  echo "Waiting for API + nodes Ready (${WAIT_NODES_TIMEOUT})..."
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if wait_api && kubectl wait --for=condition=Ready nodes --all --timeout="$WAIT_NODES_TIMEOUT"; then
      kubectl get nodes -o wide
      return 0
    fi
    echo "API/nodes not ready (attempt ${attempt}) — restarting cluster..."
    start_cluster
    write_kubeconfig
    sleep 5
  done
  die "nodes not Ready after retries"
}

main() {
  need_cmd docker
  need_cmd curl
  export PATH="${HOME}/.local/bin:${PATH}"
  bash "${SCRIPT_DIR}/install_cli_tools.sh"

  if ! docker info >/dev/null 2>&1; then
    die "docker daemon not reachable — start Docker Desktop / dockerd"
  fi

  if cluster_listed; then
    if cluster_healthy; then
      echo "k3d cluster ${CLUSTER} already healthy"
      set_restart_unless_stopped
    else
      echo "k3d cluster ${CLUSTER} listed but unhealthy (servers/docker) — recovering"
      start_cluster
    fi
  else
    if [[ "${REQUIRE_EXISTING_CLUSTER:-0}" == "1" || "${CREATE_CLUSTER:-1}" == "0" ]]; then
      die "k3d cluster ${CLUSTER} is not in this Docker engine. CI deploy must attach to the workstation cluster (same docker context as k3d). Refusing to create a second cluster (port 6443/8088 clash)."
    fi
    create_cluster
  fi

  write_kubeconfig
  wait_nodes

  if [[ "${WARM_K3D_IMAGES:-1}" == "1" ]]; then
    echo "Warming base images into cluster (set WARM_K3D_IMAGES=0 to skip)..."
    bash "${SCRIPT_DIR}/warm_k3d_base_images.sh" || echo "WARN: warm images incomplete — kube-system may stay ImagePullBackOff"
  fi

  export KUBECONFIG="$KCFG"
  echo "OK cluster=${CLUSTER} KUBECONFIG=${KCFG}"
}

main "$@"
