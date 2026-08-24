#!/usr/bin/env bash
# Load an image from Docker Desktop (Windows) into WSL docker when daemons differ.
# No-op if Windows docker/cmd unavailable or image already in WSL docker.
set -euo pipefail

IMAGE="${1:?usage: bridge_docker_desktop_image.sh <image:tag>}"

TAR_WIN='C:\Temp\prodavan-docker-bridge.tar'
TAR_WSL="/mnt/c/Temp/prodavan-docker-bridge.tar"

win_docker_save() {
  mkdir -p /mnt/c/Temp
  # Prefer cmd.exe — avoids WSL "Exec format error" on some powershell.exe shims.
  if command -v cmd.exe >/dev/null 2>&1; then
    cmd.exe /c "docker save ${IMAGE} -o ${TAR_WIN}" && return 0
  fi
  local ps="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
  if [[ -f "$ps" ]]; then
    "$ps" -NoProfile -Command "New-Item -ItemType Directory -Force C:\Temp | Out-Null; docker save '${IMAGE}' -o '${TAR_WIN}'" && return 0
  fi
  return 1
}

echo "==> bridge Docker Desktop → WSL: ${IMAGE}"
win_docker_save || { echo "WARN: docker save on Windows failed for ${IMAGE}" >&2; exit 0; }

[[ -f "$TAR_WSL" ]] || { echo "WARN: bridge tar missing at ${TAR_WSL}" >&2; exit 0; }
docker load -i "$TAR_WSL"
rm -f "$TAR_WSL"
echo "ok — loaded ${IMAGE} into WSL docker"
