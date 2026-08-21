#!/usr/bin/env bash
# Pull overlay images on the Docker host and import into k3d (documented k3d workflow).
# Primary auth remains imagePullSecrets (ghcr-pull). Import is the standard k3d way to
# preload images when the node pull path is slow/unreliable (Docker Desktop / WSL).
# See: https://k3d.io/stable/usage/configfile/ and `k3d image import --help`
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
KUSTOMIZATION="${ROOT}/infra/k3s/overlays/dev/kustomization.yaml"

need_cmd docker
need_cmd k3d
need_cmd python3

wait_docker 30

if [[ -n "${GHCR_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  USER="${GHCR_USERNAME:-${GITHUB_ACTOR:-ne-tort}}"
  echo "${GHCR_TOKEN:-$GITHUB_TOKEN}" | docker login ghcr.io -u "$USER" --password-stdin
fi

mapfile -t IMAGES < <(python3 - "$KUSTOMIZATION" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
blocks = re.findall(
    r"- name:\s*(\S+)\s*(?:newName:\s*(\S+)\s*)?(?:newTag:\s*(\S+)\s*)?",
    text,
)
out = []
for name, new_name, tag in blocks:
    ref = (new_name or name).strip()
    t = (tag or "latest").strip()
    out.append(f"{ref}:{t}")
print("\n".join(out))
PY
)

if [[ ${#IMAGES[@]} -eq 0 ]]; then
  die "No images parsed from ${KUSTOMIZATION}"
fi

pulled=()
for img in "${IMAGES[@]}"; do
  echo "docker pull ${img}"
  if docker pull "$img"; then
    pulled+=("$img")
  else
    echo "WARN: pull failed ${img}" >&2
  fi
done

[[ ${#pulled[@]} -gt 0 ]] || die "no images pulled"
k3d image import "${pulled[@]}" -c "$CLUSTER"
after_k3d_image_import
echo "imported: ${pulled[*]}"
