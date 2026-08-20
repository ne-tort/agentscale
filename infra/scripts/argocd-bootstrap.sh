#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

echo "Waiting for argocd-server..."
kubectl -n argocd rollout status deployment/argocd-server --timeout=180s

kubectl apply -f "${ROOT}/infra/argocd/apps/prodavan-dev.yaml"
echo "Applied Application prodavan-dev. Check: kubectl -n argocd get app prodavan-dev"
