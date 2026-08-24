# Win10 WSL2: opens http://prodavan.local:8088 from Windows (127.0.0.1).
# hosts: 127.0.0.1 prodavan.local
# Tries localhostForwarding first; falls back to auto portproxy (no manual WSL IP).

$ErrorActionPreference = 'Stop'
$port = 8088

function Test-ProdavanHttp {
    $code = '000'
    try {
        $code = (curl.exe -sS -m 8 -o NUL -w '%{http_code}' -H 'Host: prodavan.local' "http://127.0.0.1:${port}/health/live" 2>$null)
    } catch {
        $code = '000'
    }
    return $code
}

function Enable-WslPortProxy {
    $raw = (wsl.exe -e ip -4 -o addr show eth0 2>$null) | Out-String
    if ($raw -notmatch 'inet (\d+\.\d+\.\d+\.\d+)') {
        Write-Error 'WSL not running. Start WSL first.'
    }
    $wslIp = $Matches[1]
    netsh interface portproxy reset | Out-Null
    netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=$port connectaddress=$wslIp connectport=$port | Out-Null
    Write-Host "portproxy 127.0.0.1:${port} -> ${wslIp}:${port}"
}

$code = Test-ProdavanHttp
if ($code -ne '200') {
    Enable-WslPortProxy
}

$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    $code = Test-ProdavanHttp
    if ($code -eq '200') { break }
    Write-Host "Waiting for k3s/Traefik (HTTP $code)..."
    Start-Sleep -Seconds 10
}
if ($code -ne '200') {
    Write-Error "HTTP check failed after 3 min (last: $code). In WSL: systemctl status k3s; prodavan-ops smoke."
}
Write-Host "OK -> http://prodavan.local:${port}/"
Start-Process "http://prodavan.local:${port}/"
