# Keep Kali WSL alive while k3s runs (Win10: distro can InitTerminate after last wsl.exe exits).
# Usage: powershell -File tools/win-wsl-keepalive.ps1
# Stop:  Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'prodavan-wsl-keepalive' } | % { Stop-Process -Id $_.ProcessId -Force }

$ErrorActionPreference = 'Stop'
$distro = 'kali-linux'
# Unique marker in cmdline for stop/find
$marker = 'prodavan-wsl-keepalive'

$existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($marker) }
if ($existing) {
  Write-Host "Keepalive already running (PID $($existing.ProcessId -join ','))."
  exit 0
}

# Start detached: sleep loop holds a session so systemd/k3s are not torn down.
$arg = "-d $distro -u www -- bash -lc `"echo $marker; while true; do sleep 3600; done`""
Start-Process -FilePath 'wsl.exe' -ArgumentList $arg -WindowStyle Hidden
Start-Sleep -Seconds 2
$alive = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($marker) }
if (-not $alive) {
  Write-Error 'Failed to start keepalive wsl process'
}
Write-Host "Keepalive started PID $($alive.ProcessId -join ',')"
wsl -l -v
