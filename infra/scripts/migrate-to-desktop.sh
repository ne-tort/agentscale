#!/usr/bin/env bash
# Move Prodavan stack from unstable Kali/WSL docker → Docker Desktop (Windows).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "======== STOP WSL/Kali stack (frees 8080/8000) ========"
docker compose -f infra/docker-compose.stack.yml down || true

echo "======== EXPORT IMAGES FROM WSL DOCKER ========"
OUT="/tmp/prodavan-stack-images.tar"
docker save -o "$OUT" prodavan-api:stack prodavan-web:stack
ls -lh "$OUT"

echo "DONE_SAVE path=$OUT"
