#Requires -RunAsAdministrator
# Win10 WSL2: open http://localhost:8088 (auto portproxy if localhostForwarding fails).

$ErrorActionPreference = 'Continue'
$port = 8088

function Get-WslIp {
    $raw = (wsl.exe -u www -e ip -4 -o addr show eth0 2>$null) | Out-String
    if ($raw -match 'inet (\d+\.\d+\.\d+\.\d+)') { return $Matches[1] }
    return $null
}

function Set-PortProxy([string]$wslIp) {
    netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=$port 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=$port connectaddress=$wslIp connectport=$port | Out-Null
    netsh advfirewall firewall delete rule name="Prodavan WSL 8088" 2>$null | Out-Null
    netsh advfirewall firewall add rule name="Prodavan WSL 8088" dir=in action=allow protocol=TCP localport=$port | Out-Null
    Write-Host "portproxy 127.0.0.1:$port -> ${wslIp}:$port"
}

function Test-Http {
    $out = Join-Path $env:TEMP 'prodavan-http-code.txt'
    $err = Join-Path $env:TEMP 'prodavan-http-err.txt'
    Start-Process -FilePath curl.exe -ArgumentList @(
        '-sS', '-m', '8', '-o', 'NUL', '-w', '%{http_code}',
        "http://127.0.0.1:${port}/health/live"
    ) -Wait -NoNewWindow -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
    $code = Get-Content $out -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $code) { return '000' }
    return $code
}

$code = Test-Http
if ($code -ne '200') {
    $wslIp = Get-WslIp
    if (-not $wslIp) { Write-Error 'WSL not running.'; exit 1 }
    Set-PortProxy $wslIp
}

$deadline = (Get-Date).AddMinutes(4)
while ((Get-Date) -lt $deadline) {
    $code = Test-Http
    if ($code -eq '200') { break }
    $newIp = Get-WslIp
    if ($newIp) { Set-PortProxy $newIp }
    Write-Host "Waiting k3s/Traefik (HTTP $code)..."
    Start-Sleep -Seconds 5
}
if ($code -ne '200') {
    Write-Error "Still HTTP $code after 4 min. In WSL: sudo systemctl restart k3s"
    exit 1
}

Write-Host "OK -> http://localhost:${port}/"
Start-Process "http://localhost:${port}/"
