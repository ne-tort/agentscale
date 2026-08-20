#!/usr/bin/env bash
# Smoke HTTP through k3d ingress (Host: prodavan.local).
set -euo pipefail

HTTP_PORT="${HTTP_PORT:-8088}"
HOST_HEADER="${SMOKE_HOST:-prodavan.local}"
BASE="http://127.0.0.1:${HTTP_PORT}"

for path in /health /; do
  code="$(curl -sS -o /tmp/prodavan_smoke_body -w '%{http_code}' -H "Host: ${HOST_HEADER}" "${BASE}${path}" || echo 000)"
  echo "${path} -> HTTP ${code}"
  if [[ "$code" != "200" ]]; then
    cat /tmp/prodavan_smoke_body 2>/dev/null || true
    exit 1
  fi
done
echo "smoke OK ${BASE} (Host: ${HOST_HEADER})"
