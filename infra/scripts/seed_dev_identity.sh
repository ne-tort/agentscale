#!/usr/bin/env bash
# Dev-only: mint AUTH_MODE=test JWTs and seed company + AI key + cabinet for UI.
# Verifies create project + fixture chat unless SKIP_E2E=1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
export PATH="${HOME}/.local/bin:/usr/bin:/bin:${PATH}"
NS="${PRODAVAN_NS:-prodavan}"
HTTP_PORT="${HTTP_PORT:-8088}"
HOST="${SMOKE_HOST:-prodavan.local}"
API="http://127.0.0.1:${HTTP_PORT}/api/v1"
ADMIN_EMAIL="${SEED_ADMIN_EMAIL:-admin@prodavan.local}"
COMPANY_NAME="${SEED_COMPANY_NAME:-Dev Company}"
CABINET_NAME="${SEED_CABINET_NAME:-Dev Cabinet}"

command -v kubectl >/dev/null
command -v curl >/dev/null
command -v python3 >/dev/null

mint() {
  local sub="$1" email="$2" platform_admin="$3"
  kubectl -n "$NS" exec -i deploy/prodavan-api -- python - "$sub" "$email" "$platform_admin" <<'PY'
import sys, datetime
import jwt
from prodavan.config.settings import settings
sub, email, pa = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
now = datetime.datetime.now(datetime.UTC)
payload = {
    "sub": sub,
    "email": email,
    "aud": settings.oidc_audience,
    "iat": now,
    "exp": now + datetime.timedelta(days=7),
    "platform_admin": pa,
    "roles": ["platform.admin"] if pa else [],
}
print(jwt.encode(payload, settings.auth_test_secret, algorithm="HS256"))
PY
}

api() {
  # usage: api TOKEN METHOD PATH [json_body]
  local token="$1" method="$2" path="$3" body="${4:-}"
  local out="/tmp/prodavan_seed_http.json"
  local code
  if [[ -n "$body" ]]; then
    code="$(curl -sS -o "$out" -w '%{http_code}' \
      -X "$method" \
      -H "Host: ${HOST}" \
      -H "Authorization: Bearer ${token}" \
      -H "Content-Type: application/json" \
      -d "$body" \
      "${API}${path}" || echo 000)"
  else
    code="$(curl -sS -o "$out" -w '%{http_code}' \
      -X "$method" \
      -H "Host: ${HOST}" \
      -H "Authorization: Bearer ${token}" \
      "${API}${path}" || echo 000)"
  fi
  echo "$code"
}

ADMIN_TOKEN="$(mint 'seed-platform-admin' "$ADMIN_EMAIL" 1)"
EMP_TOKEN="$(mint 'seed-company-admin' "$ADMIN_EMAIL" 0)"

echo "==> create company"
CODE="$(api "$ADMIN_TOKEN" POST /companies "{\"name\":\"${COMPANY_NAME}\",\"admin_email\":\"${ADMIN_EMAIL}\",\"admin_display_name\":\"Dev Admin\"}")"
echo "POST /companies -> ${CODE}"
cat /tmp/prodavan_seed_http.json; echo

echo "==> /me"
CODE="$(api "$EMP_TOKEN" GET /me)"
echo "GET /me -> ${CODE}"
cat /tmp/prodavan_seed_http.json; echo
COMPANY_ID="$(python3 - <<'PY'
import json
d=json.load(open("/tmp/prodavan_seed_http.json"))
ms=(d.get("employee") or {}).get("memberships") or []
print(ms[0]["company_id"] if ms else "")
PY
)"
[[ -n "$COMPANY_ID" ]] || { echo "ERROR: no company_id from /me"; exit 1; }
echo "company_id=${COMPANY_ID}"

echo "==> AI key (cursor_sdk → FixtureCursorAdapter)"
CODE="$(api "$ADMIN_TOKEN" POST /admin/ai-keys "{\"name\":\"Dev Cursor Fixture\",\"provider\":\"cursor\",\"api_kind\":\"cursor_sdk\",\"secret\":\"dev-fixture-cursor-secret\",\"company_ids\":[\"${COMPANY_ID}\"]}")"
echo "POST /admin/ai-keys -> ${CODE}"
cat /tmp/prodavan_seed_http.json; echo

echo "==> cabinet"
CODE="$(api "$EMP_TOKEN" POST /cabinets "{\"name\":\"${CABINET_NAME}\",\"company_id\":\"${COMPANY_ID}\"}")"
echo "POST /cabinets -> ${CODE}"
cat /tmp/prodavan_seed_http.json; echo
CABINET_ID="$(python3 - <<'PY'
import json
d=json.load(open("/tmp/prodavan_seed_http.json"))
print(d.get("id") or "")
PY
)"
if [[ -z "$CABINET_ID" ]]; then
  api "$EMP_TOKEN" GET /cabinets >/dev/null
  CABINET_ID="$(python3 - <<'PY'
import json
d=json.load(open("/tmp/prodavan_seed_http.json"))
items=d if isinstance(d, list) else d.get("items") or []
print(items[0]["id"] if items else "")
PY
)"
fi
[[ -n "$CABINET_ID" ]] || { echo "ERROR: no cabinet_id"; exit 1; }
echo "cabinet_id=${CABINET_ID}"

if [[ "${SKIP_E2E:-0}" != "1" ]]; then
  echo "==> e2e project + chat"
  CODE="$(api "$EMP_TOKEN" POST "/cabinets/${CABINET_ID}/projects" '{"name":"Seed Chat Project","agent_provider":"cursor"}')"
  echo "POST projects -> ${CODE}"
  cat /tmp/prodavan_seed_http.json; echo
  PROJECT_ID="$(python3 - <<'PY'
import json
d=json.load(open("/tmp/prodavan_seed_http.json"))
print(d.get("id") or "")
PY
)"
  if [[ -n "$PROJECT_ID" ]]; then
    CODE="$(api "$EMP_TOKEN" POST "/projects/${PROJECT_ID}/chat" '{"text":"hello from seed e2e"}')"
    echo "POST chat -> ${CODE}"
    cat /tmp/prodavan_seed_http.json; echo
    [[ "$CODE" == "200" ]] || { echo "ERROR: chat failed"; exit 1; }
  else
    echo "ERROR: project create failed"; exit 1
  fi
fi

echo "=== UI paste (AUTH_MODE=test) ==="
echo "Open:          http://${HOST}:${HTTP_PORT}/"
echo "API base URL:  http://${HOST}:${HTTP_PORT}/api/v1"
echo "company_id:    ${COMPANY_ID}"
echo "cabinet_id:    ${CABINET_ID}"
echo
echo "--- platform.admin JWT ---"
echo "$ADMIN_TOKEN"
echo
echo "--- company employee JWT (projects/chat) ---"
echo "$EMP_TOKEN"
echo
echo "Agent: FixtureCursorAdapter (not real Cursor SDK). MCP sandbox spawn off in cluster."
