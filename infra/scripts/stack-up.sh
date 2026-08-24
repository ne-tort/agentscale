#!/usr/bin/env bash
# Full stack rebuild + verify on a STABLE Docker engine.
#
# On Windows: prefer Docker Desktop (not Kali/WSL docker used by Amnezia —
# that engine restarts often and breaks localhost:8080 via wslrelay).
#
#   export DOCKER_CONTEXT=desktop-linux   # Windows Docker Desktop
#   bash infra/scripts/stack-up.sh

set -euo pipefail
export DOCKER_BUILDKIT=1

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Windows host: default to Docker Desktop if available
if command -v docker.exe >/dev/null 2>&1; then
  if docker.exe context ls 2>/dev/null | grep -q 'desktop-linux'; then
    export DOCKER_HOST="${DOCKER_HOST:-npipe:////./pipe/dockerDesktopLinuxEngine}"
  fi
fi

if docker context ls 2>/dev/null | grep -q 'desktop-linux \*'; then
  echo "using Docker context: desktop-linux"
elif [ -n "${DOCKER_CONTEXT:-}" ]; then
  echo "using Docker context: ${DOCKER_CONTEXT}"
else
  echo "using default docker ($(docker info --format '{{.Name}}' 2>/dev/null || echo unknown))"
fi

BUILDER_NAME="${PRODAVAN_BUILDX_BUILDER:-prodavan}"
export BUILDX_BUILDER="${BUILDER_NAME}"

bash infra/scripts/docker-build-cached.sh ensure-builder || true

echo "======== BUILD API ========"
bash infra/scripts/docker-build-cached.sh api
if docker image inspect prodavan-api:latest >/dev/null 2>&1; then
  docker tag prodavan-api:latest prodavan-api:stack
else
  docker tag prodavan-api:local prodavan-api:stack
fi

echo "======== BUILD WEB (runtime) ========"
START=$(date +%s)
docker buildx build \
  --builder "${BUILDER_NAME}" \
  --progress=plain \
  --file apps/flutter/Dockerfile \
  --target runtime \
  --build-arg API_BASE=http://localhost:8000 \
  --build-arg FLUTTER_VERSION=3.47.1 \
  --tag prodavan-web:stack \
  --tag prodavan-web:latest \
  --tag prodavan-web:local \
  --cache-from "type=local,src=${ROOT}/.docker-cache/web" \
  --cache-to "type=local,dest=${ROOT}/.docker-cache/web,mode=max" \
  --load \
  "${ROOT}"
END=$(date +%s)
echo "WEB_BUILD_SECONDS=$((END-START))"

echo "======== COMPOSE UP ========"
docker compose -f infra/docker-compose.stack.yml up -d --force-recreate --no-build 2>/dev/null \
  || docker compose -f infra/docker-compose.stack.yml up -d --force-recreate

echo "======== WAIT HEALTH ========"
ok=0
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null \
     && curl -fsS http://127.0.0.1:8000/health/ready >/dev/null \
     && curl -fsS http://127.0.0.1:8080/health >/dev/null \
     && code=$(curl -fsS -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/) \
     && [ "$code" = "200" ]; then
    ok=1
    break
  fi
  sleep 2
done

docker compose -f infra/docker-compose.stack.yml ps
echo "API_HEALTH=$(curl -fsS http://127.0.0.1:8000/api/v1/health || true)"
echo "WEB_HEALTH=$(curl -fsS http://127.0.0.1:8080/health || true)"
echo "WEB_INDEX=$(curl -fsS -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/ || true)"

if [ "$ok" != "1" ]; then
  echo "SMOKE FAILED" >&2
  docker compose -f infra/docker-compose.stack.yml logs --tail=80
  exit 1
fi
echo "SMOKE OK — open http://127.0.0.1:8080/"
