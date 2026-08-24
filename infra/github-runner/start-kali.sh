#!/usr/bin/env bash
# Start/restart GHA runner on Kali dockerd (canonical). Do not use Docker Desktop.
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:${PATH}
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

: "${RUNNER_IMAGE:=prodavan-gha-runner:local}"
: "${RUNNER_NAME_DEFAULT:=wsl-prodavan-kali4}"
TGZ="actions-runner-linux-x64-2.336.0.tar.gz"

if [[ ! -f .env ]]; then
  echo "Copy .env.example → .env and set ACCESS_TOKEN or RUNNER_TOKEN" >&2
  exit 1
fi

if ! grep -q '^DISABLE_RUNNER_UPDATE=1' .env 2>/dev/null; then
  echo "DISABLE_RUNNER_UPDATE=1" >>.env
fi

if [[ ! -f "$TGZ" ]]; then
  echo "Downloading ${TGZ}..."
  curl -fL --retry 5 --retry-delay 3 --connect-timeout 30 -o "$TGZ" \
    "https://github.com/actions/runner/releases/download/v2.336.0/${TGZ}"
fi

docker build --network=host -t "$RUNNER_IMAGE" .
docker rm -f prodavan-gha-runner 2>/dev/null || true
# Fresh volume avoids stale .runner / session Conflict after rename or crash.
if [[ "${FRESH_VOLUME:-1}" == "1" ]]; then
  docker volume rm github-runner_runner-home 2>/dev/null || true
fi
docker volume create github-runner_runner-home >/dev/null

docker run -d --name prodavan-gha-runner --restart unless-stopped --network host \
  --env-file .env \
  -e DISABLE_RUNNER_UPDATE=1 \
  -e RUNNER_VERSION=2.336.0 \
  -e FORCE_PUBLIC_DNS=1 \
  -e KUBECONFIG=/kube/prodavan-k3d.yaml \
  -e KUBECONFIG_REWRITE_HOST= \
  -e GITHUB_TLS_PROBE_URL=https://api.github.com/zen \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v github-runner_runner-home:/opt/actions-runner \
  -v "${ROOT}/../.kube:/kube:ro" \
  -v "${ROOT}/${TGZ}:/tmp/actions-runner-linux-x64-2.336.0.tar.gz:ro" \
  "$RUNNER_IMAGE"

echo "logs: docker logs -f prodavan-gha-runner"
