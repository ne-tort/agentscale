# Argo CD (local / GitOps)

- `bootstrap/` — namespace + install pointer (real install via `infra/scripts/argocd-bootstrap.sh`)
- `apps/prodavan-dev.yaml` — Application → `infra/k3s/overlays/dev` on `main` (auto-sync + **selfHeal**; **prune=false** on local k3d so a git TLS ComparisonError cannot look like “delete the world”).

Runbook: [`docs/07-infrastructure/local-cluster-e2e.md`](../../docs/07-infrastructure/local-cluster-e2e.md).
