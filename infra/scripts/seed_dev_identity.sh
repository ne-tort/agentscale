#!/usr/bin/env bash
# Dev-only: mint AUTH_MODE=test JWTs and seed a company for UI paste-login.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"
NS="${PRODAVAN_NS:-prodavan}"
HTTP_PORT="${HTTP_PORT:-8088}"
HOST="${SMOKE_HOST:-prodavan.local}"
BASE="http://127.0.0.1:${HTTP_PORT}"
ADMIN_EMAIL="${SEED_ADMIN_EMAIL:-admin@prodavan.local}"
COMPANY_NAME="${SEED_COMPANY_NAME:-Dev Company}"

command -v kubectl >/dev/null
command -v curl >/dev/null

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

ADMIN_TOKEN="$(mint 'seed-platform-admin' "$ADMIN_EMAIL" 1)"
EMP_TOKEN="$(mint 'seed-company-admin' "$ADMIN_EMAIL" 0)"

echo "==> create company"
CODE="$(curl -sS -o /tmp/prodavan_seed_company.json -w '%{http_code}' \
  -H "Host: ${HOST}" -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"${COMPANY_NAME}\",\"admin_email\":\"${ADMIN_EMAIL}\",\"admin_display_name\":\"Dev Admin\"}" \
  "${BASE}/api/v1/companies" || echo 000)"
echo "POST /companies -> ${CODE}"
cat /tmp/prodavan_seed_company.json 2>/dev/null || true
echo

ME="$(curl -sS -H "Host: ${HOST}" -H "Authorization: Bearer ${EMP_TOKEN}" "${BASE}/api/v1/me" || true)"
echo "==> /me (employee token)"
echo "$ME"
echo

echo "=== UI paste tokens (AUTH_MODE=test) ==="
echo "Open: http://${HOST}:${HTTP_PORT}/"
echo "API base URL: http://${HOST}:${HTTP_PORT}"
echo
echo "--- platform.admin ---"
echo "$ADMIN_TOKEN"
echo
echo "--- company employee (same email) ---"
echo "$EMP_TOKEN"
