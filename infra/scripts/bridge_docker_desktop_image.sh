#!/usr/bin/env bash
# Load an image from Docker Desktop (Windows) into WSL docker when daemons differ.
# No-op if powershell/docker unavailable or image already matches repo alembic head.
set -euo pipefail

IMAGE="${1:?usage: bridge_docker_desktop_image.sh <image:tag>}"

if ! command -v powershell.exe >/dev/null 2>&1; then
  exit 0
fi

TAR_WIN='C:\Temp\prodavan-docker-bridge.tar'
TAR_WSL="/mnt/c/Temp/prodavan-docker-bridge.tar"

echo "==> bridge Docker Desktop → WSL: ${IMAGE}"
powershell.exe -NoProfile -Command "New-Item -ItemType Directory -Force C:\Temp | Out-Null; docker save '${IMAGE}' -o '${TAR_WIN}'" \
  || { echo "WARN: docker save on Windows failed for ${IMAGE}" >&2; exit 0; }

[[ -f "$TAR_WSL" ]] || { echo "WARN: bridge tar missing at ${TAR_WSL}" >&2; exit 0; }
docker load -i "$TAR_WSL"
rm -f "$TAR_WSL"
echo "ok — loaded ${IMAGE} into WSL docker"
