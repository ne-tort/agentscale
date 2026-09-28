# Start Prodavan Actions runner pack in Docker Desktop (4 fixed replicas).
# Peak CI Gate parallelism = 4 (infra, api, flutter, schemas).
# Registration is persisted per-runner volume so Docker Desktop / WSL restarts
# do not crash-loop on "already configured".

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'Docker Desktop not found. Install/start Docker Desktop first.'
}

$envFile = Join-Path $here '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $here '.env.example') $envFile
    Write-Host 'Created .env - set ACCESS_TOKEN (gh auth token) then re-run.'
    exit 1
}

# Normalize .env: blank out falsey EPHEMERAL so compose does not pass a truthy string
$envLines = Get-Content $envFile
$fixed = $false
$newLines = foreach ($line in $envLines) {
    if ($line -match '^\s*EPHEMERAL\s*=\s*(false|0|no)\s*$') {
        $fixed = $true
        Write-Host "WARN: EPHEMERAL=$($Matches[1]) would enable ephemeral in myoung34 image - blanking."
        'EPHEMERAL='
    } else {
        $line
    }
}
if ($fixed) {
    $newLines | Set-Content -Path $envFile -Encoding utf8
    Write-Host 'Normalized EPHEMERAL= in .env (persistent runners).'
}

$kube = Join-Path $env:USERPROFILE '.kube'
if (-not (Test-Path $kube)) { New-Item -ItemType Directory -Path $kube | Out-Null }

Write-Host 'Ensure Docker engine DNS (builds via docker.sock)...'
& (Join-Path $here 'Ensure-DockerDns.ps1')

$sync = Join-Path $here 'Sync-KubeForDocker.ps1'
Write-Host 'Sync kubeconfig for Docker runners...'
& $sync

$resetReg = $env:PRODAVAN_RUNNER_RESET_REG -eq '1'
if ($resetReg) {
    Write-Host 'PRODAVAN_RUNNER_RESET_REG=1 - removing registration volumes (will re-register)...'
    # docker writes progress to stderr; do not treat as terminating errors
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    docker compose down --remove-orphans
    foreach ($n in 1..4) {
        docker volume rm "prodavan-runner-$n-files" 2>$null | Out-Null
    }
    $ErrorActionPreference = $prevEa
}

Write-Host 'docker compose up -d (runner-1 to runner-4, persistent registration)...'
$prevEa = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$img = docker images -q prodavan-github-runner:py312
if (-not $img -or $env:PRODAVAN_RUNNER_REBUILD -eq '1') {
    Write-Host 'Building prodavan-github-runner:py312...'
    docker compose build
    if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = $prevEa; Write-Error 'docker compose build failed' }
} else {
    Write-Host 'Using existing image prodavan-github-runner:py312 (set PRODAVAN_RUNNER_REBUILD=1 to rebuild).'
}
docker compose up -d --remove-orphans
if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = $prevEa; Write-Error 'docker compose up failed' }
docker compose ps
$ErrorActionPreference = $prevEa

# Drop stale offline registrations (best-effort)
if (Get-Command gh -ErrorAction SilentlyContinue) {
    Write-Host 'Prune offline GitHub runner registrations (best-effort)...'
    $json = gh api repos/ne-tort/agentscale/actions/runners 2>$null
    if ($json) {
        $runners = ($json | ConvertFrom-Json).runners
        foreach ($r in $runners) {
            if ($r.status -eq 'offline') {
                Write-Host "  DELETE $($r.name) id=$($r.id)"
                gh api -X DELETE "repos/ne-tort/agentscale/actions/runners/$($r.id)" 2>$null | Out-Null
            }
        }
    }
}

Write-Host ''
Write-Host 'Check: gh api repos/ne-tort/agentscale/actions/runners --jq ".runners[]|{name,status,busy}"'
Write-Host 'Expect: prodavan-runners-runner-1..4 Up, logs "Listening for Jobs"'
Write-Host 'Heal after reboot: .\Ensure-RunnersHealthy.ps1 (also from tools/win-wsl-keepalive.ps1)'
Write-Host 'Stop:  docker compose down'
Write-Host 'Wipe:  docker compose down -v'
Write-Host 'Reset reg: $env:PRODAVAN_RUNNER_RESET_REG=''1''; .\Start-Runners.ps1'
