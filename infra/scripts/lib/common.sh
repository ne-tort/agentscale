#!/usr/bin/env bash
# Shared helpers for infra/scripts/*.sh
# shellcheck shell=bash

die() {
  echo "ERROR: $*" >&2
  exit 1
}

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
