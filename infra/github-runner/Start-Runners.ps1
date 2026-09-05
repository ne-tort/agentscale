# Start Prodavan Actions runner pack in Docker Desktop (default: 4 replicas).
# Peak CI Gate parallelism = 4 (infra, api, flutter, schemas).
# Kubeconfig sync does not require Admin; portproxy is best-effort.

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
    # Strip accidental EPHEMERAL=false (myoung34 treats any non-empty as --ephemeral)
    if ($_ -match '^\s*EPHEMERAL\s*=\s*(false|0|no)\s*$') {
        Write-Host "WARN: EPHEMERAL=$($Matches[1]) would enable ephemeral in myoung34 image — treating as unset."
    }
}

$kube = Join-Path $env:USERPROFILE '.kube'
if (-not (Test-Path $kube)) { New-Item -ItemType Directory -Path $kube | Out-Null }

Write-Host "Ensure Docker engine DNS (builds via docker.sock)..."
& (Join-Path $here 'Ensure-DockerDns.ps1')

$sync = Join-Path $here 'Sync-KubeForDocker.ps1'
Write-Host "Sync kubeconfig for Docker runners (auto; no manual step after TF recreate)..."
& $sync

# Normalize .env: blank out falsey EPHEMERAL so compose does not pass a truthy string
$envLines = Get-Content $envFile
$fixed = $false
$newLines = foreach ($line in $envLines) {
    if ($line -match '^\s*EPHEMERAL\s*=\s*(false|0|no)\s*$') {
        $fixed = $true
        'EPHEMERAL='
    } else {
        $line
    }
}
if ($fixed) {
    $newLines | Set-Content -Path $envFile -Encoding utf8
    Write-Host "Normalized EPHEMERAL= in .env (persistent runners)."
}

Write-Host "docker compose build + up -d --scale runner=$replicas"
docker compose build
docker compose up -d --scale "runner=$replicas" --force-recreate --remove-orphans
docker compose ps

# Drop stale offline registrations left by previous ephemeral misconfig
if (Get-Command gh -ErrorAction SilentlyContinue) {
    Write-Host "Prune offline GitHub runner registrations (best-effort)..."
    $json = gh api repos/ne-tort/prodavan/actions/runners 2>$null
    if ($json) {
        $runners = ($json | ConvertFrom-Json).runners
        foreach ($r in $runners) {
            if ($r.status -eq 'offline') {
                Write-Host "  DELETE $($r.name) id=$($r.id)"
                gh api -X DELETE "repos/ne-tort/prodavan/actions/runners/$($r.id)" 2>$null | Out-Null
            }
        }
    }
}

Write-Host @"

Check GitHub: gh api repos/ne-tort/prodavan/actions/runners --jq ".runners[]|{name,status,busy,labels:[.labels[].name]}"
Cache volume: prodavan-ci-cache → /cache (Flutter/pub/Poetry/pip)
Logs: docker compose logs -f --tail 50
  Expect: "Listening for Jobs" and NOT "Ephemeral option is enabled"
Stop:  docker compose down          # keeps cache
Wipe:  docker compose down -v       # deletes cache
"@
