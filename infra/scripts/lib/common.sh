#!/usr/bin/env bash
# Shared helpers for infra/scripts/*.sh
# shellcheck shell=bash

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# GHA/self-hosted: install_cli_tools writes here; later steps must see it.
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' not found on PATH"
}

default_kubeconfig() {
  local script_dir root
  script_dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
  root="$(cd "${script_dir}/../.." && pwd)"
  echo "${root}/infra/.kube/prodavan-k3d.yaml"
}

ensure_kubeconfig_env() {
  if [[ -z "${KUBECONFIG:-}" ]]; then
    export KUBECONFIG
    KUBECONFIG="$(default_kubeconfig)"
  fi
}

# Docker Desktop/WSL can briefly drop the socket during k3d image import / node restart.
wait_docker() {
  local attempts="${1:-30}"
  local i
  for i in $(seq 1 "$attempts"); do
    if docker info >/dev/null 2>&1; then
      return 0
    fi
    echo "waiting for docker daemon (${i}/${attempts})..."
    sleep 2
  done
  die "docker daemon not reachable after ${attempts} attempts"
}

# k3d image import / server container restart can leave the node SchedulingDisabled.
uncordon_all_nodes() {
  command -v kubectl >/dev/null 2>&1 || return 0
  local n
  kubectl get nodes -o name 2>/dev/null | while read -r n; do
    kubectl uncordon "${n#node/}" >/dev/null 2>&1 || true
  done
}

wait_nodes_schedulable() {
  local attempts="${1:-60}"
  local i status
  command -v kubectl >/dev/null 2>&1 || return 0
  for i in $(seq 1 "$attempts"); do
    uncordon_all_nodes
    if ! kubectl get nodes >/dev/null 2>&1; then
      echo "waiting for API (${i}/${attempts})..."
      sleep 2
      continue
    fi
    status="$(kubectl get nodes --no-headers 2>/dev/null || true)"
    if [[ -n "$status" ]] && ! echo "$status" | grep -q SchedulingDisabled; then
      if echo "$status" | grep -q ' Ready'; then
        return 0
      fi
    fi
    echo "waiting for schedulable Ready node (${i}/${attempts})..."
    sleep 2
  done
  kubectl get nodes -o wide || true
  die "no schedulable Ready node after ${attempts} attempts"
}

# Call after every k3d image import.
after_k3d_image_import() {
  local i
  for i in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
      break
    fi
    echo "waiting for docker after image import (${i}/30)..."
    sleep 2
  done
  # Give kubelet a moment after tools-node / server bounce.
  sleep 3
  wait_nodes_schedulable 60
}
