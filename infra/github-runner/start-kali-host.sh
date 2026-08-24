#!/usr/bin/env bash
# Canonical GHA runner on Kali HOST (not Docker). Docker jobs still use /var/run/docker.sock.
# Containerized runner was dying mid-job (host-net) → GitHub Session Conflict.
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:${PATH}

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

: "${RUNNER_HOME:=$HOME/prodavan-actions-runner}"
: "${RUNNER_VERSION:=2.336.0}"
TGZ="${ROOT}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"

if [[ ! -f .env ]]; then
  echo "Need .env with REPO_URL + ACCESS_TOKEN (or RUNNER_TOKEN)" >&2
  exit 1
fi
# shellcheck disable=SC1091
set -a
# Strip CR from Windows-edited .env
source <(sed 's/\r$//' .env)
set +a

: "${REPO_URL:?}"
REPO_URL="${REPO_URL//$'\r'/}"
RUNNER_NAME="${RUNNER_NAME//$'\r'/}"
RUNNER_NAME="${RUNNER_NAME:-wsl-prodavan-host}"
LABELS="${LABELS//$'\r'/}"
LABELS="${LABELS:-self-hosted,linux,docker,wsl-dev}"
ACCESS_TOKEN="${ACCESS_TOKEN//$'\r'/}"
RUNNER_TOKEN="${RUNNER_TOKEN-}"
RUNNER_TOKEN="${RUNNER_TOKEN//$'\r'/}"

# Stop docker-based runner if present (same labels would race).
docker rm -f prodavan-gha-runner 2>/dev/null || true

if [[ ! -f "$TGZ" ]]; then
  echo "Downloading ${TGZ}..."
  curl -fL --retry 5 --retry-delay 3 --connect-timeout 30 -o "$TGZ" \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
fi

mkdir -p "$RUNNER_HOME"
if [[ ! -x "${RUNNER_HOME}/run.sh" ]]; then
  tar xzf "$TGZ" -C "$RUNNER_HOME"
fi
printf 'DISABLE_RUNNER_UPDATE=1\n' > "${RUNNER_HOME}/.env"

if [[ ! -f "${RUNNER_HOME}/.runner" ]]; then
  if [[ -n "${RUNNER_TOKEN:-}" ]]; then
    TOKEN="$RUNNER_TOKEN"
  elif [[ -f "${ROOT}/.reg-token" ]]; then
    TOKEN="$(tr -d '\r\n' <"${ROOT}/.reg-token")"
  else
    OWNER_REPO="${REPO_URL#https://github.com/}"
    OWNER_REPO="${OWNER_REPO//$'\r'/}"
    TOKEN="$(curl -fsSL -X POST \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/${OWNER_REPO}/actions/runners/registration-token" \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')"
  fi
  (cd "$RUNNER_HOME" && ./config.sh \
    --url "$REPO_URL" \
    --token "$TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "$LABELS" \
    --work "_work" \
    --unattended \
    --replace)
fi

# Kill prior host listener if any
if [[ -f "${RUNNER_HOME}/.runner_pid" ]]; then
  old="$(cat "${RUNNER_HOME}/.runner_pid" || true)"
  if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
    kill "$old" 2>/dev/null || true
    sleep 2
  fi
fi
pkill -f "${RUNNER_HOME}/bin/Runner.Listener" 2>/dev/null || true
sleep 1

# Keep listener on PATH for job steps (jq, python shim).
export PATH="${HOME}/.local/bin:/usr/bin:/bin:${PATH:-}"
cd "$RUNNER_HOME"
nohup ./run.sh >"${RUNNER_HOME}/runner.out" 2>&1 &
echo $! >"${RUNNER_HOME}/.runner_pid"
sleep 3
tail -20 "${RUNNER_HOME}/runner.out" || true
echo "host runner pid=$(cat "${RUNNER_HOME}/.runner_pid") home=${RUNNER_HOME}"
echo "logs: tail -f ${RUNNER_HOME}/runner.out"
