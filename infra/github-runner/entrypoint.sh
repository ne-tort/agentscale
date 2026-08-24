#!/usr/bin/env bash
set -euo pipefail

: "${REPO_URL:?}"
: "${RUNNER_NAME:=wsl-prodavan}"
: "${LABELS:=self-hosted,linux,docker,wsl-dev}"
: "${RUNNER_VERSION:=2.336.0}"

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

# Prefer public resolvers — Docker Desktop/WSL host DNS sometimes times out.
if [[ "${FORCE_PUBLIC_DNS:-1}" == "1" ]]; then
  printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:2 attempts:3\n' >/etc/resolv.conf || true
fi

# Fail fast if GitHub TLS is broken (Desktop VM path often EOF mid-handshake).
wait_github_tls() {
  local i url="${GITHUB_TLS_PROBE_URL:-https://api.github.com/zen}"
  echo "Probing GitHub TLS (${url})..."
  for i in $(seq 1 30); do
    if curl -fsS --connect-timeout 5 --max-time 15 -o /dev/null "$url"; then
      echo "GitHub TLS OK"
      return 0
    fi
    echo "  TLS not ready (${i}/30) — retry in 5s"
    sleep 5
  done
  echo "ERROR: cannot establish TLS to GitHub from this network namespace." >&2
  echo "Use Kali WSL docker with network_mode:host (Desktop host-net often breaks TLS)." >&2
  echo "resolv.conf:" >&2
  cat /etc/resolv.conf >&2 || true
  return 1
}

if [[ ! -x ./run.sh ]]; then
  echo "Installing actions-runner ${RUNNER_VERSION}..."
  TGZ="/tmp/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  if [[ ! -s "${TGZ}" ]]; then
    wait_github_tls
    curl -fL --retry 5 --retry-delay 5 --retry-all-errors --connect-timeout 30 \
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
    curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors --connect-timeout 20 \
      -X POST \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/${OWNER_REPO}/actions/runners/registration-token" \
      | jq -r .token
    return
  fi
  echo "Set RUNNER_TOKEN or ACCESS_TOKEN" >&2
  exit 1
}

wait_github_tls

# Official knob — must live in runner home .env (docker -e alone is not enough).
printf 'DISABLE_RUNNER_UPDATE=1\n' > "${HOME_DIR}/.env"
chown runner:runner "${HOME_DIR}/.env"

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

# Keep container alive across job-cancel / listener exits. Instant restart with the
# same runner name resets GitHub's session Conflict timer — backoff before retry.
trap 'echo "entrypoint: got signal, stopping"; exit 0' TERM INT
while true; do
  set +e
  runuser -u runner -- ./run.sh
  rc=$?
  set -e
  echo "run.sh exited rc=${rc} — backoff 90s before reconnect (avoids SessionConflict thrash)"
  sleep 90
done
