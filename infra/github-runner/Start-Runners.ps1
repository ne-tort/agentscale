#Requires -RunAsAdministrator
# Start Prodavan Actions runner pack in Docker Desktop (default: 4 replicas).
# Peak CI Gate parallelism = 4 (infra, api, flutter, schemas).

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'Docker Desktop not found. Install/start Docker Desktop first.'
}

$envFile = Join-Path $here '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $here '.env.example') $envFile
    Write-Host "Created .env — set ACCESS_TOKEN (gh auth token) then re-run."
    exit 1
}

$replicas = 4
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*RUNNER_REPLICAS\s*=\s*(\d+)') { $replicas = [int]$Matches[1] }
}

# Ensure Windows kube dir exists (empty mount is ok)
$kube = Join-Path $env:USERPROFILE '.kube'
if (-not (Test-Path $kube)) { New-Item -ItemType Directory -Path $kube | Out-Null }

Write-Host "docker compose build + up -d --scale runner=$replicas"
docker compose build
docker compose up -d --scale "runner=$replicas" --remove-orphans
docker compose ps
Write-Host @"

Check GitHub: gh api repos/ne-tort/prodavan/actions/runners --jq ".runners[]|{name,status,busy,labels:[.labels[].name]}"
Cache volume: prodavan-ci-cache → /cache (Flutter/pub/Poetry/pip)
Logs: docker compose logs -f
Stop:  docker compose down          # keeps cache
Wipe:  docker compose down -v       # deletes cache
"@
