# GitHub Actions

Спецификация: [`docs/07-infrastructure/github-actions.md`](../../docs/07-infrastructure/github-actions.md).  
E2E: [`docs/07-infrastructure/local-cluster-e2e.md`](../../docs/07-infrastructure/local-cluster-e2e.md).

| Workflow | Purpose |
|----------|---------|
| `ci-gate.yml` | PR gate: `prodavan-ops validate` + api/flutter/schemas |
| `ci-api.yml` | ruff, pytest |
| `ci-schemas.yml` | pack + profile schema |
| `ci-images.yml` | buildx → GHCR `:latest` + SHA |
| `ci-flutter.yml` | analyze + palette guards |
| `verify-dev.yml` | Argo wait + HTTP smoke (no deploy) |
| `auto-merge.yml` | squash after Gate |

Нет deploy-orchestrator workflow. Day-2 = Argo sync from `main`.
