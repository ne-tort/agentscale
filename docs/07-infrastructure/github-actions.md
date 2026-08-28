# GitHub Actions (as-built)

| Workflow | Trigger | Role |
|----------|---------|------|
| `ci-gate.yml` | PR / dispatch | `prodavan-ops validate` + api/flutter/schemas (L1 unit) |
| `ci-nightly.yml` | cron 02:00 UTC | L2 `pytest -m integration` |
| `ci-e2e.yml` | label `e2e` / `[e2e]` title / dispatch | L2 + L3a k8s Job + L3b live (opt-in) |
| `ci-images.yml` | main paths / dispatch | GHCR `:latest` + SHA |
| `verify-dev.yml` | after Images / infra push | `rollout` + `wait` + HTTP smoke |
| `auto-merge.yml` | Gate success | squash + dispatch Images |

Подробнее: [`e2e.md`](e2e.md).

Deploy is **Argo CD** from git. Runners: **Docker Desktop** pack ×4 ([`infra/github-runner`](../../infra/github-runner)).  
After reboot: Admin `Sync-KubeForDocker.ps1` (or `Start-Runners.ps1`). Ops: [`infra/ops`](../../infra/ops).
