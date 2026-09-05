# Ensure Docker Desktop engine DNS stays usable for CI builds.
#
# Do NOT set daemon.json "dns" to public resolvers (8.8.8.8 / 1.1.1.1): that
# bypasses Docker's embedded resolver and breaks special names like
# host.docker.internal (migration smoke / Verify reachability from sibling
# containers). Runner containers already pin ExtServers via compose dns:.
#
# Safe to re-run. Removes a harmful dns override if present.

$ErrorActionPreference = 'Stop'

$dockerDir = Join-Path $env:USERPROFILE '.docker'
$daemonPath = Join-Path $dockerDir 'daemon.json'
New-Item -ItemType Directory -Force -Path $dockerDir | Out-Null

if (-not (Test-Path $daemonPath)) {
    Write-Host "No $daemonPath — Docker Desktop default DNS (keeps host.docker.internal)."
    return
}

$raw = Get-Content -Raw -Path $daemonPath
if (-not $raw -or -not $raw.Trim()) {
    Write-Host "Empty daemon.json — nothing to do."
    return
}

$cfgObj = $raw | ConvertFrom-Json
if ($null -eq $cfgObj.dns) {
    Write-Host "daemon.json has no dns override (OK)."
    return
}

Write-Host "Removing daemon.json dns=@($($cfgObj.dns -join ', ')) — restores embedded DNS / host.docker.internal"
$hash = [ordered]@{}
foreach ($p in $cfgObj.PSObject.Properties) {
    if ($p.Name -eq 'dns') { continue }
    $hash[$p.Name] = $p.Value
}
($hash | ConvertTo-Json -Depth 8) | Set-Content -Path $daemonPath -Encoding utf8
Write-Host "Wrote $daemonPath without dns. Restart Docker Desktop if sibling containers still fail DNS."
