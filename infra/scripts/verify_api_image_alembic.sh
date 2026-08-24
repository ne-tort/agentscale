#!/usr/bin/env bash
# Verify local API image alembic head matches repo before k3d import / rollout restart.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
API_IMAGE="${API_IMAGE:-ghcr.io/ne-tort/prodavan-api:local}"

repo_head() {
  python3 - <<'PY' "$ROOT/apps/api/alembic/versions"
import sys
from pathlib import Path
vers = sorted(Path(sys.argv[1]).glob("*.py"))
if not vers:
    raise SystemExit("no alembic versions in repo")
# revision id is prefix before first underscore in filename
print(vers[-1].name.split("_", 1)[0])
PY
}

image_head() {
  docker run --rm --entrypoint alembic "$API_IMAGE" heads 2>/dev/null | awk '{print $1; exit}'
}

verify_celery() {
  docker run --rm --entrypoint python "$API_IMAGE" -m celery --version >/dev/null 2>&1
}

if ! docker image inspect "$API_IMAGE" >/dev/null 2>&1; then
  echo "verify_api_image_alembic: image ${API_IMAGE} not found — build required"
  exit 1
fi

expected="$(repo_head)"
actual="$(image_head)"
if [[ -z "$actual" ]]; then
  echo "ERROR: could not read alembic head from ${API_IMAGE}" >&2
  exit 1
fi
if [[ "$expected" != "$actual" ]]; then
  echo "ERROR: API image alembic head=${actual}, repo head=${expected}" >&2
  echo "Run BUILD=1 bash infra/scripts/import_local_app_images_k3d.sh (WSL apt may fail — build via Docker Desktop + bridge)." >&2
  exit 1
fi
if ! verify_celery; then
  echo "ERROR: ${API_IMAGE} missing celery module (stale image)" >&2
  exit 1
fi
echo "ok — ${API_IMAGE} alembic head ${actual}, celery present"
