#!/bin/bash
# Export k3s kubeconfig for Docker Desktop runners on Windows (via /mnt/c/...).
# Args via env: KUBECONFIG_SRC, WINDOWS_KUBECONFIG, API_PORT (default 6443)
set -eu
SRC="${KUBECONFIG_SRC:?}"
DEST="${WINDOWS_KUBECONFIG:?}"
API_PORT="${API_PORT:-6443}"
test -f "$SRC"
mkdir -p "$(dirname "$DEST")"
# Strip BOM, point API at Docker gateway, skip TLS (SAN may omit host.docker.internal on old installs).
python3 - "$SRC" "$DEST" "$API_PORT" <<'PY'
import re, sys
src, dest, port = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(src, encoding="utf-8-sig").read()
text = re.sub(r"server:\s*https://\S+", f"server: https://host.docker.internal:{port}", text, count=1)
if re.search(r"insecure-skip-tls-verify:\s*", text):
    text = re.sub(r"insecure-skip-tls-verify:\s*\S+", "insecure-skip-tls-verify: true", text, count=1)
else:
    text = re.sub(r"(?m)^(\s*-\s*cluster:\s*\n)", r"\1    insecure-skip-tls-verify: true\n", text, count=1)
open(dest, "w", encoding="utf-8").write(text)
print(f"windows-kubeconfig -> {dest}")
PY
# Best-effort portproxy Windows → this WSL eth0 (needs Admin once; ignore failure).
WSL_IP="$(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
if [ -n "$WSL_IP" ] && command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -Command "
    \$ErrorActionPreference = 'Continue'
    foreach (\$p in @(${API_PORT}, 8088, 2222)) {
      netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=\$p 2>\$null | Out-Null
      netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=\$p connectaddress=${WSL_IP} connectport=\$p 2>\$null | Out-Null
    }
    Write-Host \"portproxy -> ${WSL_IP} (best-effort)\"
  " || echo "WARN: portproxy skipped (run Start-Runners elevated once if Verify cannot reach :6443)"
fi
