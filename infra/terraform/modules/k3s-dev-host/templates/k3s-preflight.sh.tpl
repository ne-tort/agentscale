#!/usr/bin/env bash
# CNI stability before k3s starts (WSL: Docker Engine / broken Tailscale flap routes).
set -euo pipefail
if systemctl list-unit-files docker.service >/dev/null 2>&1; then
  systemctl stop docker.socket docker 2>/dev/null || true
  systemctl disable --now docker.socket docker 2>/dev/null || true
  systemctl mask docker.socket docker 2>/dev/null || true
fi
if systemctl is-active --quiet tailscaled 2>/dev/null && ! tailscale status >/dev/null 2>&1; then
  systemctl stop tailscaled 2>/dev/null || true
fi
echo prodavan-k3s-preflight ok
