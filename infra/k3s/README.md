# k3s manifests (GitOps)

Workloads live under `base/` + `overlays/dev`.  
**Day-2 apply:** Argo CD Application `agentscale-dev` (see `infra/argocd/`).  
Do **not** `kubectl apply -k` overlays by hand after bootstrap.

Bootstrap once: [`docs/07-infrastructure/runbook.md`](../../docs/07-infrastructure/runbook.md).

## Secrets

`ghcr-pull` via Sealed Secrets — [`overlays/dev/SECRETS.md`](overlays/dev/SECRETS.md).

## Brokers

| Component | Persistence |
|-----------|-------------|
| Postgres / Redis / MinIO / API | PVC (local-path) |
| Redpanda | volumeClaimTemplates; no `--mode dev-container` (fsync bypass) |

Verify: `poetry run prodavan-ops wait && poetry run prodavan-ops smoke`.
