# Argo CD (local / GitOps)

- `bootstrap/` — namespace + install pointer (real install via `infra/scripts/argocd-bootstrap.sh`)
- `apps/prodavan-dev.yaml` — Application → `infra/k3s/overlays/dev` on `main` (auto-sync + prune for **dev only**)

Runbook: [`docs/07-infrastructure/local-cluster-e2e.md`](../../docs/07-infrastructure/local-cluster-e2e.md).
