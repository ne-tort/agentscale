#!/usr/bin/env bash
# Create/update ghcr-pull docker-registry secret (never commit the secret).
set -euo pipefail

NS="${PRODAVAN_NS:-prodavan}"
TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
USER="${GHCR_USERNAME:-${GITHUB_ACTOR:-ne-tort}}"

if [[ -z "$TOKEN" ]]; then
  echo "Set GHCR_TOKEN or GITHUB_TOKEN (read:packages)" >&2
  exit 1
fi

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username="$USER" \
  --docker-password="$TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "Secret ${NS}/ghcr-pull upserted"
