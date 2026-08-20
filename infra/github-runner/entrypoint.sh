#!/usr/bin/env bash
set -euo pipefail

: "${REPO_URL:?}"
: "${RUNNER_NAME:=wsl-prodavan}"
: "${LABELS:=self-hosted,linux,docker,wsl-dev}"
: "${RUNNER_VERSION:=2.328.0}"

HOME_DIR=/opt/actions-runner
mkdir -p "${HOME_DIR}"
cd "${HOME_DIR}"

# Docker socket GID on the host (Docker Desktop / engine).
if [[ -S /var/run/docker.sock ]]; then
  DOCKER_GID="$(stat -c '%g' /var/run/docker.sock)"
  if ! getent group "${DOCKER_GID}" >/dev/null 2>&1; then
    groupadd -g "${DOCKER_GID}" dockerhost || true
  fi
  DOCKER_GROUP="$(getent group "${DOCKER_GID}" | cut -d: -f1)"
  usermod -aG "${DOCKER_GROUP}" runner || true
fi

chown -R runner:runner "${HOME_DIR}"

run_as_runner() {
  runuser -u runner -- "$@"
}

if [[ ! -x ./run.sh ]]; then
  echo "Installing actions-runner ${RUNNER_VERSION}..."
  TGZ="/tmp/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  if [[ ! -s "${TGZ}" ]]; then
    curl -fL --retry 5 --retry-delay 5 --connect-timeout 30 \
      -o "${TGZ}" \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  else
    echo "Using preloaded ${TGZ}"
  fi
  tar xzf "${TGZ}" -C "${HOME_DIR}"
  chown -R runner:runner "${HOME_DIR}"
  if [[ ! -x ./run.sh ]]; then
    echo "Runner install failed: run.sh missing" >&2
    ls -la "${HOME_DIR}" >&2 || true
    exit 1
  fi
  # Official script expects root; ignore missing legacy packages on Ubuntu 22.04+.
  ./bin/installdependencies.sh || true
  chown -R runner:runner "${HOME_DIR}"
fi

fetch_token() {
  if [[ -n "${RUNNER_TOKEN:-}" ]]; then
    echo "${RUNNER_TOKEN}"
    return
  fi
  if [[ -n "${ACCESS_TOKEN:-}" ]]; then
    OWNER_REPO="${REPO_URL#https://github.com/}"
    curl -fsSL -X POST \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/${OWNER_REPO}/actions/runners/registration-token" \
      | jq -r .token
    return
  fi
  echo "Set RUNNER_TOKEN or ACCESS_TOKEN" >&2
  exit 1
}

if [[ ! -f .runner ]]; then
  TOKEN="$(fetch_token)"
  run_as_runner ./config.sh \
    --url "${REPO_URL}" \
    --token "${TOKEN}" \
    --name "${RUNNER_NAME}" \
    --labels "${LABELS}" \
    --work "_work" \
    --unattended \
    --replace
fi

# exec so SIGTERM reaches the listener; keep .runner across restarts (no deregister loop).
exec runuser -u runner -- ./run.sh
