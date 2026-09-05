# Ensure Docker Desktop engine DNS is explicit (not host-default / empty).
# Runner containers get dns: in docker-compose.yml; docker build via the mounted
# socket uses the *daemon* resolvers — without this, builds see only 192.168.65.7.
#
# Safe to re-run. Writes %USERPROFILE%\.docker\daemon.json and may ask to restart Docker.

$ErrorActionPreference = 'Stop'

$wanted = @('8.8.8.8', '1.1.1.1')
$dockerDir = Join-Path $env:USERPROFILE '.docker'
$daemonPath = Join-Path $dockerDir 'daemon.json'

New-Item -ItemType Directory -Force -Path $dockerDir | Out-Null

$cfgObj = $null
if (Test-Path $daemonPath) {
    $raw = Get-Content -Raw -Path $daemonPath
    if ($raw -and $raw.Trim()) {
        $cfgObj = $raw | ConvertFrom-Json
    }
}
if ($null -eq $cfgObj) {
    $cfgObj = [pscustomobject]@{}
}

$current = @()
if ($null -ne $cfgObj.dns) {
    $current = @($cfgObj.dns | ForEach-Object { [string]$_ })
}

$same = ($current.Count -eq $wanted.Count) -and (
    $null -eq (Compare-Object -ReferenceObject $wanted -DifferenceObject $current)
)

if ($same) {
    Write-Host "Docker daemon DNS already set: $($current -join ', ')"
    return
}

# Preserve existing keys; set/replace dns
$hash = [ordered]@{}
foreach ($p in $cfgObj.PSObject.Properties) {
    if ($p.Name -eq 'dns') { continue }
    $hash[$p.Name] = $p.Value
}
$hash['dns'] = $wanted

($hash | ConvertTo-Json -Depth 8) | Set-Content -Path $daemonPath -Encoding utf8
Write-Host "Wrote $daemonPath dns=$($wanted -join ', ')"
Write-Host "Restart Docker Desktop for engine DNS to apply (quit Docker Desktop and reopen)."
Write-Host "Until restart, only runner-container DNS (compose) is guaranteed; docker build may still use the old resolver."
