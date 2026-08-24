# Build API + web images on Docker Desktop (Windows) for k3d :local tags.
# Run from prodavan/ root in PowerShell. WSL import uses bridge_docker_desktop_image.sh.
param(
  [string]$ApiBase = "http://prodavan.local:8088/api/v1"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path "$Root/apps/api/Dockerfile")) {
  $Root = (Get-Location).Path
}

Write-Host "==> build API -> ghcr.io/ne-tort/prodavan-api:local"
docker build -f "$Root/apps/api/Dockerfile" `
  -t ghcr.io/ne-tort/prodavan-api:local `
  -t prodavan-api:local `
  "$Root"

Write-Host "==> build web -> ghcr.io/ne-tort/prodavan-web:local (API_BASE=$ApiBase)"
docker build -f "$Root/apps/flutter/Dockerfile" --target runtime `
  --build-arg "API_BASE=$ApiBase" `
  -t ghcr.io/ne-tort/prodavan-web:local `
  -t prodavan-web:local `
  "$Root"

Write-Host "ok - import in WSL: BUILD=0 bash infra/scripts/import_local_app_images_k3d.sh"
