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

repo_root_from_scripts() {
  # Call from a file in infra/scripts/
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
  cd "${script_dir}/../.." && pwd
}
