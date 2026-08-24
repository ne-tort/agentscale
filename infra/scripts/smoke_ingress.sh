#!/usr/bin/env bash
# Smoke HTTP through k3d ingress (Host: prodavan.local) with retries after reboot.
set -euo pipefail

HTTP_PORT="${HTTP_PORT:-8088}"
HOST_HEADER="${SMOKE_HOST:-prodavan.local}"
# GHA runner on Docker Desktop cannot use Kali/k3d loopback; host.docker.internal works.
SMOKE_ADDR="${SMOKE_ADDR:-}"
if [[ -z "$SMOKE_ADDR" ]]; then
  if curl -sS -o /dev/null --connect-timeout 2 --max-time 3 "http://127.0.0.1:${HTTP_PORT}/health/live" >/dev/null 2>&1; then
    SMOKE_ADDR="127.0.0.1"
  else
    SMOKE_ADDR="host.docker.internal"
  fi
fi
BASE="http://${SMOKE_ADDR}:${HTTP_PORT}"
ATTEMPTS="${SMOKE_ATTEMPTS:-24}"
SLEEP_SEC="${SMOKE_SLEEP_SEC:-5}"

smoke_once() {
  local path="$1" code
  code="$(curl -sS -o /tmp/prodavan_smoke_body -w '%{http_code}' --connect-timeout 3 --max-time 15 \
    -H "Host: ${HOST_HEADER}" "${BASE}${path}" 2>/dev/null || echo 000)"
  echo "${path} -> HTTP ${code}"
  [[ "$code" == "200" ]]
}

for path in /health/live /health/ready /api/v1/auth/config /; do
  ok=0
  for i in $(seq 1 "$ATTEMPTS"); do
    if smoke_once "$path"; then
      ok=1
      break
    fi
    echo "  retry ${i}/${ATTEMPTS} in ${SLEEP_SEC}s..."
    sleep "$SLEEP_SEC"
  done
  if [[ "$ok" != "1" ]]; then
    cat /tmp/prodavan_smoke_body 2>/dev/null || true
    exit 1
  fi
done
echo "smoke OK ${BASE} (Host: ${HOST_HEADER})"
