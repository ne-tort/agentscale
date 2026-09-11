# Ensure Docker Desktop Actions runners are healthy after WSL / Docker restart.
# Safe to run often (keepalive, logon task). Recreates only when crash-looping.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Wait-DockerReady {
    param([int]$TimeoutSec = 180)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            docker info 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return $true }
        } catch { }
        Start-Sleep -Seconds 3
    }
    return $false
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Warning 'Docker not on PATH - skip runners.'
    exit 0
}

if (-not (Wait-DockerReady)) {
    Write-Warning 'Docker Desktop not ready - skip runners.'
    exit 0
}

$envFile = Join-Path $here '.env'
if (-not (Test-Path $envFile)) {
    Write-Warning ".env missing in $here - run Start-Runners.ps1 once."
    exit 0
}

$names = @(
    'prodavan-runners-runner-1',
    'prodavan-runners-runner-2',
    'prodavan-runners-runner-3',
    'prodavan-runners-runner-4'
)

$needStart = $false
$needHeal = $false
foreach ($n in $names) {
    $status = docker inspect -f '{{.State.Status}} {{.State.ExitCode}} {{.State.Restarting}}' $n 2>$null
    if (-not $status) {
        $needStart = $true
        continue
    }
    $parts = $status.Trim() -split '\s+'
    $st = $parts[0]
    $restarting = ($parts[-1] -eq 'true')
    if ($st -ne 'running' -or $restarting) {
        Write-Host ('Unhealthy: {0} ({1})' -f $n, $status)
        $needHeal = $true
    }
}

# Recent logs only (history would false-positive on old Conflict lines)
$prevEa = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$recent = docker compose logs --since 5m 2>$null | Out-String
$ErrorActionPreference = $prevEa

if (-not $needHeal -and -not $needStart) {
    $listening = ($recent -match 'Listening for Jobs')
    $sessionStuck = ($recent -match 'A session for this runner already exists')
    $corrupt = ($recent -match 'configuredSettings' -or
                $recent -match 'Cannot configure the runner because it is already configured')
    if ($corrupt -or ($sessionStuck -and -not $listening)) {
        Write-Host 'Detected registration/session problem in recent logs.'
        $needHeal = $true
    }
}

if (-not $needStart -and -not $needHeal) {
    Write-Host 'Runners OK (4 Up).'
    $sync = Join-Path $here 'Sync-KubeForDocker.ps1'
    if (Test-Path $sync) {
        try { & $sync } catch { Write-Warning $_.Exception.Message }
    }
    exit 0
}

$ErrorActionPreference = 'Continue'

if ($needHeal) {
    Write-Host 'Healing: wipe registration volumes and re-register (session conflicts need fresh IDs)...'
    $ErrorActionPreference = $prevEa
    $env:PRODAVAN_RUNNER_RESET_REG = '1'
    & (Join-Path $here 'Start-Runners.ps1')
    Remove-Item Env:PRODAVAN_RUNNER_RESET_REG -ErrorAction SilentlyContinue
    exit $LASTEXITCODE
}

$ErrorActionPreference = $prevEa
Write-Host 'Runners missing - Start-Runners.ps1...'
& (Join-Path $here 'Start-Runners.ps1')
