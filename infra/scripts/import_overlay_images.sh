#!/usr/bin/env bash
# Pull overlay images on the Docker host and import into k3d.
# Use with imagePullPolicy IfNotPresent when kubelet→GHCR is flaky.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLUSTER="${K3D_CLUSTER:-prodavan-dev}"
KUSTOMIZATION="${ROOT}/infra/k3s/overlays/dev/kustomization.yaml"

need() { command -v "$1" >/dev/null || { echo "need $1" >&2; exit 1; }; }
need docke
need k3d
need python3

if [[ -n "${GHCR_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  USER="${GHCR_USERNAME:-${GITHUB_ACTOR:-ne-tort}}"
  echo "${GHCR_TOKEN:-$GITHUB_TOKEN}" | docker login ghcr.io -u "$USER" --password-stdin
fi

mapfile -t IMAGES < <(python3 - <<'PY' "$KUSTOMIZATION"
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
# images: - name: ... newName: ... newTag: ...
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
  echo "No images parsed from ${KUSTOMIZATION}" >&2
  exit 1
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

[[ ${#pulled[@]} -gt 0 ]] || exit 1
k3d image import "${pulled[@]}" -c "$CLUSTER"
echo "imported: ${pulled[*]}"
