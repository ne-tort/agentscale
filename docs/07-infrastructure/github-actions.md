# GitHub Actions (as-built)

| Workflow | Trigger | Role |
|----------|---------|------|
| `ci-gate.yml` | PR / dispatch | `poetry run prodavan-ops validate` + api/flutter/schemas |
| `ci-images.yml` | main paths / dispatch | GHCR `:latest` + SHA |
| `verify-dev.yml` | after Images / infra push | `wait` + `smoke` (not deploy orchestration) |
| `auto-merge.yml` | Gate success | squash + dispatch Images |
| `ci-nightly.yml` | cron | API integration |

Deploy is **Argo CD** from git. Runner: self-hosted Kali host. Ops: [`infra/ops`](../../infra/ops).
