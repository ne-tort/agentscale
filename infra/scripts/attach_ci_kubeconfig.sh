#!/usr/bin/env bash
# Point kubectl at the workstation k3d. Runner (Docker Desktop) and k3d (Kali Docker)
# often do not share a daemon — never create a second cluster. Talk to the API on
# host.docker.internal:6443 with tls-server-name=127.0.0.1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
if [[ -n "${KUBECONFIG_OUT:-}" ]]; then
  OUT="$KUBECONFIG_OUT"
elif [[ -d "${ROOT}/infra/k3s" && -w "${ROOT}/infra" ]]; then
  OUT="${ROOT}/infra/.kube/prodavan-k3d.yaml"
else
  OUT="${HOME}/.kube/prodavan-k3d.yaml"
fi
REWRITE_HOST="${KUBECONFIG_REWRITE_HOST:-host.docker.internal}"

export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"
bash "${SCRIPT_DIR}/install_cli_tools.sh"
need_cmd kubectl

cluster_listed() {
  command -v k3d >/dev/null 2>&1 || return 1
  k3d cluster list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$CLUSTER"
}

rewrite_loopback_server() {
  local src="$1" dest="$2"
  python3 - "$src" "$dest" "$REWRITE_HOST" <<'PY'
import sys
from pathlib import Path

src, dest, host = sys.argv[1], sys.argv[2], sys.argv[3]
text = Path(src).read_text(encoding="utf-8")
# k3d binds API on 127.0.0.1 inside the WSL that owns Docker; the GHA runner
# reaches that bind via Docker Desktop's host.docker.internal.
text = text.replace("https://127.0.0.1:6443", f"https://{host}:6443")
text = text.replace("https://0.0.0.0:6443", f"https://{host}:6443")
if "tls-server-name:" not in text and f"https://{host}:6443" in text:
    text = text.replace(
        f"    server: https://{host}:6443",
        f"    server: https://{host}:6443\n    tls-server-name: 127.0.0.1",
    )
Path(dest).parent.mkdir(parents=True, exist_ok=True)
Path(dest).write_text(text, encoding="utf-8")
PY
}

kube_ok() {
  local cfg="$1"
  KUBECONFIG="$cfg" kubectl get --raw=/readyz >/dev/null 2>&1 \
    || KUBECONFIG="$cfg" kubectl get nodes >/dev/null 2>&1
}

candidates=()
if cluster_listed; then
  mkdir -p "$(dirname "$OUT")"
  k3d kubeconfig get "$CLUSTER" >"$OUT"
  chmod 600 "$OUT" || true
  if kube_ok "$OUT"; then
    export KUBECONFIG="$OUT"
    if [[ -n "${GITHUB_ENV:-}" ]]; then
      echo "KUBECONFIG=${OUT}" >> "$GITHUB_ENV"
    fi
    if [[ -n "${GITHUB_PATH:-}" ]]; then
      echo "${HOME}/.local/bin" >> "$GITHUB_PATH"
    fi
    echo "OK kubeconfig via k3d in this Docker engine -> $OUT"
    kubectl get nodes
    exit 0
  fi
  echo "k3d listed ${CLUSTER} but 127.0.0.1 API not reachable from this netns — will rewrite"
  candidates+=("$OUT")
fi

for p in \
  /kube/prodavan-k3d.yaml \
  "${KUBECONFIG:-}" \
  "${ROOT}/infra/.kube/prodavan-k3d.yaml" \
  "${HOME}/.kube/prodavan-k3d.yaml" \
  /run/desktop/mnt/host/c/Users/*/git/Commerce/prodavan/infra/.kube/prodavan-k3d.yaml \
  /mnt/c/Users/*/git/Commerce/prodavan/infra/.kube/prodavan-k3d.yaml
do
  [[ -n "$p" ]] || continue
  # glob may stay literal if no match
  for f in $p; do
    [[ -f "$f" ]] && candidates+=("$f")
  done
done

seen=""
for src in "${candidates[@]}"; do
  [[ -f "$src" ]] || continue
  case " $seen " in
    *" $src "*) continue ;;
  esac
  seen+=" $src"
  tmp="$(mktemp)"
  rewrite_loopback_server "$src" "$tmp"
  if kube_ok "$tmp"; then
    mkdir -p "$(dirname "$OUT")"
    cp "$tmp" "$OUT"
    chmod 600 "$OUT" || true
    mkdir -p "${HOME}/.kube"
    cp "$OUT" "${HOME}/.kube/prodavan-k3d.yaml" 2>/dev/null || true
    rm -f "$tmp"
    export KUBECONFIG="$OUT"
    if [[ -n "${GITHUB_ENV:-}" ]]; then
      echo "KUBECONFIG=${OUT}" >> "$GITHUB_ENV"
    fi
    if [[ -n "${GITHUB_PATH:-}" ]]; then
      echo "${HOME}/.local/bin" >> "$GITHUB_PATH"
    fi
    echo "OK kubeconfig rewritten (${REWRITE_HOST}:6443) from ${src} -> ${OUT}"
    kubectl get nodes
    exit 0
  fi
  rm -f "$tmp"
done

echo "ERROR: cannot reach k3d API for ${CLUSTER}." >&2
echo "Runner Docker and k3d Docker are different engines (Desktop vs Kali WSL)." >&2
echo "Keep infra/.kube/prodavan-k3d.yaml current (ensure_k3d_cluster.sh from the k3d WSL)." >&2
echo "From this netns the API is https://${REWRITE_HOST}:6443 (tls-server-name 127.0.0.1)." >&2
exit 1
