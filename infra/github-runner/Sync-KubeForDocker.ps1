# Sync WSL k3s kubeconfig + portproxy for Docker Desktop runners.
# Kubeconfig refresh does NOT need Admin.
# Portproxy needs Admin (best-effort; Terraform also tries via powershell.exe).

$ErrorActionPreference = 'Stop'
$portApi = 6443
$portHttp = 8088
$portSsh = 2222

$raw = (wsl.exe -u www -e ip -4 -o addr show eth0 2>$null) | Out-String
if ($raw -notmatch 'inet (\d+\.\d+\.\d+\.\d+)') {
    Write-Error 'WSL (www) has no eth0 IPv4'
}
$wslIp = $Matches[1]
Write-Host "WSL IP: $wslIp"

$kubeDir = Join-Path $env:USERPROFILE '.kube'
New-Item -ItemType Directory -Force -Path $kubeDir | Out-Null
$dest = Join-Path $kubeDir 'prodavan-dev.yaml'

wsl.exe -u www -e bash -lc "cat /home/www/.kube/prodavan-dev.yaml" | Set-Content -Path $dest -Encoding utf8

$out = New-Object System.Collections.Generic.List[string]
$sawSkip = $false
foreach ($line in Get-Content $dest) {
    if ($line -match '^\s*server:\s*') {
        $out.Add(('    server: https://host.docker.internal:{0}' -f $portApi))
        continue
    }
    if ($line -match 'insecure-skip-tls-verify:') {
        $out.Add('    insecure-skip-tls-verify: true')
        $sawSkip = $true
        continue
    }
    if ($line -match '^\s*-\s*cluster:\s*$' -and -not $sawSkip) {
        $out.Add($line)
        $out.Add('    insecure-skip-tls-verify: true')
        $sawSkip = $true
        continue
    }
    $out.Add($line)
}
$out | Set-Content -Path $dest -Encoding utf8
Write-Host "Wrote $dest (server host.docker.internal:$portApi, skip-tls-verify)"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    foreach ($p in @($portApi, $portHttp, $portSsh)) {
        netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=$p 2>$null | Out-Null
        netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=$p connectaddress=$wslIp connectport=$p | Out-Null
    }
    netsh interface portproxy show v4tov4
    Write-Host "OK portproxy -> $wslIp"
} else {
    Write-Host "WARN: not Admin — skipped portproxy (Terraform apply also attempts it; elevate Start-Runners once if :6443 unreachable)"
}

Write-Host "OK. Docker runners: KUBECONFIG + PRODAVAN_CI_HOST=host.docker.internal"
