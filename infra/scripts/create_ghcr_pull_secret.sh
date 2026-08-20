#!/usr/bin/env bash
# Create/update ghcr-pull docker-registry secret (never commit the secret).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export KUBECONFIG="${KUBECONFIG:-${ROOT}/infra/.kube/prodavan-k3d.yaml}"

NS="${PRODAVAN_NS:-prodavan}"
TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
USER="${GHCR_USERNAME:-${GITHUB_ACTOR:-ne-tort}}"

if [[ -z "$TOKEN" ]]; then
  die "Set GHCR_TOKEN or GITHUB_TOKEN (read:packages)"
fi

need_cmd kubectl

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username="$USER" \
  --docker-password="$TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "Secret ${NS}/ghcr-pull upserted"
