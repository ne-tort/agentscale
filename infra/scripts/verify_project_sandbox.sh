#!/usr/bin/env bash
# Verify L07 project sandbox: sync create → object-ws container_ref + materialized workspace.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
HTTP_PORT="${HTTP_PORT:-8088}"
HOST="${SMOKE_HOST:-prodavan.local}"
API="http://127.0.0.1:${HTTP_PORT}/api/v1"

command -v kubectl >/dev/null
command -v curl >/dev/null
command -v python3 >/dev/null

mint_emp() {
  kubectl -n "$NS" exec -i deploy/prodavan-api -- python - <<'PY'
import datetime, jwt
from prodavan.config.settings import settings
now = datetime.datetime.now(datetime.UTC)
payload = {
    "sub": "sandbox-verify",
    "email": "admin@prodavan.local",
    "aud": settings.oidc_audience,
    "iat": now,
    "exp": now + datetime.timedelta(hours=1),
    "platform_admin": False,
    "roles": [],
}
print(jwt.encode(payload, settings.auth_test_secret, algorithm="HS256"))
PY
}

TOKEN="$(mint_emp)"
CODE="$(curl -sS -o /tmp/prodavan_sandbox.json -w '%{http_code}' \
  -H "Host: ${HOST}" -H "Authorization: Bearer ${TOKEN}" "${API}/cabinets")"
[[ "$CODE" == "200" ]] || { echo "FAIL: GET /cabinets -> ${CODE}"; cat /tmp/prodavan_sandbox.json; exit 1; }

CABINET_ID="$(python3 - <<'PY'
import json
d=json.load(open("/tmp/prodavan_sandbox.json"))
if isinstance(d, list):
    print(d[0]["id"] if d else "")
else:
    items=d.get("items") or []
    print(items[0]["id"] if items else "")
PY
)"
[[ -n "$CABINET_ID" ]] || { echo "FAIL: no cabinet — run seed first"; exit 1; }

SLUG="sandbox-$(date +%s)"
CODE="$(curl -sS -o /tmp/prodavan_sandbox.json -w '%{http_code}' \
  -X POST -H "Host: ${HOST}" -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Sandbox ${SLUG}\",\"agent_provider\":\"cursor\"}" \
  "${API}/cabinets/${CABINET_ID}/projects")"
[[ "$CODE" == "201" ]] || { echo "FAIL: POST project -> ${CODE}"; cat /tmp/prodavan_sandbox.json; exit 1; }

python3 - <<'PY'
import json, sys
d=json.load(open("/tmp/prodavan_sandbox.json"))
ref = d.get("container_ref") or ""
mat = d.get("materialize") or {}
if not ref.startswith("object-ws:"):
    print(f"FAIL: container_ref={ref!r}", file=sys.stderr)
    sys.exit(1)
if mat.get("status") != "materialized":
    print(f"FAIL: materialize status={mat.get('status')!r}", file=sys.stderr)
    sys.exit(1)
ws = mat.get("workspace_root") or ""
if not ws:
    print("FAIL: empty workspace_root", file=sys.stderr)
    sys.exit(1)
print(f"ok project_id={d.get('id')} container_ref={ref}")
print(f"   workspace_root={ws}")
PY

# Object store mirror on API PVC (single-node k3d pattern)
KEY="$(python3 - <<'PY'
import json
d=json.load(open("/tmp/prodavan_sandbox.json"))
print(d.get("workspace_key") or d.get("id","").replace("proj_",""))
PY
)"
kubectl -n "$NS" exec deploy/prodavan-api -- test -d "/data/storage/projects/${KEY}/workspace" \
  && echo "ok PVC mirror /data/storage/projects/${KEY}/workspace"

echo "verify_project_sandbox OK (object-ws + materialize; no k8s Pod isolator — I8)"
