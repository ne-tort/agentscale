#Requires -RunAsAdministrator
# Sync WSL k3s kubeconfig + portproxy so Docker Desktop runners can Verify Dev / smoke.
# - copies ~/.kube/prodavan-dev.yaml from WSL user www to Windows
# - rewrites server to https://host.docker.internal:6443
# - portproxy 6443, 8088, 2222 -> current WSL IP

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

# Point API at Docker Desktop gateway; portproxy forwards to WSL k3s
(Get-Content $dest -Raw) `
    -replace 'server:\s*https://[^\s]+', "server: https://host.docker.internal:$portApi" `
    | Set-Content -Path $dest -Encoding utf8 -NoNewline
Write-Host "Wrote $dest (server host.docker.internal:$portApi)"

foreach ($p in @($portApi, $portHttp, $portSsh)) {
    netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=$p 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=$p connectaddress=$wslIp connectport=$p | Out-Null
}
netsh interface portproxy show v4tov4

Write-Host "OK. Docker runners use KUBECONFIG=/home/runner/.kube/prodavan-dev.yaml and PRODAVAN_CI_HOST=host.docker.internal"
Write-Host "Terraform SSH: ssh -i infra/.ssh/prodavan_tf -p 2222 www@127.0.0.1"
