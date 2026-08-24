#!/usr/bin/env bash
# Dev-only: wipe Postgres PVC so Alembic can apply the current migration chain.
# Needed after schema rewrite (legacy 20260808* / 2026082101 → stub_bootstrap / 20260823*).
set -euo pipefail
export PATH="${HOME}/.local/bin:/usr/bin:/bin:${PATH}"
NS="${PRODAVAN_NS:-prodavan}"
KUBECONFIG="${KUBECONFIG:-}"

echo "WARNING: deletes Postgres data in namespace ${NS}"
kubectl -n "$NS" scale deploy/prodavan-api --replicas=0 || true
kubectl -n "$NS" scale deploy/prodavan-celery-worker --replicas=0 || true
kubectl -n "$NS" scale deploy/prodavan-celery-beat --replicas=0 || true
kubectl -n "$NS" delete deploy/prodavan-postgres --ignore-not-found
kubectl -n "$NS" delete pvc prodavan-postgres-data --ignore-not-found --wait=true
echo "re-apply overlay to recreate postgres + scale api"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
bash "$ROOT/infra/scripts/apply_overlay_safe.sh"
kubectl -n "$NS" rollout status deploy/prodavan-postgres --timeout=180s
kubectl -n "$NS" scale deploy/prodavan-api --replicas=1
kubectl -n "$NS" scale deploy/prodavan-celery-worker --replicas=1
kubectl -n "$NS" scale deploy/prodavan-celery-beat --replicas=1
kubectl -n "$NS" rollout status deploy/prodavan-api --timeout=240s
echo "ok"
